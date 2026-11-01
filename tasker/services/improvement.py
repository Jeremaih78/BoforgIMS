"""Scoped improvement commands; Tasker task and capture services remain authoritative."""
from copy import copy
from datetime import date, timedelta
from decimal import Decimal
import hashlib
import json
import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Max, OuterRef, Q, Subquery,Value,IntegerField
from django.db.models.functions import TruncDate,Coalesce
from django.utils import timezone

from tasker.improvement_models import (CarryDecision, Experiment, ExperimentResult, ImprovementEvent,
    SOPTemplate, SOPVersion, SOPExecution, WeeklyReview)
from tasker.models import Task, TaskChecklistItem, DailyPlan
from tasker.organization import can_manage_team, require_task_access, visible_teams_for
from tasker.services.coordination import scoped_tasks_for, visible_requests_for
from tasker.services.workbench import apply_work_actions, work_version
from tasker.services.work_dates import work_date


class ImprovementConflict(ValidationError):pass


def actor(user,permission='tasker.view_task'):
    current=get_user_model().objects.filter(pk=getattr(user,'pk',None),is_active=True).first()
    if not getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) or not current or not current.has_perm('tasker.view_task') or not current.has_perm(permission):
        raise PermissionDenied
    return current


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,ensure_ascii=False).encode()).hexdigest()


def key(value):
    try:return uuid.UUID(str(value))
    except (TypeError,ValueError,AttributeError):raise ValidationError('Reload this form before submitting.')


def expect(record,value):
    if str(record.revision)!=str(value):
        raise ImprovementConflict('This record changed. Your draft is retained; reload and compare before saving.')


def replay(user,client_key,action,payload):
    token=key(client_key)
    previous=ImprovementEvent.objects.filter(actor=user,client_key=token).first()
    if previous and (previous.action!=action or previous.request_digest!=digest(payload)):
        raise ImprovementConflict('This request was already used for different work.')
    return previous


def audit(user,client_key,action,record,payload,details=None):
    return ImprovementEvent.objects.create(actor=user,client_key=key(client_key),action=action,
        entity_type=type(record).__name__,entity_id=str(record.pk),request_digest=digest(payload),details=details or {})


def scoped_records(model,user):
    user=actor(user,'tasker.view_'+model._meta.model_name)
    return model.objects.filter(Q(team__isnull=True,owner=user)|Q(team__in=visible_teams_for(user)))


def templates_for(user):return scoped_records(SOPTemplate,user)
def experiments_for(user):return scoped_records(Experiment,user)


def can_edit(user,record):
    return record.owner_id==user.pk if not record.team_id else can_manage_team(user,record.team_id)


class TaskDraft:
    definition_submitted=True
    def __init__(self,task,version=None):
        self.task=task
        self.cleaned_data={'work_version':version,'accept_definition':False}
    def save(self,commit=False):
        if not self.task.slug:self.task.slug='workflow-'+uuid.uuid4().hex
        self.task.full_clean()
        return self.task
    def save_m2m(self):pass


def save_work(task,user,version=None):
    from .tasks import save_task
    return save_task(form=TaskDraft(task,version),user=user,preserve_notes=True)


