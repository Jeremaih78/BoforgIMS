from datetime import date,timedelta
from decimal import Decimal
from itertools import combinations
import re
import uuid

from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from django.urls import reverse
from django.db.models import F,Q

from tasker.improvement_models import Experiment,ExperimentResult,WeeklyReview
from tasker.models import Task,Team
from tasker.organization import can_manage_team,visible_teams_for
from .improvement import actor,audit,digest,expect,replay,scoped_records,experiments_for
from .captures import visible_captures_for
from .coordination import scoped_tasks_for,visible_requests_for
from .work_dates import work_date
from .workbench import work_version


def review_scope(user,team=None):
    user=actor(user)
    if team and not visible_teams_for(user).filter(pk=getattr(team,'pk',team)).exists():raise PermissionDenied
    return user


def idea_review(*,user,team=None,limit=40):
    user=review_scope(user,team)
    rows=visible_captures_for(user).filter(kind='IDEA',triage_state='INBOX')
    rows=rows.filter(visibility='TEAM',shared_team_id=getattr(team,'pk',team)) if team else rows.filter(owner=user)
    total=rows.count();items=list(rows.order_by('-pinned','-updated_at','pk')[:limit])
    def tokens(item):return set(re.findall(r'\w{3,}',(item.title+' '+item.content[:1000]).casefold()))-{
        'the','and','for','with','this','that','from','have','idea','should','would'}
    terms={item.pk:tokens(item) for item in items};suggestions=[];groups={}
    for item in items:
        for tag in item.tags:groups.setdefault(tag,[]).append(item)
    for left,right in combinations(items,2):
        shared=terms[left.pk]&terms[right.pk];union=terms[left.pk]|terms[right.pk]
        score=len(shared)/len(union) if union else 0
        if len(shared)>=2 and score>=.4:suggestions.append({'left':left,'right':right,'score':round(score*100)})
    return {'items':items,'total':total,'omitted':max(0,total-len(items)),
        'groups':[(tag,values) for tag,values in sorted(groups.items()) if len(values)>1],
        'suggestions':sorted(suggestions,key=lambda row:-row['score'])[:20]}


def weekly_facts(*,user,week_start=None,team=None):
    user=review_scope(user,team)
    day=week_start or work_date(user)-timedelta(days=work_date(user).weekday())
    if type(day) is not date or day.weekday()!=0 or day>work_date(user):raise ValidationError('Choose a Monday from this week or earlier.')
    tasks=scoped_tasks_for(user).filter(archived=False)
    requests=visible_requests_for(user).filter(status__in=('OPEN','ACKNOWLEDGED'),task__archived=False)
    if team:
        tasks=tasks.filter(team_id=team.pk);requests=requests.filter(task__team_id=team.pk)
    else:
        tasks=tasks.filter(owner=user);requests=requests.filter(task__owner=user)
    blocked=list(tasks.filter(status='BLOCKED').order_by('due_date','pk')[:40])
    handoffs=list(requests.select_related('task','resolver').order_by('expected_response_at','pk')[:40])
    approvals,approval_omitted=approval_agenda(user=user,team=team)
    from .goals import visible_outcomes_for
    from .business_outcomes import filter_valid_business_outcomes
    outputs=visible_outcomes_for(user).filter(status='ACCEPTED',occurred_on__gte=day,occurred_on__lt=day+timedelta(days=7))
    outputs=outputs.filter(team=team) if team else outputs.filter(employee=user)
    outputs=filter_valid_business_outcomes(outputs)
    experiments=experiments_for(user).filter(review_on__lte=day+timedelta(days=6)) if user.has_perm('tasker.view_experiment') else Experiment.objects.none()
    experiments=experiments.filter(team=team) if team else experiments.filter(owner=user)
    return {'week_start':day,'week_end':day+timedelta(days=6),'blocked':blocked,'approvals':approvals,'handoffs':handoffs,
        'experiments':list(experiments.order_by('review_on','pk')[:20]),'outputs':list(outputs.order_by('-occurred_on','pk')[:40]),'completed':tasks.filter(status='COMPLETED',
            completed_at__date__gte=day,completed_at__date__lt=day+timedelta(days=7)).count(),
        'omitted':max(0,tasks.filter(status='BLOCKED').count()-len(blocked))+max(0,requests.count()-len(handoffs))+max(0,outputs.count()-40)+approval_omitted+max(0,experiments.count()-20)}


