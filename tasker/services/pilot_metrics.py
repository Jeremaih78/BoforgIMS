"""Comparable business output, explicit missing days and paired-cluster uncertainty."""
from decimal import Decimal
from datetime import timedelta
import random
from django.db.models import Sum,F
from django.utils import timezone
from zoneinfo import ZoneInfo
from .pilots import actor,visible_pilots,observations_for,manages,protocol,digest,_days,source_current
from tasker.pilot_models import PilotValidation,METRICS
from tasker.models import BaselineObservation


def rate(n,d):return n/d if d and n is not None else None
def _summary(rows,expected):
    sums={f:sum((r[f] for r in rows if r[f] is not None),Decimal(0)) for f in METRICS}
    missing={f:sum(r[f] is None for r in rows)+max(0,expected-len(rows)) for f in METRICS}
    known=lambda f:sums[f] if rows and not missing[f] else None
    metrics={f:known(f) for f in METRICS}
    metrics.update(output_per_hour=rate(known('accepted_output'),known('staffed_hours')),
        cycle_minutes_mean=rate(known('cycle_minutes'),known('cycle_samples')),
        quality_rate=rate(known('quality_passed'),known('quality_checked')),
        rework_per_output=rate(known('rework_count'),known('accepted_output')),
        deadline_rate=rate(known('deadline_met'),known('deadline_due')),
        overtime_per_hour=rate(known('overtime_hours'),known('staffed_hours')),
        admin_per_hour=rate(known('admin_hours'),known('staffed_hours')))
    return {'expected':expected,'accepted':len(rows),'missing_days':max(0,expected-len(rows)),
        'complete':sum(all(r[f] is not None for f in METRICS) for r in rows),
        'missing_fields':missing,'metrics':metrics,'sums':sums,
        'coverage_percent':Decimal(len(rows)*100)/expected if expected else None}


def _estimate(cells):
    values={k:rate(v['accepted_output'],v['staffed_hours']) for k,v in cells.items()}
    if any(values.get(k) in (None,0) for k in ('PILOT_BASELINE','COMPARISON_BASELINE','COMPARISON_FOLLOWUP')) or values.get('PILOT_FOLLOWUP') is None:return None
    return ((values['PILOT_FOLLOWUP']/values['PILOT_BASELINE'])/(values['COMPARISON_FOLLOWUP']/values['COMPARISON_BASELINE'])-1)*100


def _interval(blocks):
    if len(blocks)<4:return {'low':None,'high':None,'replicates':0,'invalid':0}
    rng=random.Random(7132026);estimates=[];invalid=0
    for _ in range(1000):
        chosen=rng.choices(blocks,k=len(blocks))
        cells={k:{f:sum((b[k][f] for b in chosen),Decimal(0)) for f in ('accepted_output','staffed_hours')} for k in chosen[0]}
        value=_estimate(cells)
        if value is None:invalid+=1
        else:estimates.append(value)
    if invalid or not estimates:return {'low':None,'high':None,'replicates':len(estimates),'invalid':invalid}
    estimates.sort()
    def percentile(p):
        index=Decimal(len(estimates)-1)*Decimal(str(p));lo=int(index);hi=min(lo+1,len(estimates)-1)
        return estimates[lo]+(estimates[hi]-estimates[lo])*(index-lo)
    return {'low':percentile(.025),'high':percentile(.975),'replicates':1000,'invalid':0}


def _row(row):
    base=row.baseline_observation if row.phase=='BASELINE' else None
    result={f:getattr(base if base and f in ('staffed_hours','accepted_output','quality_passed','rework_count') else row,f) for f in METRICS}
    result.update(employee_id=row.employee_id,day=row.observed_on,phase=row.phase,exposed=row.tasker_exposed,id=row.pk,
        proof=digest([row.pk,row.revision,row.accepted_at,row.accepted_by_id,row.source_key,row.evidence,
            [(o.pk,o.updated_at,o.status,o.value) for o in sorted(row.outcomes.all(),key=lambda v:v.pk)],
            [base.pk,base.updated_at,base.status,base.accepted_at,base.accepted_by_id,base.staffed_hours,base.accepted_output,base.quality_passed,base.rework_count] if base else None]))
    return result