@transaction.atomic
def publish_sop(*,user,pk=None,expected_revision='new',client_key,title,instructions,definition_of_done,steps,team=None):
    user=actor(user,'tasker.add_soptemplate' if pk is None else 'tasker.change_soptemplate')
    payload=dict(pk=pk,title=title,instructions=instructions,definition_of_done=definition_of_done,steps=steps,team_id=getattr(team,'pk',team))
    # Actor serialization covers two first publications sharing a client key.
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    old=replay(user,client_key,'sop.publish',payload)
    if old:
        return templates_for(user).get(pk=old.entity_id)
    if pk:
        item=templates_for(user).select_for_update(of=('self',)).filter(pk=pk).first()
        if not item or not can_edit(user,item):raise PermissionDenied
        expect(item,expected_revision)
        if item.team_id!=getattr(team,'pk',team):raise ValidationError('A template retains its original sharing scope.')
        if item.archived:raise ValidationError('Restore this SOP before publishing another version.')
    else:
        if expected_revision!='new':raise ImprovementConflict('Reload the new SOP form.')
        if team and not can_manage_team(user,team):raise PermissionDenied
        item=SOPTemplate(owner=user,team=team,created_by=user)
    if team:
        from tasker.services.goals import _lock_scope
        _lock_scope(team.company_id,team.pk)
        if not can_manage_team(actor(user),team):raise PermissionDenied
    item.title=title.strip();item.updated_by=user;item.latest_version+=1;item.revision=uuid.uuid4()
    item.full_clean();item.save()
    version=SOPVersion.objects.create(template=item,number=item.latest_version,title=item.title,
        instructions=instructions.strip(),definition_of_done=definition_of_done.strip(),
        steps=[step.strip() for step in steps],published_by=user)
    audit(user,client_key,'sop.publish',item,payload,{'version_id':version.pk,'number':version.number})
    return item


def instantiate(*,user,version,owner,client_key,due_date=None):
    """Caller holds Template -> scope locks; reuse Task creation/checklist rules."""
    user=actor(user,'tasker.add_task')
    template=version.template
    if not templates_for(user).filter(pk=template.pk).exists():raise PermissionDenied
    if not template.team_id and owner.pk!=template.owner_id:raise PermissionDenied
    old=SOPExecution.objects.filter(actor=user,client_key=key(client_key)).select_related('task').first()
    if old:
        if old.version_id!=version.pk or old.task.owner_id!=owner.pk or old.task.due_date!=due_date:
            raise ImprovementConflict('This execution request already belongs to other work.')
        if not scoped_tasks_for(user).filter(pk=old.task_id).exists():raise PermissionDenied
        return old.task
    task=Task(owner=owner,company=template.team.company if template.team_id else None,team=template.team,
        title=version.title,description=version.instructions,definition_of_done=version.definition_of_done,
        due_date=due_date,priority='MEDIUM')
    task=save_work(task,user)
    from .tasks import add_checklist_item
    for step in version.steps:add_checklist_item(task=task,user=user,title=step)
    SOPExecution.objects.create(version=version,task=task,actor=user,client_key=key(client_key))
    return task


@transaction.atomic
def use_sop(*,user,pk,expected_revision,client_key,due_date=None):
    user=actor(user,'tasker.add_task')
    item=templates_for(user).select_for_update(of=('self',)).filter(pk=pk).first()
    if not item:raise PermissionDenied
    prior=SOPExecution.objects.filter(actor=user,client_key=key(client_key)).select_related('version').first()
    if prior:
        if prior.version.template_id!=item.pk:raise ImprovementConflict('This execution request belongs to another SOP.')
        return instantiate(user=user,version=prior.version,owner=user,client_key=client_key,due_date=due_date)
    expect(item,expected_revision)
    if item.archived:raise ValidationError('This SOP is archived.')
    version=item.versions.get(number=item.latest_version)
    return instantiate(user=user,version=version,owner=user,client_key=client_key,due_date=due_date)


@transaction.atomic
def archive_sop(*,user,pk,expected_revision,client_key,archived):
    user=actor(user,'tasker.change_soptemplate')
    item=templates_for(user).select_for_update(of=('self',)).filter(pk=pk).first()
    if not item or not can_edit(user,item):raise PermissionDenied
    if item.team_id:
        from .goals import _lock_scope
        _lock_scope(item.team.company_id,item.team_id)
        user=actor(user,'tasker.change_soptemplate')
        if not templates_for(user).filter(pk=item.pk).exists() or not can_edit(user,item):raise PermissionDenied
    payload=dict(pk=pk,archived=archived)
    if replay(user,client_key,'sop.archive',payload):return item
    expect(item,expected_revision)
    item.archived=bool(archived);item.revision=uuid.uuid4();item.updated_by=user;item.save()
    audit(user,client_key,'sop.archive',item,payload)
    return item


