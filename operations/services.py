import hashlib
import json
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounting.models import Company
from inventory.replenishment_policies import current_actor
from tasker.models import Team
from .models import Responsibility, ResponsibilityEvent
from .policies import can_manage, capable, eligible, visible
from .registry import CHECKLISTS, facts, spec_for


def token(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('Reload the form before submitting.')


def get_row(user, pk):
    row = visible(user).select_related('company','team','assigned_to').filter(pk=pk).first()
    if row is None:
        raise PermissionDenied
    row.current = facts(row)
    return row


def _receipt(row, actor, client_key, action, fingerprint):
    old = ResponsibilityEvent.objects.filter(actor=actor,client_key=client_key).first()
    if old:
        if old.responsibility_id != row.pk or old.action != action or old.request_digest != fingerprint:
            raise ValidationError('This request was already used for different work.')
        return old


def _audit(row, actor, client_key, action, fingerprint):
    ResponsibilityEvent.objects.create(responsibility=row,actor=actor,client_key=client_key,
        action=action,revision=row.revision,request_digest=fingerprint,source_version=facts(row)['version'],
        details=dict(owner_id=row.assigned_to_id,team_id=row.team_id,company_id=row.company_id,
            priority=row.priority,enabled=row.enabled,next_review_on=str(row.next_review_on) if row.next_review_on else None,
            sla_due_on=str(row.sla_due_on) if row.sla_due_on else None,contact_interval_days=row.contact_interval_days,
            checklist=row.checklist,quality_accepted_by_id=row.quality_accepted_by_id))


def _fingerprint(data):
    return hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()


@transaction.atomic
def configure(*, user, kind, source_id, company_id, team_id, assigned_to_id, enabled,
              next_review_on, contact_interval_days, sla_due_on, expected_revision, client_key, priority='HIGH'):
    spec = spec_for(kind)
    actor_ids = sorted({user.pk} | ({assigned_to_id} if assigned_to_id else set()))
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk__in=actor_ids).order_by('pk'))
    actor = current_actor(user)
    source = spec.cls().objects.select_for_update().filter(pk=source_id).first()
    if source is None or not can_manage(actor,kind,company_id,team_id):
        raise PermissionDenied
    if getattr(source,'company_id',company_id) != company_id:
        raise ValidationError('Choose the source record’s current company. Company-less finance records need source correction first.')
    row = Responsibility.objects.select_for_update().filter(kind=kind,source_id=source_id).first()
    Company.objects.select_for_update().get(pk=company_id,is_active=True)
    if not can_manage(current_actor(user),kind,company_id,team_id):
        raise PermissionDenied
    if row and not visible(actor).filter(pk=row.pk).exists():
        raise PermissionDenied
    if row and not can_manage(actor,kind,row.company_id,row.team_id):
        raise PermissionDenied
    if team_id:
        team = Team.objects.select_for_update().filter(pk=team_id,company_id=company_id,is_deleted=False).first()
        if team is None:
            raise ValidationError('Choose a live team in this company.')
    if assigned_to_id and not eligible(actor,kind,company_id,team_id).filter(pk=assigned_to_id).exists():
        raise ValidationError('Choose an active employee with source permissions and matching company/team membership.')
    key = token(client_key)
    payload = dict(kind=kind,source_id=source_id,company_id=company_id,team_id=team_id,
        assigned_to_id=assigned_to_id,enabled=enabled,next_review_on=next_review_on,
        contact_interval_days=contact_interval_days,sla_due_on=sla_due_on,priority=priority)
    fingerprint = _fingerprint(payload)
    if row and _receipt(row,actor,key,'configure',fingerprint):
        return row
    if str(expected_revision) != (str(row.revision) if row else 'new'):
        raise ValidationError('Responsibility changed. Reload before assigning work.')
    if kind == 'quotation_work' and enabled and not (assigned_to_id and next_review_on and contact_interval_days):
        raise ValidationError('Set an explicit quotation owner, next contact date and contact interval before enabling follow-ups.')
    if kind == 'service_work' and enabled and not sla_due_on:
        raise ValidationError('Set the repair SLA due date before enabling this responsibility.')
    row = row or Responsibility(kind=kind,source_id=source_id,company_id=company_id,
        **{spec.field:source})
    for name in ('team_id','assigned_to_id','enabled','next_review_on','contact_interval_days','sla_due_on','priority'):
        setattr(row,name,payload[name])
    # A changed owner or scope cannot inherit another employee's quality sign-off.
    row.quality_version = ''
    row.quality_accepted_by = None
    row.quality_accepted_at = None
    row.save()
    _audit(row,actor,key,'configure',fingerprint)
    return row


@transaction.atomic
def review(*, user, pk, expected_revision, expected_source_version, client_key,
           next_review_on, notes, checklist, accept_quality=False, contact_done=False):
    from django.utils import timezone
    reference = Responsibility.objects.filter(pk=pk).first()
    if not reference:
        raise PermissionDenied
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk))
    actor = current_actor(user)
    spec = spec_for(reference.kind)
    list(spec.cls().objects.select_for_update().filter(pk=reference.source_id))
    row = Responsibility.objects.select_for_update().get(pk=pk)
    Company.objects.select_for_update().get(pk=row.company_id)
    if not visible(actor).filter(pk=pk).exists() or not capable(actor,row.kind,work=True) or (
            row.assigned_to_id != actor.pk and not can_manage(actor,row.kind,row.company_id,row.team_id)):
        raise PermissionDenied
    fingerprint = _fingerprint(dict(next_review_on=next_review_on,notes=notes,checklist=checklist,
        accept_quality=accept_quality,contact_done=contact_done))
    key = token(client_key)
    if _receipt(row,actor,key,'review',fingerprint):
        return row
    current = facts(row)
    if str(row.revision) != str(expected_revision) or current['version'] != expected_source_version:
        raise ValidationError('The source or responsibility changed. Your draft is retained; reload before saving.')
    if current['status'] not in ('OPEN','IN_PROGRESS'):
        raise ValidationError('This source no longer has an active review.')
    allowed_checks = CHECKLISTS.get(row.kind,())
    if set(checklist) != set(allowed_checks) or any(type(v) is not bool for v in checklist.values()):
        raise ValidationError('Use the current checklist.')
    if accept_quality:
        source = getattr(row,spec.field)
        if row.kind != 'service_work' or not can_manage(actor,row.kind,row.company_id,row.team_id):
            raise PermissionDenied
        if source.status != 'CLOSED' or not source.inspection.strip() or not source.resolution.strip() or not all(checklist.values()):
            raise ValidationError('Close the repair at source, record inspection and resolution, and check every handoff item before accepting quality.')
        row.quality_version = current['native_version']
        row.quality_accepted_by, row.quality_accepted_at = actor, timezone.now()
    if contact_done:
        if row.kind != 'quotation_work' or not row.contact_interval_days:
            raise ValidationError('A follow-up requires the quotation’s explicit contact policy.')
        next_review_on = timezone.localdate() + timedelta(days=row.contact_interval_days)
    row.next_review_on, row.notes, row.checklist = next_review_on, notes.strip(), checklist
    row.save()
    _audit(row,actor,key,'review',fingerprint)
    return row
