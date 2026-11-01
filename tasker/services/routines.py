"""Calendar-local recurrence with immutable occurrences and leased, bounded generation."""
import calendar
from datetime import date, datetime, timedelta, timezone as datetime_timezone
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounting.models import Company
from tasker.improvement_models import RecurringWorkflow, RoutineException, RoutineOccurrence, SOPTemplate
from tasker.models import Task, Team
from tasker.organization import managed_teams_for, company_read_ids, validate_task_scope
from .capacity import aware_local
from .improvement import actor, audit, expect, instantiate, key, replay, templates_for


def routines_for(user):
    user=actor(user,'tasker.view_recurringworkflow')
    return RecurringWorkflow.objects.filter(template__in=templates_for(user)).filter(
        Q(owner=user)|Q(created_by=user)|Q(team__in=managed_teams_for(user))|Q(company_id__in=company_read_ids(user)))


def can_configure(user,row):
    return bool(user.has_perm('tasker.change_recurringworkflow') and (
        row.owner_id==user.pk or row.team_id and can_manage(user,row.team_id)))


def can_manage(user,team):return managed_teams_for(user).filter(pk=team).exists()


def _management_scope(row,user):
    """Serialize configuration with live team/grant changes without requiring a valid assignee to pause."""
    if row.company_id:
        Company.objects.select_for_update().get(pk=row.company_id)
        if row.team_id:Team.all_objects.select_for_update().get(pk=row.team_id)
    user=actor(user,'tasker.change_recurringworkflow')
    if not routines_for(user).filter(pk=row.pk).exists() or not can_configure(user,row):raise PermissionDenied
    return user


