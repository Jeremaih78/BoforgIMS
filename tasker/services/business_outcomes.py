from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from accounting.models import Company
from inventory.replenishment_policies import current_actor

from inventory.replenishment_policies import company_ids
from operations.models import Responsibility
from operations.registry import SOURCES
from tasker.business_models import BusinessRecordLink
from tasker.business_outcome_models import BusinessContribution, BusinessOutcomeEvidence
from tasker.goal_models import OutcomeMeasurement
from tasker.models import TeamMembership
from tasker.services import business_links, goals


def can_attribute(user,link):
    from tasker.organization import can_manage_team
    return (user.is_active and user.has_perm('tasker.change_businesscontribution')
        and link.company_id and (company_ids(user,manage=True).filter(pk=link.company_id).exists()
            or can_manage_team(user,team_for(link))))


def _owner_team_valid(link,team_id):
    return not team_id or TeamMembership.objects.filter(user_id=link.owner_id,team_id=team_id,
        is_active=True,is_deleted=False,team__is_deleted=False,team__company_id=link.company_id).exists()


def team_for(link):
    if link.source_type in SOURCES:
        return Responsibility.objects.filter(pk=link.source_record_id,kind=link.source_type,
            company_id=link.company_id).values_list('team_id',flat=True).first()
    return BusinessContribution.objects.filter(link=link).values_list('team_id',flat=True).first()


@transaction.atomic
def attribute(*,user,link_id,team,key_result,expected_revision):
    # Canonical source before projection and scope, matching reconciliation.
    stored=BusinessRecordLink.objects.get(pk=link_id)
    business_links._adapter(stored.source_type).load_source(stored.source_type,stored.source_record_id,lock=True)
    link=BusinessRecordLink.objects.select_for_update().get(pk=link_id)
    if link.company_id:
        Company.objects.select_for_update().get(pk=link.company_id)
    user=current_actor(user)
    current=business_links.get_business_link(user=user,pk=link_id)
    if not can_attribute(user,current):
        raise PermissionDenied
    if str(link.revision) != str(expected_revision) or current.source_stale:
        raise ValidationError('Source facts changed. Wait for reconciliation and reload before attributing work.')
    if BusinessOutcomeEvidence.objects.filter(link=link).exists():
        raise ValidationError('This action already has retained outcome evidence. Its attribution is fixed.')
    team_id=getattr(team,'pk',None)
    if current.source_type in SOURCES and team_id != team_for(current):
        raise ValidationError('Choose the team recorded in the operational source responsibility.')
    if not _owner_team_valid(current,team_id):
        raise ValidationError('The responsible employee must be a current member of the attributed team.')
    if key_result and not goals.visible_key_results_for(user).filter(pk=key_result.pk).exists():
        raise PermissionDenied
    mapping=BusinessContribution.objects.filter(link=link).first() or BusinessContribution(link=link)
    mapping.team,mapping.key_result,mapping.updated_by=team,key_result,user
    mapping.full_clean();mapping.save()
    return mapping


def evidence_valid(evidence):
    try:
        link=evidence.link
        if not business_links.enabled(link.source_type):
            return False
        facts=business_links._adapter(link.source_type).snapshot(link.source_type,link.source_record_id)
        outcome=evidence.outcome
        return (facts['status']=='COMPLETED' and facts['source_version']==evidence.source_version
            and business_links.adapter.digest(facts)==evidence.source_digest
            and facts['owner_id']==outcome.employee_id and facts['company_id']==outcome.company_id
            and team_for(link)==outcome.team_id and _owner_team_valid(link,outcome.team_id)
            and completion_day(link)==outcome.occurred_on)
    except (ValidationError,Responsibility.DoesNotExist):
        return False


def valid_outcome_ids(queryset):
    return [row.outcome_id for row in BusinessOutcomeEvidence.objects.filter(outcome__in=queryset)
        .select_related('link','outcome') if evidence_valid(row)]


def filter_valid_business_outcomes(queryset):
    from django.db.models import Q
    business=queryset.filter(source_type='business_action')
    return queryset.filter(~Q(source_type='business_action') | Q(pk__in=valid_outcome_ids(business)))