def approval_agenda(*,user,team=None):
    """Current goal acceptance and native stocktake approvals, never invented request kinds."""
    from .goals import visible_outcomes_for
    pending=visible_outcomes_for(user).filter(status='DRAFT',key_result__goal__status='ACTIVE').filter(
        Q(task__isnull=True)|Q(task__in=scoped_tasks_for(user),task__owner_id=F('employee_id'),
            task__company_id=F('company_id'),task__team_id=F('team_id'),task__archived=False))
    pending=pending.filter(team=team) if team else pending.filter(employee=user)
    rows=[{'id':str(row.pk),'kind':'outcome','title':row.key_result.title,'status':'Awaiting outcome acceptance',
        'url':reverse('ims:tasker:goal_detail',args=[row.key_result.goal_id])} for row in pending.order_by('occurred_on','pk')[:20]]
    omitted=max(0,pending.count()-20)
    from .business_links import visible_business_links_for,present_business_link,enabled
    from .business_outcomes import team_for
    if enabled('stocktake_work'):
        native=visible_business_links_for(user,state='active',source_type='stocktake_work',mine=not bool(team))
        if team:native=native.filter(company_id=team.company_id)
        omitted+=max(0,native.count()-40)
        for link in native[:40]:
            try:item=present_business_link(user=user,link=link)
            except PermissionDenied:continue
            if team and team_for(item)!=team.pk:continue
            if item.source_status!='REVIEW':continue
            rows.append({'id':str(item.pk),'kind':'stocktake','title':item.title,'status':'Awaiting source approval','url':item.get_absolute_url()})
    return rows,omitted


@transaction.atomic
def record_weekly_review(*,user,week_start,team,note,client_key):
    user=actor(user,'tasker.add_weeklyreview')
    if team:
        from .goals import _lock_scope
        _lock_scope(team.company_id,team.pk)
        if not can_manage_team(actor(user),team):raise PermissionDenied
    payload=dict(week_start=week_start,team_id=getattr(team,'pk',None),note=note)
    old=replay(user,client_key,'weekly.review',payload)
    if old:return scoped_records(WeeklyReview,user).get(pk=old.entity_id)
    if len(note)>5000:raise ValidationError('Keep this review within 5,000 characters.')
    facts=weekly_facts(user=user,week_start=week_start,team=team)
    row=WeeklyReview.objects.create(owner=user,team=team,week_start=week_start,note=note.strip(),references={
        'tasks':[t.pk for t in facts['blocked']],'requests':[r.pk for r in facts['handoffs']],
        'approvals':[{'kind':r['kind'],'id':r['id']} for r in facts['approvals']],
        'outcomes':[o.pk for o in facts['outputs']]})
    audit(user,client_key,'weekly.review',row,payload)
    return row


