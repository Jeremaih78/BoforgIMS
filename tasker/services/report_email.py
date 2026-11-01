"""Guarded SMTP over the existing report ledger; durable uncertainty prevents resend."""
from datetime import timedelta
from email.utils import parseaddr
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.mail import EmailMessage,get_connection
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone

from accounting.models import Company
from tasker.models import Team,OrganizationAccess
from tasker.notification_models import NotificationDelivery
from . import reporting
from .jobs import JobCancelled,JobDeferred,assert_current_job_lease,fence_current_job_effects


def job_kind(report):
    policy=reporting._policy(report.team)
    if getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) and policy and policy.email_mode=='SMTP':
        return 'reports.smtp'
    return 'reports.deliver'


class SMTPReportEmailAdapter:
    def send(self,*,recipient,subject,body,idempotency_key):
        sender=settings.DEFAULT_FROM_EMAIL
        address=parseaddr(sender)[1];validate_email(address);validate_email(recipient.email)
        timeout=max(1,min(15,int(getattr(settings,'EMAIL_TIMEOUT',10) or 10)))
        connection=get_connection(backend='django.core.mail.backends.smtp.EmailBackend',fail_silently=False,timeout=timeout)
        message=EmailMessage(subject=subject,body=body,from_email=sender,to=[recipient.email],connection=connection,
            headers={'Message-ID':f'<tasker-{reporting._digest(idempotency_key)[:40]}@{address.split("@",1)[1]}>'})
        if message.send(fail_silently=False)!=1:raise RuntimeError('SMTP acceptance is unconfirmed')
        return {'state':'PROVIDER_ACCEPTED'}


def _locked_delivery(pk):
    reference=NotificationDelivery.objects.filter(pk=pk,channel='EMAIL',report_snapshot__isnull=False).values(
        'recipient_id','report_snapshot__team_id','report_snapshot__team__company_id').first()
    if not reference:return None
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk=reference['recipient_id']))
    list(Company.objects.select_for_update().filter(pk=reference['report_snapshot__team__company_id']))
    list(Team.all_objects.select_for_update().filter(pk=reference['report_snapshot__team_id']))
    return NotificationDelivery.objects.select_for_update(of=('self',)).select_related(
        'recipient','report_snapshot__team__company').filter(pk=pk,channel='EMAIL').first()


def _check(delivery):
    report,user=delivery.report_snapshot,delivery.recipient
    if not user.is_active or not user.has_perm('tasker.view_task') or report.team.is_deleted or not report.team.company.is_active or not OrganizationAccess.objects.filter(
            user=user,company_id=report.team.company_id,is_active=True,is_deleted=False,can_receive_reports=True).exists():
        return 'recipient_access_changed'
    latest=reporting.ManagerReportSnapshot.objects.filter(team=report.team,report_date=report.report_date).order_by('-version').first()
    if not latest or latest.pk!=report.pk:return 'report_superseded'
    try:
        reporting._assert_sources_current(report)
        if reporting._content_digest(reporting._team_facts(report.team,report.report_date),reporting._policy_revision(report.team))!=report.source_digest:
            return 'report_sources_changed'
    except (reporting.ReportConflict,ValidationError):return 'report_sources_changed'
    return ''


def _policy_ready(delivery):
    policy=reporting._policy(delivery.report_snapshot.team)
    if not getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) or not getattr(settings,'TASKER_REPORTS_ENABLED',False) or not getattr(
            settings,'TASKER_REPORT_EMAIL_LIVE_ENABLED',False) or not policy or not policy.configured or policy.email_mode!='SMTP' or not policy.email_enabled:
        return False
    if not getattr(settings,'EMAIL_HOST','') or not getattr(settings,'DEFAULT_FROM_EMAIL',''):return False
    if settings.EMAIL_USE_TLS and settings.EMAIL_USE_SSL or not 1<=settings.EMAIL_PORT<=65535:return False
    try:
        address=parseaddr(settings.DEFAULT_FROM_EMAIL)[1];validate_email(address);validate_email(delivery.recipient.email)
        if address.rsplit('@',1)[-1].casefold() in ('localhost','localhost.localdomain'):return False
    except ValidationError:return False
    return True