def completion_day(link):
    """Use dated native evidence; never back-date or treat first observation as completion."""
    stamp=None
    if link.source_type=='collection_task':
        from credit_control.models import CollectionTask
        stamp=CollectionTask.objects.filter(pk=link.source_record_id).values_list('completed_at',flat=True).first()
    elif link.source_type in SOURCES:
        row=Responsibility.objects.get(pk=link.source_record_id,kind=link.source_type)
        spec=SOURCES[row.kind]
        native=getattr(row,spec.field)
        if row.kind=='quotation_work':
            from sales.models import Invoice
            return Invoice.objects.filter(quotation_id=row.source_id).order_by('date','pk').values_list('date',flat=True).first()
        field={'shipment_work':'closed_at','stocktake_work':'closed_at','period_work':'locked_at',
            'fulfilment_work':'status_changed_at','bill_work':'status_changed_at'}.get(row.kind)
        stamp=row.quality_accepted_at if row.kind=='service_work' else getattr(native,field,None) if field else None
    return timezone.localdate(stamp) if stamp else None


@transaction.atomic
def accept_source_output(*,user,link_id,expected_revision):
    stored=BusinessRecordLink.objects.get(pk=link_id)
    business_links._adapter(stored.source_type).load_source(stored.source_type,stored.source_record_id,lock=True)
    link=BusinessRecordLink.objects.select_for_update().get(pk=link_id)
    if link.company_id:
        Company.objects.select_for_update().get(pk=link.company_id)
    user=current_actor(user)
    current=business_links.get_business_link(user=user,pk=link_id)
    if not can_attribute(user,current):
        raise PermissionDenied
    if str(link.revision)!=str(expected_revision) or current.source_stale:
        raise ValidationError('Source facts changed. Reconcile and reload before accepting an output.')
    if current.status!='COMPLETED' or current.source_type in ('replenishment_plan','promise_to_pay'):
        raise ValidationError('This source does not establish a completed, acceptable output. A cleared stock need or promise status alone is insufficient.')
    mapping=BusinessContribution.objects.select_related('key_result__goal','team').filter(link=link).first()
    if not mapping or not mapping.key_result_id:
        raise ValidationError('Map this action to an operational output result before accepting evidence.')
    result=mapping.key_result
    if mapping.team_id!=team_for(current):
        raise ValidationError('The source team changed. Review the explicit attribution before accepting an output.')
    if result.unit.strip().lower() not in ('outputs','accepted outputs') or result.aggregation!='SUM':
        raise ValidationError('Use a SUM result measured in “outputs” or “accepted outputs”. Financial totals require financial source evidence.')
    if not _owner_team_valid(current,mapping.team_id) or not current.owner_id:
        raise ValidationError('Current employee and team membership are required.')
    occurred_on=completion_day(current)
    if not occurred_on:
        raise ValidationError('The source has no dated completion evidence. Legacy or bulk-updated status alone cannot earn an accepted output.')
    old=BusinessOutcomeEvidence.objects.select_related('link','outcome').filter(link=link).first()
    if old:
        if not evidence_valid(old):
            raise ValidationError('This source action already has retained evidence that needs retraction/review; it cannot earn a second credit.')
        return goals.accept_outcome(outcome=old.outcome,user=user,_business_verified=True)
    outcome=goals.record_outcome(user=user,key_result=result,employee=current.owner,value=1,
        evidence=f'Completed source action {link.source_app}/{link.source_type}/{link.source_record_id}; source version {current.source_version}. One reviewed operational output.',
        source_type='business_action',source_key=f'action:{link.pk}',company=current.company,
        team=mapping.team,occurred_on=occurred_on)
    BusinessOutcomeEvidence.objects.create(link=link,outcome=outcome,source_version=current.source_version,source_digest=current.source_digest)
    return goals.accept_outcome(outcome=outcome,user=user,_business_verified=True)


def check_acceptance(outcome):
    if outcome.source_type=='business_action':
        evidence=BusinessOutcomeEvidence.objects.select_related('link','outcome').filter(outcome=outcome).first()
        if not evidence or not evidence_valid(evidence):
            raise ValidationError('The authoritative business source no longer supports this output.')


@transaction.atomic
def retract_invalid_for(link):
    """Preserve original evidence; remove credit after reopen, reassignment or deletion."""
    evidence=BusinessOutcomeEvidence.objects.select_related('link','outcome').filter(link=link,
        outcome__status='ACCEPTED').first()
    if evidence and not evidence_valid(evidence):
        outcome=evidence.outcome
        reason='Business source reopened, changed owner/scope, was disabled or no longer supports accepted output.'
        history=[*outcome.acceptance_history,{'status':'RETRACTED','at':timezone.now().isoformat(),'actor_id':None,'reason':reason}]
        OutcomeMeasurement.all_objects.filter(pk=outcome.pk,status='ACCEPTED').update(status='RETRACTED',
            retracted_at=timezone.now(),retraction_reason=reason,acceptance_history=history,updated_at=timezone.now())