@transaction.atomic
def promote_experiment(*,user,capture_id,version,client_key,owner,team,title,expected_benefit,unit,baseline,target,direction,review_on):
    user=actor(user,'tasker.add_experiment')
    # Reuse the capture owner's version/serialization boundary and canonical
    # promotion. No private capture body is copied into experiment work.
    from tasker.services.captures import _actor,_owned,promote_capture
    user=_actor(user);capture=_owned(user,capture_id)
    payload=dict(capture_id=capture_id,owner=owner.pk,team_id=getattr(team,'pk',None),title=title,
        expected_benefit=expected_benefit,unit=unit,baseline=baseline,target=target,direction=direction,review_on=review_on)
    old=replay(user,client_key,'experiment.promote',payload)
    if old:return experiments_for(user).get(pk=old.entity_id)
    existing=Experiment.objects.filter(source_capture=capture).first()
    if existing:raise ValidationError('This idea already has an experiment. Open the retained experiment instead.')
    if capture.version!=version:raise ValidationError('The idea changed. Reload before promoting it.')
    if capture.kind!='IDEA' or capture.triage_state!='INBOX' or capture.is_deleted:raise ValidationError('Review an active idea from your Inbox.')
    if capture.promoted_task_id or capture.promoted_initiative_id:raise ValidationError('This idea already has promoted work. Review that destination instead of duplicating it.')
    if team and (capture.visibility!='TEAM' or capture.shared_team_id!=team.pk):
        raise ValidationError('Share this idea deliberately with the selected team before creating team work.')
    if owner.pk!=user.pk and (not team or not can_manage_team(user,team) or not user.has_perm('tasker.assign_task')):raise PermissionDenied
    if not unit.strip() or not expected_benefit.strip() or not title.strip() or review_on<work_date(user):
        raise ValidationError('Set a title, expected benefit, measurement unit and current/future review date.')
    rule=Experiment(source_capture=capture,owner=owner,team=team,company=team.company if team else None,title=title.strip(),
        expected_benefit=expected_benefit.strip(),unit=unit.strip(),baseline=Decimal(str(baseline)),target=Decimal(str(target)),
        direction=direction,review_on=review_on)
    rule.clean()
    done=f'Record the observed {unit} against baseline {baseline} and target {target} ({direction.lower()} is better); review evidence by {review_on}.'
    task=promote_capture(user=user,pk=capture.pk,version=version,title=title,team=team,due_date=review_on,
        work_description='Expected benefit: '+expected_benefit,definition_of_done=done)
    if owner.pk!=user.pk:
        from .workbench import apply_work_actions
        task=apply_work_actions(user=user,task_ids=[task.pk],action='assign',value=owner.pk,versions={task.pk:work_version(task)})[0]
    rule.task=task;rule.created_by=user;rule.updated_by=user;rule.full_clean();rule.save()
    audit(user,client_key,'experiment.promote',rule,payload,{'capture_id':capture.pk,'task_id':task.pk})
    return rule


def result_current(result):
    task=result.experiment.task
    if result.outcome_id:
        from .business_outcomes import filter_valid_business_outcomes
        from tasker.models import OutcomeMeasurement
        if not filter_valid_business_outcomes(OutcomeMeasurement.objects.all()).filter(pk=result.outcome_id,status='ACCEPTED',task=task,
                employee=result.experiment.owner,team=result.experiment.team,company=result.experiment.company,value=result.value).exists():return False
    return bool(task and not task.is_deleted and not task.archived and task.status=='COMPLETED'
        and task.owner_id==result.experiment.owner_id and task.team_id==result.experiment.team_id
        and task.company_id==result.experiment.company_id and work_version(task)==result.task_version)


@transaction.atomic
def record_result(*,user,pk,expected_revision,client_key,value,evidence,outcome=None):
    user=actor(user,'tasker.add_experimentresult')
    ref=experiments_for(user).get(pk=pk)
    Task.all_objects.select_for_update().get(pk=ref.task_id)
    item=experiments_for(user).select_for_update(of=('self',)).get(pk=pk)
    if item.owner_id!=user.pk and not item.team_id or item.owner_id!=user.pk and not can_manage_team(user,item.team_id):raise PermissionDenied
    payload=dict(pk=pk,value=value,evidence=evidence,outcome_id=getattr(outcome,'pk',None))
    old=replay(user,client_key,'experiment.observe',payload)
    if old:return ExperimentResult.objects.get(pk=old.details['result_id'])
    expect(item,expected_revision)
    require_scope(item,user)
    if item.task.status!='COMPLETED':raise ValidationError('Complete the experiment task against its definition before recording an observation.')
    if outcome:
        from .goals import visible_outcomes_for
        from .business_outcomes import filter_valid_business_outcomes
        if not filter_valid_business_outcomes(visible_outcomes_for(user)).filter(pk=outcome.pk,status='ACCEPTED',task=item.task,
                employee=item.owner,company=item.company,team=item.team).exists() or outcome.key_result.unit.casefold()!=item.unit.casefold() or Decimal(str(value))!=outcome.value:
            raise ValidationError('Choose matching, currently accepted Tasker outcome evidence for this experiment task and unit.')
    result=ExperimentResult.objects.create(experiment=item,value=value,evidence=evidence.strip(),
        task_version=work_version(item.task),recorded_by=user,outcome=outcome)
    audit(user,client_key,'experiment.observe',item,payload,{'result_id':result.pk})
    return result


