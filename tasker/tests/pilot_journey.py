"""Runnable invented cross-stage journey on disposable infrastructure only."""
import json,uuid
from datetime import time,timedelta
from unittest.mock import Mock,patch
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.test import override_settings
from django.utils import timezone
from tasker.models import AIConfiguration,Task,TaskTimeEntry,Goal,KeyResult,Initiative
from tasker.forms import DailyPlanForm
from tasker.services import captures,goals,focus,coordination,jobs,business_links
from tasker.services.planner import save_daily_plan
from tasker.services.capacity import save_work_policy,aware_local
from tasker.services.scheduling import save_schedule_draft,preview_schedule,accept_schedule
from tasker.services.effort import set_remaining_effort,effort_for_task
from tasker.services.workbench import work_version
from tasker.services.strategy import build_strategy_board,declare_work
from tasker.services.business_outcomes import attribute,accept_source_output
from tasker.ai.service import AIService
from tasker.ai.exceptions import AIDisabledError
from tasker.job_models import OutboxJob
from tasker.report_models import ManagerReportSnapshot
from inventory.models import Shipment,ShipmentItem,Supplier,Product,StockMovement
from accounting.models import JournalEntry
from operations.services import configure
from .pilot_fixtures import NOW

FLAGS=dict(TASKER_PILOT_ENABLED=True,TASKER_COORDINATION_ENABLED=True,TASKER_REPORTS_ENABLED=True,
    TASKER_STRATEGY_ENABLED=True,TASKER_SHIPMENT_ADAPTER_ENABLED=True,TASKER_REPORT_EMAIL_LIVE_ENABLED=False,
    TASKER_AI_OPENAI_API_KEY='',TASKER_PILOT_ACCESS_ENFORCED=False,TASKER_ROLLOUT_WRITES_ENABLED=False)


def run_journey(data):
    with override_settings(**FLAGS),patch('django.utils.timezone.now',return_value=NOW):return _run(data)


