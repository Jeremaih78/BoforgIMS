"""Scoped, versioned pilot commands. Existing baseline and goal evidence remain canonical."""
import hashlib,json,uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from zoneinfo import ZoneInfo
from tasker.pilot_models import Pilot,PilotMember,PilotObservation,PilotEvent,PilotReleaseEvidence,PilotValidation,DEFINITIONS,METRICS
from tasker.models import BaselineStudy,BaselineObservation
from tasker.organization import can_manage_team,can_manage_company
from . import baseline


def digest(value):return hashlib.sha256(json.dumps(value,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
def actor(user,permission='tasker.view_pilot'):
    user=get_user_model().objects.filter(pk=getattr(user,'pk',None),is_active=True).first()
    if not getattr(settings,'TASKER_PILOT_ENABLED',False) or not user or not user.has_perm(permission):raise PermissionDenied
    return user
def manages(user,pilot):return can_manage_team(user,pilot.team_id) if pilot.team_id else can_manage_company(user,pilot.company_id)
def visible_pilots(user):
    user=actor(user)
    studies=baseline.visible_studies_for(user)
    manager_ids=[s.pk for s in studies if baseline._can_manage(user,s)]
    return Pilot.objects.filter(study__in=studies).filter(Q(study_id__in=manager_ids)|Q(pk__in=PilotMember.objects.filter(employee=user).values('pilot_id'))).select_related('study__team','study__company','owner')
def observations_for(user,pilot):
    user=actor(user,'tasker.view_pilotobservation')
    if not visible_pilots(user).filter(pk=pilot.pk).exists():raise PermissionDenied
    rows=PilotObservation.objects.filter(pilot=pilot).select_related('employee','baseline_observation','accepted_by')
    rows=rows.filter(Q(phase='FOLLOWUP')|Q(baseline_observation__in=baseline.visible_observations_for(user)))
    return rows if manages(user,pilot) else rows.filter(employee=user)
def _lock(user,pk,permission):
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    ref=Pilot.objects.select_related('study').get(pk=pk)
    baseline._lock_scope(ref.study)
    BaselineStudy.objects.select_for_update().get(pk=ref.study_id)
    user=actor(user,permission)
    pilot=visible_pilots(user).select_for_update(of=('self',)).get(pk=pk)
    return user,pilot
def expect(item,revision):
    if str(item.revision)!=str(revision):raise ValidationError('This record changed. Your draft is retained; reload and compare before saving.')
def event(user,pilot,key,action,payload,details=None):
    return PilotEvent.objects.create(pilot=pilot,actor=user,client_key=uuid.UUID(str(key)),action=action,request_digest=digest(payload),details=details or {})
def replay(user,key,action,payload):
    try:token=uuid.UUID(str(key))
    except (ValueError,TypeError,AttributeError):raise ValidationError('Reload this form before submitting.')
    old=PilotEvent.objects.filter(actor=user,client_key=token).first()
    if old and (old.action!=action or old.request_digest!=digest(payload)):raise ValidationError('This submission was already used for different work.')
    return old
def _manager(user,pilot):
    if not manages(user,pilot):raise PermissionDenied
def _days(start,end,pilot):
    return [start+timedelta(days=i) for i in range((end-start).days+1) if (start+timedelta(days=i)).weekday() in pilot.weekdays and (start+timedelta(days=i)).isoformat() not in pilot.excluded_dates]
def protocol(pilot):
    fields=('study_id','owner_id','is_synthetic','method','timezone_name','starts_on','ends_on','weekdays','excluded_dates','definitions','minimum_pairs','minimum_days','minimum_output','target_percent','features')
    return {**{f:getattr(pilot,f) for f in fields},'baseline':{f:getattr(pilot.study,f) for f in ('role','workload_type','output_unit','output_definition','acceptance_criteria','effort_definition','quality_definition','source_definition','comparison_plan','start_date','end_date','collection_started_at')},
        'roster':list(pilot.members.order_by('employee_id').values('employee_id','arm','match_key','rollout_on')),
        'estimator':'matched-rate-ratio-did-v1','uncertainty':'paired-cluster-percentile-bootstrap-95-v1','bootstrap_seed':7132026,'bootstrap_replicates':1000,
        'quality_rule':'no lower pass rate, no higher rework/output, overtime/hour or admin/hour; no missing-day imputation'}


@transaction.atomic
def save_pilot(*,user,study,client_key,expected_revision='new',pk=None,**data):
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    baseline._lock_scope(study);study=BaselineStudy.objects.select_for_update().get(pk=study.pk)
    user=actor(user,'tasker.change_pilot' if pk else 'tasker.add_pilot')
    if not baseline.visible_studies_for(user).filter(pk=study.pk).exists() or not baseline._can_manage(user,study):raise PermissionDenied
    payload=dict(pk=pk,study_id=study.pk,**{k:getattr(v,'pk',v) for k,v in data.items()})
    old=replay(user,client_key,'protocol.save',payload)
    if old:return visible_pilots(user).get(pk=old.pilot_id)
    item=visible_pilots(user).select_for_update(of=('self',)).get(pk=pk) if pk else Pilot(study=study,created_by=user)
    if pk:
        _manager(user,item);expect(item,expected_revision)
        if item.approved_at:raise ValidationError('An approved protocol is frozen. Create a new pilot.')
    elif expected_revision!='new':raise ValidationError('Reload the new protocol form.')
    for f in ('owner','title','is_synthetic','method','timezone_name','starts_on','ends_on','weekdays','excluded_dates','definitions','minimum_pairs','minimum_days','minimum_output','target_percent','features'):
        if f in data:setattr(item,f,data[f])
    if item.owner_id and (not item.owner.is_active or not manages(item.owner,item)):raise ValidationError('Choose an active pilot owner who can manage this study scope.')
    item.revision=uuid.uuid4();item.updated_by=user;item.full_clean();item.save()
    event(user,item,client_key,'protocol.save',payload)
    return item


@transaction.atomic
def add_member(*,user,pk,expected_revision,client_key,employee,arm,match_key,rollout_on=None):
    user,pilot=_lock(user,pk,'tasker.change_pilot');_manager(user,pilot)
    payload=dict(pk=pk,employee_id=employee.pk,arm=arm,match_key=match_key,rollout_on=rollout_on)
    old=replay(user,client_key,'roster.add',payload)
    if old:return pilot.members.get(pk=old.details['member_id'])
    expect(pilot,expected_revision)
    if pilot.approved_at:raise ValidationError('The approved roster is frozen. Withdrawal preserves missing-data coverage.')
    if pilot.members.count()>=40:raise ValidationError('Use a bounded pilot of at most 40 people; larger strata need another protocol.')
    if not baseline._employee_in_scope(employee.pk,pilot.study):raise ValidationError('Choose an active employee in the pilot team/company.')
    row=PilotMember(pilot=pilot,employee=employee,arm=arm,match_key=match_key.strip(),rollout_on=rollout_on)
    if not row.match_key:raise ValidationError('Name the predeclared matching block.')
    row.full_clean();row.save();pilot.revision=uuid.uuid4();pilot.save(update_fields=['revision','updated_at'])
    event(user,pilot,client_key,'roster.add',payload,{'member_id':row.pk})
    return row


@transaction.atomic
def approve_protocol(*,user,pk,expected_revision,client_key):
    user,pilot=_lock(user,pk,'tasker.change_pilot');_manager(user,pilot)
    payload={'pk':pk,'revision':str(expected_revision)}
    if replay(user,client_key,'protocol.approve',payload):return pilot
    expect(pilot,expected_revision)
    if pilot.approved_at:raise ValidationError('This protocol is already approved.')
    if not pilot.owner_id or not manages(pilot.owner,pilot):raise ValidationError('Select the registered pilot owner before approval.')
    if 'core' not in pilot.features:raise ValidationError('Include the core workspace in the pilot feature selection.')
    if not all(pilot.definitions.get(k,'').strip() for k in DEFINITIONS):raise ValidationError('Define all source, workload, spillover, missing-data and independent-review policies first.')
    study=pilot.study
    if not study.collection_started_at or study.status=='DRAFT' or not all(getattr(study,k) for k in ('role','workload_type','output_unit','output_definition','acceptance_criteria','effort_definition','quality_definition','source_definition','comparison_plan')):
        raise ValidationError('Start a fully defined Stage 2 baseline before approving the pilot.')
    today=timezone.now().astimezone(ZoneInfo(pilot.timezone_name)).date()
    if not pilot.is_synthetic and (pilot.starts_on<=today or timezone.localdate(study.collection_started_at,ZoneInfo(pilot.timezone_name))>=pilot.starts_on):raise ValidationError('Real protocols must be approved prospectively, before rollout begins.')
    if len(_days(study.start_date,study.end_date,pilot))<pilot.minimum_days or len(_days(pilot.starts_on,pilot.ends_on,pilot))<pilot.minimum_days:raise ValidationError('Both windows need the predefined minimum number of observation days.')
    roster=list(pilot.members.select_related('employee'))
    if not roster:raise ValidationError('Choose an explicit cohort roster; nobody is enrolled automatically.')
    blocks={}
    for row in roster:
        if not baseline._employee_in_scope(row.employee_id,study):raise ValidationError('A cohort employee left this scope. Review the draft roster.')
        blocks.setdefault(row.match_key,set()).add(row.arm)
        if row.arm=='PILOT' and row.rollout_on!=pilot.starts_on:raise ValidationError('The early group starts on the follow-up window’s first date.')
        if row.arm=='COMPARISON' and pilot.method=='STAGGERED' and (not row.rollout_on or row.rollout_on<=pilot.ends_on):raise ValidationError('The later group must remain unexposed throughout this comparison window.')
        if row.arm=='COMPARISON' and pilot.method=='MATCHED' and row.rollout_on is not None:raise ValidationError('A matched control has no rollout date in this protocol.')
    if pilot.method!='BEFORE_AFTER' and any(v!={'PILOT','COMPARISON'} for v in blocks.values()):raise ValidationError('Each matching block needs one early and one comparison employee.')
    if pilot.method=='BEFORE_AFTER' and any(r.arm!='PILOT' for r in roster):raise ValidationError('Before/after protocols contain only the pilot arm and remain exploratory.')
    pilot.state='APPROVED';pilot.approved_at=timezone.now();pilot.approved_by=user
    pilot.protocol_digest=digest(protocol(pilot));pilot.revision=uuid.uuid4();pilot.full_clean();pilot.save()
    event(user,pilot,client_key,'protocol.approve',payload,{'protocol_digest':pilot.protocol_digest})
    return pilot


@transaction.atomic
def save_observation(*,user,pk,client_key,expected_revision='new',observation_id=None,outcome_ids=None,**data):
    user,pilot=_lock(user,pk,'tasker.change_pilotobservation' if observation_id else 'tasker.add_pilotobservation')
    if not pilot.approved_at or pilot.state!='APPROVED':raise ValidationError('Approve and open the protocol before recording observations.')
    employee=data.get('employee')
    if not employee or not pilot.members.filter(employee=employee,withdrawn_at__isnull=True).exists() or not baseline._employee_in_scope(employee.pk,pilot.study):raise ValidationError('Choose a current employee from the frozen roster.')
    if not manages(user,pilot) and employee.pk!=user.pk:raise PermissionDenied
    payload=dict(pk=pk,observation_id=observation_id,outcome_ids=sorted(outcome_ids or []),**{k:getattr(v,'pk',v) for k,v in data.items()})
    old=replay(user,client_key,'observation.save',payload)
    if old:return observations_for(user,pilot).get(pk=old.details['observation_id'])
    row=observations_for(user,pilot).select_for_update(of=('self',)).get(pk=observation_id) if observation_id else PilotObservation(pilot=pilot,created_by=user)
    if observation_id:
        expect(row,expected_revision)
        if row.status!='DRAFT':raise ValidationError('Accepted/retracted evidence is retained. Enter a corrected source record.')
    elif expected_revision!='new':raise ValidationError('Reload the observation form.')
    allowed={'employee','phase','baseline_observation','observed_on','source_key','evidence','tasker_exposed','output_source',*METRICS}
    if set(data)-allowed:raise ValidationError('Use the current observation fields.')
    for name,value in data.items():setattr(row,name,value)
    lo,hi=(pilot.study.start_date,pilot.study.end_date) if row.phase=='BASELINE' else (pilot.starts_on,pilot.ends_on)
    if row.observed_on not in _days(lo,hi,pilot) or row.observed_on>timezone.now().astimezone(ZoneInfo(pilot.timezone_name)).date():raise ValidationError('Choose an elapsed observation day inside the declared window.')
    if row.baseline_observation_id and not baseline.visible_observations_for(user).filter(pk=row.baseline_observation_id).exists():raise PermissionDenied
    row.revision=uuid.uuid4();row.updated_by=user;row.full_clean();row.save()
    if outcome_ids and (row.phase!='FOLLOWUP' or row.output_source!='OUTCOMES'):raise ValidationError('Link goal outcomes only for follow-up output measured by outcomes.')
    from .goals import visible_outcomes_for
    selected=list(visible_outcomes_for(user).filter(pk__in=outcome_ids or []))
    if len(selected)!=len(set(outcome_ids or [])) or len(selected)>50:raise ValidationError('Choose at most 50 visible canonical outcomes.')
    row.outcomes.set(selected)
    event(user,pilot,client_key,'observation.save',payload,{'observation_id':row.pk})
    return row


def source_current(row):
    """References are checked on acceptance and every subsequent report."""
    if row.phase=='BASELINE':
        base=row.baseline_observation
        return bool(base and not base.is_deleted and base.status=='ACCEPTED' and (base.study_id,base.employee_id,base.observed_on,base.role,base.workload_type)==
            (row.pilot.study_id,row.employee_id,row.observed_on,row.pilot.study.role,row.pilot.study.workload_type) and base.accepted_by_id!=base.employee_id)
    if row.output_source=='REGISTER':return row.status=='DRAFT' or bool(row.evidence.strip() and row.accepted_at and row.accepted_by_id and row.accepted_by_id!=row.employee_id)
    from .business_outcomes import filter_valid_business_outcomes
    from tasker.models import OutcomeMeasurement
    ids=[item.pk for item in row.outcomes.all()]
    if not ids:return False
    current=filter_valid_business_outcomes(OutcomeMeasurement.objects.filter(pk__in=ids,status='ACCEPTED',employee=row.employee,
        company_id=row.pilot.company_id,team_id=row.pilot.team_id,occurred_on=row.observed_on,key_result__unit=row.pilot.study.output_unit))
    return current.count()==len(ids) and sum((v.value for v in current),0)==row.accepted_output


@transaction.atomic
def review_observation(*,user,pk,observation_id,expected_revision,client_key,action,reason=''):
    user,pilot=_lock(user,pk,'tasker.change_pilotobservation');_manager(user,pilot)
    row=observations_for(user,pilot).select_for_update(of=('self',)).get(pk=observation_id)
    payload=dict(pk=pk,observation_id=observation_id,action=action,reason=reason)
    if replay(user,client_key,'observation.review',payload):return row
    expect(row,expected_revision)
    if action=='accept':
        if row.status!='DRAFT' or row.employee_id==user.pk:raise ValidationError('An independent manager must accept draft observations; self-acceptance is excluded.')
        if not row.evidence.strip() or not source_current(row):raise ValidationError('Provide current, independently accepted source evidence before acceptance.')
        if not pilot.members.filter(employee=row.employee,withdrawn_at__isnull=True).exists() or not baseline._employee_in_scope(row.employee_id,pilot.study):raise ValidationError('The employee is no longer in the current pilot scope.')
        row.status='ACCEPTED';row.accepted_at=timezone.now();row.accepted_by=user
    elif action=='retract':
        if row.status!='ACCEPTED' or not reason.strip() or len(reason)>500:raise ValidationError('Retract accepted evidence with a reason of up to 500 characters.')
        row.status='RETRACTED'
    else:raise ValidationError('Choose accept or retract.')
    row.revision=uuid.uuid4();row.updated_by=user;row.full_clean();row.save()
    event(user,pilot,client_key,'observation.review',payload,{'observation_id':row.pk,'status':row.status,'reason':reason.strip()})
    return row


@transaction.atomic
def control_pilot(*,user,pk,expected_revision,client_key,action,member_id=None,reason=''):
    user,pilot=_lock(user,pk,'tasker.change_pilot');_manager(user,pilot)
    payload=dict(pk=pk,action=action,member_id=member_id,reason=reason)
    if replay(user,client_key,'pilot.control',payload):return pilot
    expect(pilot,expected_revision)
    if pilot.state=='CLOSED':raise ValidationError('A closed pilot retains its evidence. Create a new protocol for further work.')
    if action in ('pause','close'):
        if not reason.strip():raise ValidationError('Record a shared operating reason.')
        pilot.state='PAUSED' if action=='pause' else 'CLOSED'
    elif action=='resume':
        if pilot.state!='PAUSED' or not pilot.approved_at:raise ValidationError('Resume an approved paused pilot.')
        pilot.state='APPROVED'
    elif action=='withdraw':
        row=pilot.members.select_for_update().get(pk=member_id)
        if not reason.strip():raise ValidationError('Record a withdrawal reason; original expected days are retained.')
        row.withdrawn_at=timezone.now();row.withdrawal_reason=reason.strip();row.access_enabled=False;row.full_clean();row.save()
    elif action in ('authorize_rollout','enable_access'):
        if not getattr(settings,'TASKER_ROLLOUT_WRITES_ENABLED',False) or pilot.is_synthetic:raise ValidationError('Real rollout is disabled. This implementation does not authorize deployment or enrollment.')
        from .pilot_release import release_readiness
        if not release_readiness(user=user,pilot=pilot)['ready_for_real_pilot']:raise ValidationError('Resolve all real release and operating checks before authorizing access.')
        if action=='authorize_rollout':
            if timezone.now().astimezone(ZoneInfo(pilot.timezone_name)).date()>=pilot.starts_on:raise ValidationError('Authorize the real release before its declared start. Use a new prospective protocol for a delayed rollout.')
            pilot.rollout_authorized_at=timezone.now();pilot.rollout_authorized_by=user
        else:
            if not pilot.rollout_authorized_at:raise ValidationError('Authorize this frozen cohort release first.')
            row=pilot.members.select_for_update().get(pk=member_id)
            if row.withdrawn_at or not baseline._employee_in_scope(row.employee_id,pilot.study):raise ValidationError('Review this employee’s current scope.')
            if row.arm=='COMPARISON':
                from .pilot_metrics import pilot_report
                if pilot_report(user=user,pilot=pilot)['productivity_gate']!='VALIDATED_UNDER_PROTOCOL':raise ValidationError('Expansion to the later group requires completed productivity validation and separate authorization.')
            row.access_enabled=True;row.full_clean();row.save()
    else:raise ValidationError('Choose a supported pilot control.')
    pilot.revision=uuid.uuid4();pilot.full_clean();pilot.save()
    event(user,pilot,client_key,'pilot.control',payload,{'state':pilot.state,'member_id':member_id})
    return pilot


@transaction.atomic
def validate_productivity(*,user,pk,expected_revision,client_key,report_digest,reference):
    user,pilot=_lock(user,pk,'tasker.change_pilot');_manager(user,pilot);expect(pilot,expected_revision)
    payload=dict(pk=pk,report_digest=report_digest,reference=reference)
    if replay(user,client_key,'pilot.validate',payload):return pilot
    from .pilot_metrics import pilot_report
    report=pilot_report(user=user,pilot=pilot)
    if not reference.strip() or report['report_digest']!=report_digest or not report['eligible_for_validation']:
        raise ValidationError('Validation needs the current real, complete, sufficiently precise comparison and a review reference.')
    PilotValidation.objects.get_or_create(pilot=pilot,report_digest=report_digest,defaults={'protocol_digest':pilot.protocol_digest,'reference':reference,'reviewer':user})
    event(user,pilot,client_key,'pilot.validate',payload)
    return pilot