def handle_smtp_delivery(payload,job=None,adapter=None):
    if type(payload) is not dict or set(payload)!={'delivery_id'} or type(payload['delivery_id']) is not int or payload['delivery_id']<1:
        return JobCancelled('report_delivery_invalid')
    # Phase one commits UNKNOWN *before* the network call. A crash or stale
    # lease can lose a send, but cannot silently retry a possibly accepted mail.
    with transaction.atomic():
        delivery=_locked_delivery(payload['delivery_id'])
        if not delivery:return JobCancelled('report_delivery_missing')
        if delivery.state in ('UNKNOWN','FAILED','CANCELLED','DELIVERED','PROVIDER_ACCEPTED','TEST_ACCEPTED'):return {'noop':1}
        error=_check(delivery)
        if error:
            delivery.state='CANCELLED';delivery.error_code=error;delivery.save(update_fields=['state','error_code'])
            return JobCancelled(error)
        if not _policy_ready(delivery):
            delivery.state='HELD';delivery.error_code='smtp_activation_pending';delivery.save(update_fields=['state','error_code'])
            return JobDeferred(run_after=timezone.now()+timedelta(hours=1),reason='smtp_activation_pending')
        policy=reporting._policy(delivery.report_snapshot.team);now=timezone.now()
        from .capacity import aware_local
        from .notifications import delivery_window
        local=now.astimezone(ZoneInfo(policy.timezone_name))
        if delivery.report_snapshot.report_date.weekday() not in policy.weekdays:
            return JobDeferred(run_after=now+timedelta(days=1),reason='outside_reporting_days')
        if policy.consolidation=='AT_CUTOFF' and local.date()==delivery.report_snapshot.report_date:
            cutoff=aware_local(local.date(),policy.cutoff_time,policy.timezone_name)
            if now<cutoff:return JobDeferred(run_after=cutoff,reason='awaiting_report_cutoff')
        instant,reason=delivery_window(recipient=delivery.recipient,now=now)
        if instant>now:return JobDeferred(run_after=instant,reason=reason)
        if job is not None:assert_current_job_lease();fence_current_job_effects()
        delivery.state='UNKNOWN';delivery.error_code='smtp_dispatch_unconfirmed';delivery.attempt_count+=1
        delivery.attempted_at=now;delivery.next_attempt_at=None
        delivery.save(update_fields=['state','error_code','attempt_count','attempted_at','next_attempt_at'])
    # Keep recipient/scope changes serialized during the bounded send. The
    # durable intent above survives a transport error or transaction rollback.
    with transaction.atomic():
        delivery=_locked_delivery(payload['delivery_id'])
        if not delivery or delivery.state!='UNKNOWN' or delivery.error_code!='smtp_dispatch_unconfirmed':return {'noop':1}
        if job is not None:assert_current_job_lease()
        error=_check(delivery)
        if error or not _policy_ready(delivery):
            delivery.state='CANCELLED';delivery.error_code=error or 'smtp_activation_pending'
            delivery.save(update_fields=['state','error_code']);return JobCancelled(delivery.error_code)
        try:
            message=reporting.render_report_email(delivery.report_snapshot)
            receipt=(adapter or SMTPReportEmailAdapter()).send(recipient=delivery.recipient,**message,idempotency_key=delivery.dedupe_key)
            if not isinstance(receipt,dict) or receipt.get('state')!='PROVIDER_ACCEPTED':raise RuntimeError('Unconfirmed receipt')
        except Exception:
            delivery.error_code='report_delivery_uncertain';delivery.save(update_fields=['error_code'])
            return JobCancelled('report_delivery_uncertain')
        delivery.state='PROVIDER_ACCEPTED';delivery.error_code='';delivery.accepted_at=timezone.now()
        delivery.save(update_fields=['state','error_code','accepted_at'])
        return {'accepted':1}