def _run(data):
    manager,worker,team=data.manager,data.worker,data.team
    # Exercise the real lease/failure/retry/report handler, with a synthetic failure.
    admin=get_user_model().objects.create_user('pilot-recovery-operator',is_superuser=True)
    job=jobs.enqueue_job(kind='reports.build',dedupe_key='stage13-recovery',owner=manager,
        payload={'team_id':team.pk,'report_date':NOW.date().isoformat()})
    claim=jobs.claim_next_job(worker_id='stage13-rehearsal')
    assert claim.pk==job.pk
    with patch('tasker.services.jobs._handler_for',return_value=Mock(side_effect=jobs.JobPermanentError('synthetic_failure'))):
        jobs.run_claimed_job(claim)
    job.refresh_from_db();assert job.state=='FAILED'
    jobs.retry_failed(user=admin,job_id=job.pk)
    claim=jobs.claim_next_job(worker_id='stage13-rehearsal');assert claim.pk==job.pk
    jobs.run_claimed_job(claim);job.refresh_from_db();assert job.state=='SUCCEEDED'
    assert ManagerReportSnapshot.objects.filter(team=team).exists()
    # The provider is unavailable; manual work still completes.
    AIConfiguration.objects.update(is_active=False)
    AIConfiguration.objects.create(name='SYNTHETIC offline rehearsal',is_enabled=False,is_active=True)
    provider=Mock(side_effect=AssertionError('No provider request is permitted'))
    try:AIService(provider_factory=provider).generate_text(user=worker,operation='rehearsal',prompt_key='task_summary',variables={})
    except AIDisabledError:pass
    else:raise AssertionError('AI outage was not surfaced')
    provider.assert_not_called()
    idea=captures.create_capture(user=worker,client_key=uuid.uuid4(),kind='IDEA',title='Improve shipment checks',
        content='PRIVATE_PILOT_IDEA: personal thought retained in my inbox.')
    task=captures.promote_capture(user=worker,pk=idea.pk,version=idea.version,team=team,title='Verify synthetic shipment checks',
        work_description='Shared procedure: review received quantities and evidence before closure.',definition_of_done='Quantities verified and authoritative shipment closure reviewed.')
    goal=Goal.objects.create(company=data.company,team=team,title='SYNTHETIC reliable accepted operations',status='ACTIVE',
        evidence_policy='One independently accepted authoritative source output; no manual task duplicate.')
    result=KeyResult.objects.create(goal=goal,title='SYNTHETIC accepted source outputs',unit='outputs',baseline=0,target=100)
    initiative=Initiative.objects.create(goal=goal,key_result=result,title='Review shipment receipt',owner=worker)
    goals.link_task_contribution(task=task,initiative=initiative,user=manager)
    form=DailyPlanForm({'focus_area':'Verified shipment output','top_priorities':[task.pk],'planned_tasks':[task.pk],
        'quick_notes':'PRIVATE_PILOT_PLAN: personal reflection'},user=worker,plan_date=NOW.date())
    assert form.is_valid(),form.errors
    save_daily_plan(form=form,user=worker,plan_date=NOW.date())
    save_work_policy(user=worker,timezone_name='Africa/Harare',weekdays=list(range(7)),start_time=time(8),end_time=time(17),buffer_minutes=15)
    set_remaining_effort(user=worker,task=task,remaining_minutes=30,expected_version=effort_for_task(task=task)['version'])
    start=aware_local(NOW.date(),time(14),'Africa/Harare');end=start+timedelta(minutes=30)
    block=save_schedule_draft(user=worker,task=task,starts_at=start,ends_at=end)
    preview=preview_schedule(user=worker,task=task,starts_at=start,ends_at=end,block=block)
    accept_schedule(user=worker,block=block,expected_version=block.revision,preview_version=preview['version'],override_reason='Synthetic rehearsal work window')
    task.refresh_from_db();declare_work(user=worker,team_id=team.pk,current_task_id=task.pk)
    focus.start_focus(task=task,user=worker,interval_minutes=25)
    focus.save_focus_notes(task=task,user=worker,notes='PRIVATE_PILOT_FOCUS: personal working note')
    task.refresh_from_db()
    request=coordination.create_request(user=worker,task=task,kind='HANDOFF',resolver=manager,
        reason='Need a manager check of shipment receipt',requested_action='Verify quantities before closure',
        expected_response_at=NOW+timedelta(hours=1),client_key=uuid.uuid4(),expected_task_version=work_version(task))
    task.refresh_from_db();assert task.status=='BLOCKED'
    request=coordination.acknowledge_request(user=manager,request_id=request.pk,expected_revision=request.revision)
    coordination.resolve_request(user=manager,request_id=request.pk,expected_revision=request.revision,resolution='Receipt verified; original operator can continue.')
    task.refresh_from_db();assert task.status=='PENDING'
    for user in (manager,worker):user.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=('inventory','operations')))
    supplier=Supplier.objects.create(name='SYNTHETIC rehearsal supplier')
    product=Product.objects.create(name='SYNTHETIC printing consumable',sku='S13-REHEARSAL',quantity=1)
    shipment=Shipment.objects.create(supplier=supplier,origin_country='ZW',destination_country='ZW',incoterm='FOB',shipping_method='ROAD',
        status='RECEIVED',received_at=NOW,received_by=worker)
    ShipmentItem.objects.create(shipment=shipment,product=product,quantity_expected=1,quantity_received=1,unit_purchase_price=1)
    row=configure(user=manager,kind='shipment_work',source_id=shipment.pk,company_id=data.company.pk,team_id=team.pk,
        assigned_to_id=worker.pk,enabled=True,next_review_on=NOW.date(),contact_interval_days=None,sla_due_on=None,expected_revision='new',client_key=uuid.uuid4())
    link=business_links.reconcile_business_source(source_type=row.kind,source_id=row.pk)
    attribute(user=manager,link_id=link.pk,team=team,key_result=result,expected_revision=link.revision)
    postings=(StockMovement.objects.count(),JournalEntry.objects.count())
    shipment.transition_status('CLOSED',actor=manager,note='SYNTHETIC source review')
    link=business_links.reconcile_business_source(source_type=row.kind,source_id=row.pk)
    outcome=accept_source_output(user=manager,link_id=link.pk,expected_revision=link.revision)
    assert accept_source_output(user=manager,link_id=link.pk,expected_revision=link.revision).pk==outcome.pk
    focus.complete_focus(task=task,user=worker,accept_definition=True,definition_version=focus.completion_definition_version(task))
    assert goals.key_result_progress(result)['current']==1
    assert (StockMovement.objects.count(),JournalEntry.objects.count())==postings
    assert not TaskTimeEntry.objects.filter(user=worker,ended_at__isnull=True).exists()
    board=build_strategy_board(user=manager,team_id=team.pk,day=NOW.date())
    for sentinel in ('PRIVATE_PILOT_IDEA','PRIVATE_PILOT_PLAN','PRIVATE_PILOT_FOCUS'):
        assert sentinel not in json.dumps(board,default=str)
    assert not captures.visible_captures_for(manager).filter(pk=idea.pk).exists()
    try:business_links.get_business_link(user=data.outsider,pk=link.pk)
    except PermissionDenied:pass
    else:raise AssertionError('Restricted employee could read the operational source')
    assert data.pilot.is_synthetic or data.company.name.startswith('SYNTHETIC')
    return {'private_capture':True,'goal_link':True,'capacity_plan':True,'focus':True,'block_handoff_resolved':True,
        'native_source_accepted_once':True,'manager_board_private':True,'restricted_user_denied':True,
        'ai_outage_manual_path':True,'failed_job_recovered':True,'provider_requests':0,'external_sends':0}