def require_scope(item,user):
    from tasker.organization import require_task_access
    require_task_access(user,item.task,change=False)
    if (item.task.owner_id,item.task.company_id,item.task.team_id)!=(item.owner_id,item.company_id,item.team_id):
        raise ValidationError('The task assignment changed. Review experiment ownership before accepting results.')


@transaction.atomic
def sync_experiment_owner(*,user,pk,expected_revision,expected_task_version,client_key):
    """Confirm an existing Task delegation; never transfer or broaden source sharing."""
    user=actor(user,'tasker.change_experiment')
    ref=experiments_for(user).get(pk=pk)
    task=Task.all_objects.select_for_update().get(pk=ref.task_id)
    item=experiments_for(user).select_for_update(of=('self',)).get(pk=pk)
    if not item.team_id or not can_manage_team(user,item.team_id):raise PermissionDenied
    from tasker.organization import require_task_access,validate_task_scope
    require_task_access(user,task,change=False);validate_task_scope(user,task,previous=task,lock=True)
    payload=dict(pk=pk,task_version=expected_task_version)
    if replay(user,client_key,'experiment.owner',payload):return item
    expect(item,expected_revision)
    if work_version(task)!=expected_task_version:raise ValidationError('The task changed again. Review its current assignment.')
    if task.archived or task.status=='CANCELLED' or task.team_id!=item.team_id or task.company_id!=item.company_id:
        raise ValidationError('This experiment retains its original team audience. Review active work in the same scope.')
    old_owner=item.owner_id;item.owner_id=task.owner_id;item.revision=uuid.uuid4();item.updated_by=user;item.full_clean();item.save()
    audit(user,client_key,'experiment.owner',item,payload,{'old_owner_id':old_owner,'owner_id':item.owner_id})
    return item


@transaction.atomic
def accept_result(*,user,pk,result_id,expected_revision,client_key):
    user=actor(user,'tasker.change_experimentresult')
    ref=experiments_for(user).get(pk=pk)
    Task.all_objects.select_for_update().get(pk=ref.task_id)
    item=experiments_for(user).select_for_update(of=('self',)).get(pk=pk)
    if item.team_id and not can_manage_team(user,item.team_id) or not item.team_id and item.owner_id!=user.pk:raise PermissionDenied
    payload=dict(pk=pk,result_id=result_id)
    old=replay(user,client_key,'experiment.accept',payload)
    if old:return ExperimentResult.objects.get(pk=old.details['result_id'])
    expect(item,expected_revision);require_scope(item,user)
    source=ExperimentResult.objects.select_related('experiment__task').get(pk=result_id,experiment=item)
    if not result_current(source):raise ValidationError('The observation no longer matches the current completed task. Record a fresh observation.')
    if source.accepted_at:return source
    accepted=ExperimentResult.objects.filter(origin=source).first()
    if not accepted:
        accepted=ExperimentResult.objects.create(origin=source,experiment=item,value=source.value,evidence=source.evidence,
            task_version=source.task_version,recorded_by=source.recorded_by,accepted_by=user,accepted_at=timezone.now(),outcome=source.outcome)
    audit(user,client_key,'experiment.accept',item,payload,{'result_id':accepted.pk})
    return accepted
