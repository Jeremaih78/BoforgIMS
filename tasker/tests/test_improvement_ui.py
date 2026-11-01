import uuid
from datetime import timedelta
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.db import close_old_connections,connections
from django.test import TestCase,TransactionTestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from tasker.tests.test_improvement import fixtures,ImprovementTests,NOW
from tasker.improvement_models import SOPTemplate,SOPExecution,SOPVersion,RecurringWorkflow,RoutineOccurrence,WeeklyReview
from tasker.services.improvement import publish_sop,use_sop
from tasker.services.routines import generate,handle_generate,reconcile_routines,save_routine
from tasker.services.jobs import LostJobLease
from tasker.job_models import OutboxJob


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_RECURRENCE_ENABLED=True)
class ImprovementUITests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls)
    def setUp(self):self.client.force_login(self.worker)
    def sop(self,**changes):
        args=dict(user=self.worker,title='Private sample SOP',instructions='PRIVATE_SOP_BODY',definition_of_done='Verified evidence',
            steps=['Check first','Record result'],team=None,client_key=uuid.uuid4());args.update(changes);return publish_sop(**args)
    def test_workspace_lists_and_details_render_with_scoped_navigation(self):
        sop=self.sop()
        for route,args in [('improvement_home',[]),('sop_list',[]),('sop_detail',[sop.pk]),('sop_edit',[sop.pk]),
                ('routine_list',[]),('improvement_ideas',[]),('experiment_list',[]),('sop_create',[])]:
            response=self.client.get(reverse('ims:tasker:'+route,args=args));self.assertEqual(response.status_code,200,route)
            self.assertContains(response,'Weekly review');self.assertIn('no-store',response['Cache-Control'])
    def test_personal_review_history_renders_without_queryset_slice_error(self):
        from tasker.services.improvement_reviews import record_weekly_review
        day=timezone.localdate()-timedelta(days=timezone.localdate().weekday())
        record_weekly_review(user=self.worker,week_start=day,team=None,note='PRIVATE_WEEKLY_REFLECTION',client_key=uuid.uuid4())
        response=self.client.get(reverse('ims:tasker:improvement_home'));self.assertContains(response,'PRIVATE_WEEKLY_REFLECTION')
        self.client.force_login(self.manager)
        self.assertNotContains(self.client.get(reverse('ims:tasker:improvement_home')),'PRIVATE_WEEKLY_REFLECTION')
    def test_manager_cannot_open_private_sop_even_with_all_model_permissions(self):
        sop=self.sop();self.client.force_login(self.manager)
        self.assertEqual(self.client.get(reverse('ims:tasker:sop_detail',args=[sop.pk])).status_code,404)
    def test_feature_off_hides_navigation_and_denies_writes(self):
        with override_settings(TASKER_IMPROVEMENT_ENABLED=False):
            self.assertEqual(self.client.get(reverse('ims:tasker:improvement_home')).status_code,404)
            self.assertEqual(self.client.post(reverse('ims:tasker:sop_create'),{}).status_code,404)
    def test_csrf_and_get_cannot_execute_a_sop(self):
        from django.test import Client
        sop=self.sop();url=reverse('ims:tasker:sop_run',args=[sop.pk])
        self.assertEqual(self.client.get(url).status_code,405)
        c=Client(enforce_csrf_checks=True);c.force_login(self.worker);self.assertEqual(c.post(url,{}).status_code,403)
        self.assertFalse(SOPExecution.objects.exists())
    def test_stale_and_unknown_fields_retain_drafts_without_publication(self):
        sop=self.sop();url=reverse('ims:tasker:sop_edit',args=[sop.pk])
        data=dict(title='Retained proposed title',team='',instructions='Draft instructions',definition_of_done='Accepted output',
            steps='First\nSecond',expected_revision=uuid.uuid4(),client_key=uuid.uuid4())
        response=self.client.post(url,data);self.assertEqual(response.status_code,409);self.assertContains(response,'Retained proposed title',status_code=409)
        data.update(expected_revision=sop.revision,unexpected='x')
        response=self.client.post(url,data);self.assertEqual(response.status_code,400);self.assertContains(response,'Retained proposed title',status_code=400)
        self.assertEqual(sop.versions.count(),1)
    def test_sop_post_replay_redirects_to_same_task(self):
        sop=self.sop();url=reverse('ims:tasker:sop_run',args=[sop.pk]);data=dict(expected_revision=sop.revision,client_key=uuid.uuid4(),due_date='')
        a=self.client.post(url,data);b=self.client.post(url,data)
        self.assertEqual(a.status_code,302);self.assertEqual(a.url,b.url);self.assertEqual(SOPExecution.objects.count(),1)
    def test_scoped_readonly_admin_does_not_expose_private_versions_to_manager(self):
        from django.contrib import admin
        from django.test import RequestFactory
        sop=self.sop();request=RequestFactory().get('/admin/');request.user=self.manager
        for model in (SOPTemplate,SOPVersion,SOPExecution,WeeklyReview):
            model_admin=admin.site._registry[model]
            self.assertFalse(model_admin.get_queryset(request).exists());self.assertFalse(model_admin.has_change_permission(request))


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_RECURRENCE_ENABLED=True)
class RoutineJobTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls)
    sop=ImprovementTests.sop
    routine=ImprovementTests.routine
    def test_late_lease_loss_rolls_back_tasks_checklists_and_occurrence(self):
        row=self.routine()
        with patch('django.utils.timezone.now',return_value=NOW),patch('tasker.services.jobs.assert_current_job_lease'),patch(
                'tasker.services.jobs.fence_current_job_effects',side_effect=LostJobLease()):
            with self.assertRaises(LostJobLease):handle_generate({'routine_id':row.pk})
        self.assertFalse(row.occurrences.exists());self.assertFalse(SOPExecution.objects.exists())
    def test_reference_only_payload_and_failed_budget_are_preserved(self):
        row=self.routine()
        with patch('django.utils.timezone.now',return_value=NOW):
            reconcile_routines();job=OutboxJob.objects.get(kind='routine.generate');self.assertEqual(job.payload,{'routine_id':row.pk})
            OutboxJob.objects.filter(pk=job.pk).update(state='FAILED',attempts=5)
            reconcile_routines();reconcile_routines()
        self.assertEqual(OutboxJob.objects.filter(kind='routine.generate').count(),1);job.refresh_from_db();self.assertEqual(job.attempts,5)
    def test_expired_job_success_can_repair_without_duplicate_occurrence(self):
        row=self.routine()
        with patch('django.utils.timezone.now',return_value=NOW):
            reconcile_routines();job=OutboxJob.objects.get(kind='routine.generate');OutboxJob.objects.filter(pk=job.pk).update(state='SUCCEEDED')
            reconcile_routines();reconcile_routines()
            self.assertEqual(OutboxJob.objects.filter(kind='routine.generate').count(),2)
            generate(routine_id=row.pk,now=NOW);generate(routine_id=row.pk,now=NOW)
        self.assertEqual(row.occurrences.count(),1)
    def test_future_release_does_not_create_repair_job_flood(self):
        row=self.routine()
        with patch('django.utils.timezone.now',return_value=NOW.replace(hour=5)):
            reconcile_routines();reconcile_routines()
        self.assertFalse(OutboxJob.objects.filter(kind='routine.generate').exists())
    def test_edit_calendar_requires_explicit_rebase_and_keeps_prior_execution(self):
        row=self.routine();generate(routine_id=row.pk,now=NOW);receipt=row.occurrences.get()
        args=dict(user=self.manager,pk=row.pk,template=row.template,expected_revision=row.revision,client_key=uuid.uuid4(),
            owner_id=self.worker.pk,frequency='WEEKLY',interval=1,weekdays=[0],timezone_name='Africa/Harare',
            local_time=row.local_time,start_on=row.start_on,end_on=None,missed_policy='LATEST',overlap_policy='SKIP_OPEN',paused=True)
        from django.core.exceptions import ValidationError
        with patch('django.utils.timezone.now',return_value=NOW):
            with self.assertRaises(ValidationError):save_routine(**args)
            save_routine(**args,rebase=True)
        receipt.refresh_from_db();self.assertEqual(receipt.rule_snapshot['frequency'],'DAILY');self.assertIsNotNone(receipt.task_id)
    def test_dst_calendar_exception_has_skipped_receipt_and_advances(self):
        from datetime import date,time,datetime,timezone as utc
        row=self.routine(timezone_name='America/New_York',local_time=time(2,30),start_on=date(2027,3,14))
        result=generate(routine_id=row.pk,now=datetime(2027,3,14,9,tzinfo=utc.utc))
        self.assertEqual(result['skipped'],1);self.assertEqual(row.occurrences.get().reason_code,'calendar_exception')


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_STOCKTAKE_ADAPTER_ENABLED=True)
class WeeklyAgendaTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls)
    def test_resolved_and_reassigned_coordination_disappear_from_live_agenda(self):
        from tasker.models import Task,CoordinationRequest
        from tasker.services.coordination import create_request,resolve_request
        from tasker.services.workbench import work_version
        from tasker.services.improvement_reviews import weekly_facts
        task=Task.objects.create(owner=self.worker,team=self.team,company=self.company,title='Needs a decision')
        item=create_request(user=self.worker,task=task,kind='BLOCKER',resolver=self.manager,reason='Manager input',
            requested_action='Review specifications',expected_response_at=timezone.now()+timedelta(days=1),client_key=uuid.uuid4(),expected_task_version=work_version(task))
        self.assertEqual(len(weekly_facts(user=self.manager,team=self.team)['handoffs']),1)
        resolve_request(user=self.manager,request_id=item.pk,expected_revision=item.revision,resolution='Decision recorded')
        self.assertEqual(weekly_facts(user=self.manager,team=self.team)['handoffs'],[])
        CoordinationRequest.objects.filter(pk=item.pk).update(status='OPEN')
        Task.objects.filter(pk=task.pk).update(owner=self.manager)
        self.assertEqual(weekly_facts(user=self.manager,team=self.team)['handoffs'],[])
    def test_pending_goal_acceptance_disappears_when_accepted_or_task_scope_changes(self):
        from tasker.models import Goal,KeyResult,Task,Initiative
        from tasker.services.goals import record_outcome,accept_outcome,link_task_contribution
        from tasker.services.improvement_reviews import weekly_facts
        goal=Goal.objects.create(company=self.company,team=self.team,title='Quality outputs',status='ACTIVE',evidence_policy='Verified proof')
        result=KeyResult.objects.create(goal=goal,title='Accepted sample proofs',unit='proofs',baseline=0,target=100)
        task=Task.objects.create(owner=self.worker,team=self.team,company=self.company,title='Verified proof',status='COMPLETED')
        initiative=Initiative.objects.create(goal=goal,key_result=result,title='Improve proof quality')
        link_task_contribution(task=task,initiative=initiative,user=self.manager)
        outcome=record_outcome(user=self.worker,key_result=result,employee=self.worker,task=task,value=1,evidence='Synthetic reference',source_key='sample')
        self.assertEqual(len(weekly_facts(user=self.manager,team=self.team)['approvals']),1)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse('ims:tasker:improvement_home')+'?team='+str(self.team.pk)),'Awaiting outcome acceptance')
        Task.objects.filter(pk=task.pk).update(owner=self.manager)
        self.assertEqual(weekly_facts(user=self.manager,team=self.team)['approvals'],[])
        Task.objects.filter(pk=task.pk).update(owner=self.worker)
        accept_outcome(user=self.manager,outcome=outcome)
        self.assertEqual(weekly_facts(user=self.manager,team=self.team)['approvals'],[])
    def test_native_stocktake_approval_uses_current_source_and_permissions(self):
        from django.contrib.auth.models import Permission
        from inventory.models import Stocktake
        from operations.services import configure
        from tasker.services.business_links import reconcile_business_source
        from tasker.services.improvement_reviews import weekly_facts
        for user in (self.manager,self.worker):user.user_permissions.add(*Permission.objects.filter(content_type__app_label__in=('operations','inventory')))
        source=Stocktake.objects.create(name='Synthetic approval',status='REVIEW',created_by=self.manager)
        row=configure(user=self.manager,kind='stocktake_work',source_id=source.pk,company_id=self.company.pk,team_id=self.team.pk,
            assigned_to_id=self.worker.pk,enabled=True,next_review_on=timezone.localdate(),contact_interval_days=None,sla_due_on=None,expected_revision='new',client_key=uuid.uuid4())
        reconcile_business_source(source_type='stocktake_work',source_id=row.pk)
        self.assertTrue(any(item['kind']=='stocktake' for item in weekly_facts(user=self.manager,team=self.team)['approvals']))
        Stocktake.objects.filter(pk=source.pk).update(status='CLOSED')
        self.assertFalse(any(item['kind']=='stocktake' for item in weekly_facts(user=self.manager,team=self.team)['approvals']))


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_RECURRENCE_ENABLED=True)
class ImprovementConcurrencyTests(TransactionTestCase):
    def setUp(self):fixtures(self)
    sop=ImprovementTests.sop
    routine=ImprovementTests.routine
    def test_carry_get_does_not_require_a_transaction_or_lock_readers(self):
        from tasker.models import Task
        task=Task.objects.create(title='Scope-safe read',owner=self.worker,company=self.company,team=self.team)
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get(reverse('ims:tasker:carry_decision',args=[task.slug])).status_code,200)
    def race(self,fn):
        barrier=Barrier(2)
        def work(i):
            close_old_connections()
            try:barrier.wait(timeout=10);return fn(i)
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(work,range(2)))
    def test_two_first_occurrence_workers_create_one_task(self):
        row=self.routine()
        self.race(lambda i:generate(routine_id=row.pk,now=NOW))
        self.assertEqual(row.occurrences.filter(status='CREATED').count(),1);self.assertEqual(SOPExecution.objects.count(),1)
    def test_duplicate_sop_execution_requests_create_one_task(self):
        sop=self.sop();token=uuid.uuid4()
        result=self.race(lambda i:use_sop(user=self.worker,pk=sop.pk,expected_revision=sop.revision,client_key=token).pk)
        self.assertEqual(result[0],result[1]);self.assertEqual(SOPExecution.objects.count(),1)