def carry_candidates(user):
    user=actor(user);today=work_date(user)
    from .work_dates import work_zone
    latest=CarryDecision.objects.filter(task_id=OuterRef('pk')).order_by('-created_at').annotate(
        day=TruncDate('created_at',tzinfo=work_zone(user))).values('day')[:1]
    history=DailyPlan.objects.filter(owner=user,plan_date__lt=today,
        plan_date__gt=Coalesce(OuterRef('last_decision'),Value(date.min))).filter(
        Q(planned_tasks=OuterRef('pk'))|Q(review__carry_over_tasks=OuterRef('pk'),review__is_deleted=False)
    ).values('owner_id').annotate(total=Count('pk',distinct=True)).values('total')[:1]
    return scoped_tasks_for(user).filter(owner=user,archived=False,status__in=('PENDING','IN_PROGRESS','BLOCKED')).filter(
        Q(due_date__lte=today)|Q(due_date__isnull=True)).annotate(last_decision=Subquery(latest)).annotate(
        carry_count=Coalesce(Subquery(history),Value(0),output_field=IntegerField())
    ).filter(carry_count__gte=2).order_by('-carry_count','due_date','pk')


def models_f(value):
    from django.db.models import F
    return F(value)


@transaction.atomic
def decide_carry(*,user,task_id,expected_version,client_key,action,reason,title='',definition_of_done='',due_date=None,assignee=None):
    user=actor(user,'tasker.change_task')
    task=Task.all_objects.select_for_update().get(pk=task_id)
    require_task_access(user,task)
    payload=dict(task_id=task_id,action=action,reason=reason,title=title,definition_of_done=definition_of_done,
        due_date=due_date,assignee=getattr(assignee,'pk',None))
    old=replay(user,client_key,'carry.decide',payload)
    if old:return CarryDecision.objects.get(pk=old.details['decision_id'])
    if work_version(task)!=expected_version:raise ImprovementConflict('This task changed. Reload before deciding.')
    if task.archived or task.status in ('COMPLETED','CANCELLED'):raise ValidationError('Review active work before making a carry-over decision.')
    if not reason.strip() or len(reason)>1000:raise ValidationError('Give a shared reason of up to 1,000 characters.')
    result=None
    if action=='DELEGATE':
        if assignee is None:raise ValidationError('Choose a qualified employee.')
        apply_work_actions(user=user,task_ids=[task.pk],action='assign',value=assignee.pk,versions={task.pk:expected_version})
    elif action=='RESCHEDULE':
        if type(due_date) is not date or due_date<work_date(user):raise ValidationError('Choose today or a later date.')
        apply_work_actions(user=user,task_ids=[task.pk],action='due_date',value=due_date,versions={task.pk:expected_version})
    elif action=='CLARIFY':
        if not definition_of_done.strip():raise ValidationError('Define the outcome before continuing.')
        candidate=copy(task);candidate.definition_of_done=definition_of_done.strip()
        save_work(candidate,user,expected_version)
    elif action=='SPLIT':
        if not title.strip() or not definition_of_done.strip():raise ValidationError('Name the small next step and its acceptance condition.')
        result=save_work(Task(title=title.strip(),definition_of_done=definition_of_done.strip(),owner=task.owner,
            company=task.company,team=task.team,parent_task=task,priority=task.priority,due_date=due_date),user)
    elif action=='STOP':
        candidate=copy(task);candidate.status='CANCELLED';save_work(candidate,user,expected_version)
    else:raise ValidationError('Choose clarify, split, delegate, reschedule or stop.')
    decision=CarryDecision.objects.create(task=task,actor=user,action=action,reason=reason.strip(),
        prior_version=expected_version,result_task=result)
    from .tasks import _activity
    from tasker.models import TaskActivity
    _activity(task,user,TaskActivity.EventType.UPDATED,'Carry-over decision: '+action.lower(),{'reason':reason.strip()})
    audit(user,client_key,'carry.decide',task,payload,{'decision_id':decision.pk})
    return decision
