"""Explicit End day declaration and private corrections over Stage 8 report facts."""
import uuid
from django.conf import settings
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone

from tasker.improvement_models import DayClosure,DayRevision
from tasker.models import DailyPlan,DailyReview,TaskTimeEntry
from .improvement import actor,audit,digest,expect,replay
from .planner import plan_version
from .work_dates import work_date


REVIEW_FIELDS=('accomplishments','energy_level','most_productive_time','biggest_distraction','lessons_learned','tomorrow_focus')


def review_version(review):
    if not review or not review.pk:return 'new'
    return digest([review.pk,*[getattr(review,f) for f in REVIEW_FIELDS],
        list(review.carry_over_tasks.order_by('pk').values_list('pk',flat=True))])


def review_payload(form,plan):
    return dict(plan_id=plan.pk,**{name:form.cleaned_data.get(name) for name in REVIEW_FIELDS},
        carry_over_tasks=sorted(t.pk for t in form.cleaned_data['carry_over_tasks']),
        correction_reason=form.cleaned_data.get('correction_reason',''))


def before_review_save(*,user,plan,form):
    """Called under the planner's Actor -> Plan lock before reflection writes."""
    payload=review_payload(form,plan)
    old=replay(user,form.cleaned_data.get('review_client_key'),'review.save',payload)
    if old:return DailyReview.objects.get(pk=old.entity_id,plan__owner=user)
    current=DailyReview.objects.filter(plan=plan).first()
    if form.cleaned_data.get('review_version')!=review_version(current):
        raise ValidationError('This review changed. Your draft is retained; reload and compare before saving.')
    closure=DayClosure.objects.select_for_update().filter(plan=plan,owner=user).first()
    if closure and closure.closed:
        expect(closure,form.cleaned_data.get('close_revision'))
        if not form.cleaned_data.get('correction_reason','').strip():
            raise ValidationError('Give a private correction reason before changing a closed day’s review.')
    return None


def after_review_save(*,user,plan,review,form):
    payload=review_payload(form,plan)
    closure=DayClosure.objects.select_for_update().filter(plan=plan,owner=user).first()
    if closure and closure.closed:
        closure.revision=uuid.uuid4();closure.save(update_fields=['revision','updated_at'])
        DayRevision.objects.create(closure=closure,action='CORRECTION',review_digest=review_version(review),
            private_reason=form.cleaned_data['correction_reason'].strip())
        if getattr(settings,'TASKER_REPORTS_ENABLED',False):
            from .reporting import close_day
            close_day(user=user,plan=plan,review=review)
    audit(user,form.cleaned_data['review_client_key'],'review.save',review,payload)


@transaction.atomic
def change_day(*,user,plan_id,expected_plan_version,expected_review_version,expected_revision,
               client_key,closed,reason='',stop_focus=False):
    from django.contrib.auth import get_user_model
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    user=actor(user,'tasker.view_dailyreview')
    plan=DailyPlan.objects.select_for_update().filter(pk=plan_id,owner=user,plan_date=work_date(user)).first()
    if not plan:raise PermissionDenied
    review=DailyReview.objects.filter(plan=plan).first()
    if not review:raise ValidationError('Save your private review before closing the day.')
    payload=dict(plan_id=plan_id,closed=closed,reason=reason,stop_focus=stop_focus,
        plan_version=expected_plan_version,review_version=expected_review_version,revision=expected_revision)
    old=replay(user,client_key,'day.close' if closed else 'day.reopen',payload)
    if old:return DayClosure.objects.get(pk=old.entity_id,owner=user)
    if expected_plan_version!=plan_version(plan) or expected_review_version!=review_version(review):
        raise ValidationError('Your plan or review changed. Reload and review the current facts before closing.')
    closure=DayClosure.objects.select_for_update().filter(plan=plan,owner=user).first()
    if str(expected_revision)!=(str(closure.revision) if closure else 'new'):
        raise ValidationError('This day’s declaration changed. Reload before acting.')
    if closure and closure.closed==bool(closed):
        audit(user,client_key,'day.close' if closed else 'day.reopen',closure,payload)
        return closure
    if not closure and not closed:raise ValidationError('Close this day before reopening it.')
    if not closed and (not reason.strip() or len(reason)>1000):raise ValidationError('Give a private reason for reopening this day.')
    running=TaskTimeEntry.objects.filter(user=user,ended_at__isnull=True).exists()
    if closed and running and not stop_focus:raise ValidationError('Pause or stop your Focus, or explicitly choose to stop it when closing.')
    if closed and running:
        from .focus import stop_own_focus
        stop_own_focus(user=user)
    closure=closure or DayClosure(plan=plan,owner=user)
    closure.closed=bool(closed);closure.revision=uuid.uuid4()
    if closed:closure.closed_at=timezone.now()
    closure.save()
    DayRevision.objects.create(closure=closure,action='CLOSED' if closed else 'REOPENED',
        private_reason=reason.strip(),review_digest=review_version(review))
    if getattr(settings,'TASKER_REPORTS_ENABLED',False):
        from .reporting import close_day
        close_day(user=user,plan=plan,review=review)
    audit(user,client_key,'day.close' if closed else 'day.reopen',closure,payload)
    return closure


def require_open_focus(user):
    if getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) and DayClosure.objects.filter(
            owner=user,plan__plan_date=work_date(user),closed=True).exists():
        raise ValueError('Your day is closed. Reopen it before starting another Focus session.')