def occurs_on(row,day):
    if day<row.start_on or row.end_on and day>row.end_on:return False
    if row.frequency=='DAILY':return (day-row.start_on).days%row.interval==0
    if row.frequency=='WEEKLY':
        start=row.start_on-timedelta(days=row.start_on.weekday())
        return ((day-start).days//7)%row.interval==0 and day.weekday() in row.weekdays
    months=(day.year-row.start_on.year)*12+day.month-row.start_on.month
    return months%row.interval==0 and day.day==min(row.start_on.day,calendar.monthrange(day.year,day.month)[1])


def next_date(row,after):
    # Maximum supported interval (366 months) fits within this bounded horizon.
    if row.frequency=='MONTHLY':
        months=max(0,(after.year-row.start_on.year)*12+after.month-row.start_on.month)
        months=((months+row.interval-1)//row.interval)*row.interval
        for offset in range(2):
            index=row.start_on.year*12+row.start_on.month-1+months+offset*row.interval
            year,month=divmod(index,12);month+=1
            if year>9999:return None
            day=date(year,month,min(row.start_on.day,calendar.monthrange(year,month)[1]))
            if day>=after and occurs_on(row,day):return day
        return None
    for number in range(366*7+8):
        try:day=after+timedelta(days=number)
        except OverflowError:return None
        if row.end_on and day>row.end_on:return None
        if occurs_on(row,day):return day
    return None


def scheduled_at(row,day):
    # The existing capacity policy rejects ambiguous/non-existent DST clocks.
    # Both receive an explicit skipped receipt rather than a guessed time.
    return aware_local(day,row.local_time,row.timezone_name)


def _scope(row,user):
    if row.team_id!=row.template.team_id or (row.team_id and row.company_id!=row.template.team.company_id):raise PermissionDenied
    if not templates_for(user).filter(pk=row.template_id,archived=False).exists():raise PermissionDenied
    owner=get_user_model().objects.filter(pk=row.owner_id,is_active=True).first()
    if not owner or not templates_for(owner).filter(pk=row.template_id).exists():raise PermissionDenied
    probe=Task(owner=owner,company_id=row.company_id,team_id=row.team_id,title='Routine scope check')
    validate_task_scope(user,probe,lock=True)
    return owner


@transaction.atomic
def save_routine(*,user,template,client_key,expected_revision='new',pk=None,rebase=False,**data):
    user=actor(user,'tasker.add_recurringworkflow' if pk is None else 'tasker.change_recurringworkflow')
    template=templates_for(user).select_for_update(of=('self',)).get(pk=template.pk)
    row=routines_for(user).select_for_update(of=('self',)).filter(pk=pk).first() if pk else None
    if pk and (not row or not can_configure(user,row)):raise PermissionDenied
    payload=dict(pk=pk,template=template.pk,rebase=rebase,**data)
    old=replay(user,client_key,'routine.save',payload)
    if old:return routines_for(user).get(pk=old.entity_id)
    if row:
        expect(row,expected_revision)
        if row.template_id!=template.pk:raise ValidationError('A routine retains its source SOP. Create a new routine for another SOP.')
    else:
        if expected_revision!='new':raise ValidationError('Reload the new routine form.')
        row=RecurringWorkflow(template=template,company=template.team.company if template.team_id else None,
            team=template.team,created_by=user)
    schedule_fields=('frequency','interval','weekdays','timezone_name','local_time','start_on','end_on')
    changed=bool(pk and any(getattr(row,f)!=data[f] for f in schedule_fields))
    if changed and not rebase:raise ValidationError('Confirm rebasing the future calendar before changing its rule. Existing executions stay unchanged.')
    old_next=row.next_on
    for name in (*schedule_fields,'owner_id','missed_policy','overlap_policy','paused'):
        setattr(row,name,data[name])
    row.full_clean(exclude=['next_on'])
    _scope(row,user)
    if not pk or changed:
        local_now=timezone.now().astimezone(ZoneInfo(row.timezone_name))
        if row.start_on<local_now.date() and not pk:raise ValidationError('Start new routines today or later; historical work is not backfilled.')
        first=next_date(row,max(row.start_on,local_now.date()))
        if first:
            try:
                if scheduled_at(row,first)<=timezone.now():first=next_date(row,first+timedelta(days=1))
            except ValidationError:
                pass # A calendar exception receives an explicit skipped receipt.
        row.next_on=first
    row.updated_by=user;row.needs_review=False;row.revision=uuid.uuid4();row.save()
    audit(user,client_key,'routine.save',row,payload,{'old_next_on':str(old_next),'next_on':str(row.next_on),'rebased':changed,
        'owner_id':row.owner_id,'team_id':row.team_id,'rule':{f:str(getattr(row,f)) for f in schedule_fields}})
    return row


@transaction.atomic
def set_paused(*,user,pk,expected_revision,client_key,paused):
    user=actor(user,'tasker.change_recurringworkflow')
    reference=routines_for(user).get(pk=pk)
    SOPTemplate.objects.select_for_update().get(pk=reference.template_id)
    row=routines_for(user).select_for_update(of=('self',)).get(pk=pk)
    user=_management_scope(row,user)
    payload=dict(pk=pk,paused=paused)
    if replay(user,client_key,'routine.pause',payload):return row
    expect(row,expected_revision)
    if not paused:_scope(row,user)
    row.paused=bool(paused);row.needs_review=False;row.revision=uuid.uuid4();row.updated_by=user;row.save()
    audit(user,client_key,'routine.pause',row,payload)
    return row


@transaction.atomic
def skip_date(*,user,pk,expected_revision,client_key,scheduled_on,reason):
    user=actor(user,'tasker.change_recurringworkflow')
    reference=routines_for(user).get(pk=pk)
    SOPTemplate.objects.select_for_update().get(pk=reference.template_id)
    row=routines_for(user).select_for_update(of=('self',)).get(pk=pk)
    user=_management_scope(row,user)
    payload=dict(pk=pk,scheduled_on=scheduled_on,reason=reason)
    old=replay(user,client_key,'routine.skip',payload)
    if old:return RoutineException.objects.get(pk=old.details['exception_id'])
    expect(row,expected_revision)
    if not occurs_on(row,scheduled_on) or scheduled_on<timezone.now().astimezone(ZoneInfo(row.timezone_name)).date():
        raise ValidationError('Choose a current or future date on this routine’s calendar.')
    if row.occurrences.filter(scheduled_on=scheduled_on).exists():raise ValidationError('This occurrence already has retained execution evidence.')
    if not reason.strip():raise ValidationError('Record a shared reason for skipping this occurrence.')
    exception=RoutineException.objects.create(routine=row,scheduled_on=scheduled_on,reason=reason.strip(),created_by=user)
    audit(user,client_key,'routine.skip',row,payload,{'exception_id':exception.pk})
    return exception


@transaction.atomic
def generate(*,routine_id,now=None):
    if not getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) or not getattr(settings,'TASKER_RECURRENCE_ENABLED',False):return {'disabled':1}
    now=now or timezone.now()
    reference=RecurringWorkflow.objects.filter(pk=routine_id).first()
    if not reference:return {'missing':1}
    template=SOPTemplate.objects.select_for_update().get(pk=reference.template_id)
    row=RecurringWorkflow.objects.select_for_update().get(pk=routine_id)
    if row.paused or not row.next_on:return {'paused':1}
    from .pilot_access import feature_access
    if not feature_access(row.owner,'recurrence'):return {'cohort_held':1}
    user=get_user_model().objects.filter(pk=row.updated_by_id,is_active=True).first()
    try:
        user=actor(user,'tasker.add_task')
        if not user.has_perm('tasker.change_recurringworkflow'):raise PermissionDenied
        owner=_scope(row,user)
        if template.archived:raise PermissionDenied
    except (PermissionDenied,ValidationError):
        row.paused=True;row.needs_review=True;row.revision=uuid.uuid4();row.save()
        from tasker.improvement_models import ImprovementEvent
        ImprovementEvent.objects.create(entity_type='RecurringWorkflow',entity_id=str(row.pk),action='routine.scope_review')
        return {'held':1}
    local_day=now.astimezone(ZoneInfo(row.timezone_name)).date()
    last_day=min(local_day,row.end_on) if row.end_on else local_day
    if last_day==local_day:
        try:
            if scheduled_at(row,last_day)>now:last_day-=timedelta(days=1)
        except ValidationError:pass
    latest=last_date(row,last_day)
    created=skipped=0
    target=latest if row.missed_policy=='LATEST' else next_date(row,local_day) if row.missed_policy=='SKIP' else row.next_on
    if target and row.next_on<target and (target-row.next_on).days>20:
        RoutineOccurrence.objects.create(routine=row,scheduled_on=row.next_on,rule_revision=row.revision,
            rule_snapshot={'skip_through':str(target-timedelta(days=1)),'policy':row.missed_policy},
            status='SKIPPED',reason_code='missed_range_skipped')
        row.next_on=target;skipped+=1
    for _ in range(20):
        day=row.next_on
        if day is None or day>local_day:break
        try:instant=scheduled_at(row,day)
        except ValidationError:instant=None
        if instant and instant>now:break
        existing=row.occurrences.filter(scheduled_on=day).first()
        if existing:
            row.next_on=next_date(row,day+timedelta(days=1));continue
        reason=''
        if instant is None:reason='calendar_exception'
        elif row.exceptions.filter(scheduled_on=day).exists():reason='explicit_exception'
        elif row.missed_policy=='SKIP' and day<local_day:reason='missed_day_skipped'
        elif row.missed_policy=='LATEST' and latest and day<latest:reason='newer_occurrence_due'
        elif row.overlap_policy=='SKIP_OPEN' and row.occurrences.filter(task__is_deleted=False,
                task__archived=False,task__status__in=('PENDING','IN_PROGRESS','BLOCKED')).exists():reason='prior_work_open'
        version=template.versions.filter(published_at__lte=instant or now).order_by('-number').first()
        if not version and not reason:reason='no_effective_sop_version'
        task=None
        if not reason:
            task=instantiate(user=user,version=version,owner=owner,
                client_key=uuid.uuid5(uuid.NAMESPACE_URL,f'tasker-routine:{row.pk}:{day}'),due_date=day)
            created+=1
        else:skipped+=1
        RoutineOccurrence.objects.create(routine=row,scheduled_on=day,scheduled_at=instant,rule_revision=row.revision,
            rule_snapshot={f:str(getattr(row,f)) for f in ('owner_id','company_id','team_id','frequency','interval','weekdays','start_on','end_on','timezone_name','local_time','missed_policy','overlap_policy')},
            version=version,task=task,status='CREATED' if task else 'SKIPPED',reason_code=reason)
        row.next_on=next_date(row,day+timedelta(days=1))
    row.save(update_fields=['next_on','updated_at'])
    return {'created':created,'skipped':skipped}


def last_date(row,on):
    if on<row.start_on:return None
    if row.frequency=='MONTHLY':
        months=((on.year-row.start_on.year)*12+on.month-row.start_on.month)//row.interval*row.interval
        for offset in range(2):
            index=row.start_on.year*12+row.start_on.month-1+months-offset*row.interval
            year,month=divmod(index,12);month+=1
            if year<1:return None
            day=date(year,month,min(row.start_on.day,calendar.monthrange(year,month)[1]))
            if day<=on and occurs_on(row,day):return day
        return None
    for i in range(row.interval*7+8):
        day=on-timedelta(days=i)
        if day<row.start_on:return None
        if occurs_on(row,day):return day
    return None


def handle_generate(payload,job=None):
    from .jobs import assert_current_job_lease,fence_current_job_effects
    if type(payload) is not dict or set(payload)!={'routine_id'} or type(payload['routine_id']) is not int or payload['routine_id']<1:
        raise ValidationError('A routine job contains only a positive routine reference.')
    with transaction.atomic():
        assert_current_job_lease();result=generate(routine_id=payload['routine_id']);fence_current_job_effects()
        if result.get('cohort_held'):
            from .jobs import JobDeferred
            return JobDeferred(run_after=timezone.now()+timedelta(hours=1),reason='pilot_not_released')
        return result


def reconcile_routines(*,limit=100):
    if not getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) or not getattr(settings,'TASKER_RECURRENCE_ENABLED',False):return {'queued':0}
    from .jobs import enqueue_job
    from tasker.job_models import OutboxJob
    # Fair cursor avoids paused, future or failing workflows starving later IDs.
    from tasker.business_models import BusinessSyncCursor
    with transaction.atomic():
        cursor,_=BusinessSyncCursor.objects.get_or_create(key='routines')
        cursor=BusinessSyncCursor.objects.select_for_update().get(pk=cursor.pk)
        rows=list(RecurringWorkflow.objects.filter(paused=False,next_on__isnull=False,pk__gt=cursor.collection_after).order_by('pk')[:limit])
        if not rows:cursor.collection_after=0
        count=0
        for row in rows:
            cursor.collection_after=row.pk
            if row.next_on>timezone.now().astimezone(ZoneInfo(row.timezone_name)).date():continue
            try:
                if scheduled_at(row,row.next_on)>timezone.now():continue
            except ValidationError:pass
            base=f'routine:{row.pk}:{row.revision}:{row.next_on}'
            old=OutboxJob.objects.filter(dedupe_key__startswith=base).order_by('-created_at','-pk').first()
            if old and old.state in ('FAILED','CANCELLED'):continue
            if old and old.state=='SUCCEEDED':base+=':repair:'+str(old.pk)
            enqueue_job(kind='routine.generate',dedupe_key=base,payload={'routine_id':row.pk},owner=row.owner)
            count+=1
        cursor.save()
        return {'queued':count,'scanned':len(rows)}