def pilot_report(*,user,pilot):
    user=actor(user);pilot=visible_pilots(user).get(pk=pilot.pk)
    manager=manages(user,pilot);members=list(pilot.members.select_related('employee').order_by('match_key','arm'))
    own=not manager
    if own:members=[m for m in members if m.employee_id==user.pk]
    observations=observations_for(user,pilot).filter(status='ACCEPTED').select_related('pilot__study').prefetch_related('outcomes')
    eligible_ids={m.employee_id for m in members};rows=[];invalid_sources=0;core_unlinked=0
    for row in observations.order_by('employee_id','observed_on','pk'):
        if row.employee_id not in eligible_ids or not source_current(row):invalid_sources+=1;continue
        r=_row(row)
        lo,hi=(pilot.study.start_date,pilot.study.end_date) if row.phase=='BASELINE' else (pilot.starts_on,pilot.ends_on)
        if row.observed_on not in _days(lo,hi,pilot):invalid_sources+=1;continue
        rows.append(r)
    baseline_days=_days(pilot.study.start_date,pilot.study.end_date,pilot);follow_days=_days(pilot.starts_on,pilot.ends_on,pilot)
    from .baseline import visible_observations_for,_employee_in_scope
    canonical=visible_observations_for(user).filter(study=pilot.study,status='ACCEPTED',employee_id__in=eligible_ids)
    # Disclose accepted canonical baseline evidence still lacking a supplement;
    # never silently label its absent quality/timing denominator as zero.
    linked_base=set(observations.filter(phase='BASELINE').values_list('baseline_observation_id',flat=True))
    core_unlinked=canonical.exclude(pk__in=linked_base).count()
    cells={};pair_blocks=[]
    for arm in ('PILOT','COMPARISON'):
        n=sum(m.arm==arm for m in members)
        for phase,days in (('BASELINE',baseline_days),('FOLLOWUP',follow_days)):
            ids={m.employee_id for m in members if m.arm==arm}
            cells[arm+'_'+phase]=_summary([r for r in rows if r['employee_id'] in ids and r['phase']==phase],n*len(days))
    contamination=sum(r['exposed'] is None or r['exposed']!=(r['phase']=='FOLLOWUP' and any(m.employee_id==r['employee_id'] and m.arm=='PILOT' for m in members)) for r in rows)
    keys={m.match_key for m in members}
    complete_cells=all(c['expected'] and c['complete']==c['expected'] for c in cells.values())
    if complete_cells:
        for key in sorted(keys):
            block={}
            for arm in ('PILOT','COMPARISON'):
                ids={m.employee_id for m in members if m.arm==arm and m.match_key==key}
                for phase in ('BASELINE','FOLLOWUP'):
                    block[arm+'_'+phase]={f:sum((r[f] for r in rows if r['employee_id'] in ids and r['phase']==phase),Decimal(0)) for f in ('accepted_output','staffed_hours')}
            pair_blocks.append(block)
    point=_estimate({k:v['sums'] for k,v in cells.items()}) if complete_cells and not own and pilot.method!='BEFORE_AFTER' else None
    interval=_interval(pair_blocks) if point is not None else {'low':None,'high':None,'replicates':0,'invalid':0}
    quality_checks={}
    for arm in ('PILOT','COMPARISON'):
        before,after=cells[arm+'_BASELINE']['metrics'],cells[arm+'_FOLLOWUP']['metrics']
        for field,direction in (('quality_rate','HIGHER'),('rework_per_output','LOWER'),('overtime_per_hour','LOWER'),('admin_per_hour','LOWER')):
            a,b=before[field],after[field]
            quality_checks[arm+'_'+field]=None if a is None or b is None else b>=a if direction=='HIGHER' else b<=a
    blockers=[];today=timezone.now().astimezone(ZoneInfo(pilot.timezone_name)).date()
    if pilot.is_synthetic:blockers.append('synthetic_data_not_productivity_evidence')
    if own:blockers.append('manager_comparison_only')
    if not pilot.approved_at:blockers.append('protocol_not_approved')
    if pilot.approved_at and digest(protocol(pilot))!=pilot.protocol_digest:blockers.append('protocol_or_baseline_changed')
    if not pilot.is_synthetic and (not pilot.approved_at or timezone.localdate(pilot.approved_at,ZoneInfo(pilot.timezone_name))>=pilot.starts_on):blockers.append('protocol_not_prospective')
    if not pilot.is_synthetic and (not pilot.rollout_authorized_at or timezone.localdate(pilot.rollout_authorized_at,ZoneInfo(pilot.timezone_name))>=pilot.starts_on):blockers.append('rollout_not_authorized_before_start')
    if pilot.ends_on>=today:blockers.append('observation_window_open')
    if not complete_cells:blockers.append('missing_or_incomplete_employee_days')
    if invalid_sources:blockers.append('source_evidence_invalidated')
    if contamination:blockers.append('exposure_unknown_or_contaminated')
    if any(m.withdrawn_at for m in members):blockers.append('cohort_withdrawal_requires_review')
    if any(not _employee_in_scope(m.employee_id,pilot.study) for m in members):blockers.append('cohort_scope_changed')
    if pilot.method=='BEFORE_AFTER':blockers.append('no_comparison_group_exploratory_only')
    if len(pair_blocks)<pilot.minimum_pairs:blockers.append('insufficient_matched_pairs')
    if min(len(baseline_days),len(follow_days))<pilot.minimum_days:blockers.append('insufficient_observation_days')
    if any(c['sums']['accepted_output']<pilot.minimum_output for c in cells.values()):blockers.append('insufficient_accepted_output')
    if any(v is None for v in quality_checks.values()):blockers.append('quality_or_effort_guardrails_unknown')
    elif not all(quality_checks.values()):blockers.append('quality_rework_or_effort_regressed')
    if interval['low'] is None:blockers.append('uncertainty_not_estimable')
    elif interval['low']<=pilot.target_percent:blockers.append('greater_than_target_not_supported_at_95_percent')
    if not pilot.is_synthetic and (not pilot.study.collection_started_at or timezone.localdate(pilot.study.collection_started_at,ZoneInfo(pilot.timezone_name))>pilot.study.start_date):blockers.append('baseline_not_prospectively_started')
    if any(r['phase']=='BASELINE' and pilot.study.collection_started_at and r['day']<timezone.localdate(pilot.study.collection_started_at,ZoneInfo(pilot.timezone_name)) for r in rows):blockers.append('backdated_baseline_observation')
    report_digest=digest([pilot.protocol_digest,[(m.employee_id,m.arm,m.match_key,m.rollout_on,str(m.withdrawn_at)) for m in members],
        [(r['id'],r['proof']) for r in rows],invalid_sources,contamination])
    eligible=not blockers
    validated=eligible and PilotValidation.objects.filter(pilot=pilot,report_digest=report_digest,protocol_digest=pilot.protocol_digest).exists()
    gate='VALIDATED_UNDER_PROTOCOL' if validated else 'READY_FOR_INDEPENDENT_REVIEW' if eligible else 'SYNTHETIC_NOT_EVIDENCE' if pilot.is_synthetic else 'PENDING_REAL_VALIDATION'
    # These counts are operational diagnostics, not productivity denominators.
    costs={'known_usd':None,'held_unknown_usd':None,'attempts':0,'scope':'Only attributed AI requests in this follow-up window; no prompt bodies.'}
    goal_outputs=[]
    if manager:
        from tasker.ai_budget_models import AISpendAttempt
        from .capacity import aware_local
        from datetime import time
        start=aware_local(pilot.starts_on,time.min,pilot.timezone_name);end=aware_local(pilot.ends_on+timedelta(days=1),time.min,pilot.timezone_name)
        attempts=AISpendAttempt.objects.filter(reservation__company_id=pilot.company_id,reservation__user_id__in=eligible_ids,reservation__admitted_at__gte=start,reservation__admitted_at__lt=end)
        costs.update(attempts=attempts.count(),known_usd=attempts.filter(status='KNOWN').aggregate(v=Sum('charged_amount'))['v'] or Decimal(0),held_unknown_usd=attempts.filter(status__in=('RESERVED','DISPATCHED','UNKNOWN')).aggregate(v=Sum('held_amount'))['v'] or Decimal(0))
        from .goals import visible_outcomes_for
        from .business_outcomes import filter_valid_business_outcomes
        outcomes=filter_valid_business_outcomes(visible_outcomes_for(user)).filter(status='ACCEPTED',company_id=pilot.company_id,employee_id__in=eligible_ids,occurred_on__range=(pilot.starts_on,pilot.ends_on))
        if pilot.team_id:outcomes=outcomes.filter(team_id=pilot.team_id)
        goal_outputs=list(outcomes.values('key_result__title','key_result__unit').annotate(value=Sum('value')).order_by('key_result__title')[:30])
    canonical_summary=[]
    for arm in ('PILOT','COMPARISON'):
        ids=[m.employee_id for m in members if m.arm==arm]
        core=list(canonical.filter(employee_id__in=ids,accepted_by__isnull=False).exclude(accepted_by_id=F('employee_id')))
        hours=sum((b.staffed_hours for b in core if b.staffed_hours is not None),Decimal(0))
        output=sum((b.accepted_output for b in core if b.accepted_output is not None),Decimal(0))
        complete=bool(core) and len(core)==len(ids)*len(baseline_days) and all(b.staffed_hours is not None and b.accepted_output is not None for b in core)
        if ids:canonical_summary.append({'arm':arm.title(),'accepted':len(core),'expected':len(ids)*len(baseline_days),
            'output':output if complete else None,'hours':hours if complete else None,'rate':rate(output,hours) if complete else None})
    return {'pilot':pilot,'manager':manager,'cells':cells,'point_percent':point,'interval':interval,'quality_checks':quality_checks,'canonical_summary':canonical_summary,
        'blockers':list(dict.fromkeys(blockers)),'eligible_for_validation':eligible,'productivity_gate':gate,'report_digest':report_digest,
        'invalid_sources':invalid_sources,'canonical_baseline_without_supplement':core_unlinked,'contamination':contamination,
        'members':members,'rows':rows,'costs':costs,'goal_outputs':goal_outputs,
        'drafts':observations_for(user,pilot).filter(status='DRAFT').count(),
        'method_notice':'Same role/workload only. Matched rate-ratio difference-in-differences with a 95% paired-cluster percentile bootstrap; conditional on the declared comparability and parallel-trends assumptions. No causal guarantee or individual employee ranking.'}
