import uuid
from datetime import date,datetime,time,timedelta,timezone as dt_timezone
from decimal import Decimal
from unittest.mock import Mock,patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase,TransactionTestCase,override_settings
from django.utils import timezone

from accounting.models import Company
from tasker.models import Task,Team,TeamMembership,OrganizationAccess,DailyPlan,DailyReview,TaskChecklistItem
from tasker.improvement_models import *
from tasker.services.improvement import publish_sop,use_sop,archive_sop,templates_for,carry_candidates,decide_carry
from tasker.services.routines import save_routine,generate,scheduled_at,set_paused,skip_date,reconcile_routines
from tasker.services.improvement_reviews import promote_experiment,record_result,accept_result,idea_review,weekly_facts,record_weekly_review
from tasker.services.workbench import work_version
from tasker.services.captures import create_capture,mutate_capture

NOW=datetime(2026,11,4,10,tzinfo=dt_timezone.utc)


def fixtures(obj):
    obj.manager=get_user_model().objects.create_user('improve-manager',email='manager@example.invalid')
    obj.worker=get_user_model().objects.create_user('improve-worker')
    obj.other=get_user_model().objects.create_user('improve-outsider')
    for user in (obj.manager,obj.worker,obj.other):user.user_permissions.add(*Permission.objects.filter(content_type__app_label='tasker'))
    obj.company=Company.objects.create(name='Synthetic improvement company')
    obj.team=Team.objects.create(company=obj.company,name='Improvement team')
    TeamMembership.objects.create(team=obj.team,user=obj.manager,role='MANAGER')
    TeamMembership.objects.create(team=obj.team,user=obj.worker)
    OrganizationAccess.objects.create(company=obj.company,user=obj.manager,can_manage=True,can_view_all=True,can_receive_reports=True)


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_RECURRENCE_ENABLED=True)
class ImprovementTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls)

    def sop(self,**changes):
        values=dict(user=self.manager,client_key=uuid.uuid4(),title='Sample quality routine',instructions='Authorized work instructions',
            definition_of_done='Review every checklist item and record the verified output.',steps=['Inspect work','Record evidence'],team=self.team)
        values.update(changes);return publish_sop(**values)

    def routine(self,sop=None,**changes):
        sop=sop or self.sop()
        values=dict(user=self.manager,template=sop,client_key=uuid.uuid4(),owner_id=self.worker.pk,frequency='DAILY',interval=1,
            weekdays=[],timezone_name='Africa/Harare',local_time=time(9),start_on=NOW.date(),end_on=None,
            missed_policy='LATEST',overlap_policy='SKIP_OPEN',paused=False)
        values.update(changes)
        with patch('django.utils.timezone.now',return_value=NOW-timedelta(days=1)):
            row=save_routine(**values)
        SOPVersion.objects.filter(template=sop).update(published_at=NOW-timedelta(days=10))
        return row

    def idea(self,**changes):
        values=dict(user=self.worker,client_key=uuid.uuid4(),kind='IDEA',title='Faster printing setup',
            content='PRIVATE_IDEA_SENTINEL: improve printing setup with a standard checklist.',tags=['printing'])
        values.update(changes);return create_capture(**values)

    def experiment(self,capture=None,**changes):
        capture=capture or self.idea()
        values=dict(user=self.worker,capture_id=capture.pk,version=capture.version,client_key=uuid.uuid4(),owner=self.worker,
            team=None,title='Test a setup checklist',expected_benefit='Reduce setup minutes',unit='minutes',baseline=Decimal(30),
            target=Decimal(20),direction='LOWER',review_on=timezone.localdate()+timedelta(days=7))
        values.update(changes);return promote_experiment(**values)

    def test_sop_publication_and_execution_replay_preserve_versions_and_create_one_task(self):
        token=uuid.uuid4();sop=self.sop(client_key=token)
        self.assertEqual(self.sop(client_key=token).pk,sop.pk)
        execute=uuid.uuid4()
        task=use_sop(user=self.worker,pk=sop.pk,expected_revision=sop.revision,client_key=execute)
        self.assertEqual(use_sop(user=self.worker,pk=sop.pk,expected_revision=sop.revision,client_key=execute).pk,task.pk)
        self.assertEqual(task.checklist_items.count(),2)
        updated=self.sop(pk=sop.pk,expected_revision=sop.revision,title='Revised work',steps=['New future step'])
        task.refresh_from_db();self.assertEqual(task.title,'Sample quality routine')
        self.assertEqual(task.sop_execution.version.number,1)
        self.assertEqual(updated.versions.count(),2)

    def test_private_sop_and_reviews_do_not_become_manager_content(self):
        sop=self.sop(user=self.worker,team=None,instructions='PRIVATE_SOP_SENTINEL')
        self.assertFalse(templates_for(self.manager).filter(pk=sop.pk).exists())
        review=record_weekly_review(user=self.worker,week_start=timezone.localdate()-timedelta(days=timezone.localdate().weekday()),
            team=None,note='PRIVATE_WEEKLY_SENTINEL',client_key=uuid.uuid4())
        from tasker.services.improvement import scoped_records
        self.assertFalse(scoped_records(WeeklyReview,self.manager).filter(pk=review.pk).exists())
        with self.assertRaises(PermissionDenied):self.sop(user=self.worker,team=self.team)

    def test_recurring_generation_is_replay_safe_and_keeps_historical_sop(self):
        row=self.routine();first=generate(routine_id=row.pk,now=NOW)
        self.assertEqual(first['created'],1)
        self.assertEqual(generate(routine_id=row.pk,now=NOW)['created'],0)
        occurrence=row.occurrences.get();self.assertEqual(occurrence.version.number,1)
        self.assertEqual(occurrence.task.owner_id,self.worker.pk)
        self.assertEqual(occurrence.task.team_id,self.team.pk)
        self.assertEqual(occurrence.task.status,'PENDING')
        self.assertEqual(Task.objects.count(),1)

    def test_template_changes_apply_only_to_future_due_instants(self):
        row=self.routine();generate(routine_id=row.pk,now=NOW)
        original=row.occurrences.get().task
        Task.objects.filter(pk=original.pk).update(status='COMPLETED')
        template=row.template
        with patch('django.utils.timezone.now',return_value=NOW+timedelta(hours=2)):
            self.sop(pk=template.pk,expected_revision=template.revision,steps=['Future version'])
        SOPVersion.objects.filter(template=template,number=2).update(published_at=NOW+timedelta(hours=2))
        generate(routine_id=row.pk,now=NOW+timedelta(days=1))
        self.assertEqual(row.occurrences.order_by('scheduled_on').last().version.number,2)
        original.refresh_from_db();self.assertEqual(original.checklist_items.count(),2)

    def test_overlap_policy_records_skip_instead_of_endlessly_rolling_open_work(self):
        row=self.routine();generate(routine_id=row.pk,now=NOW)
        second=generate(routine_id=row.pk,now=NOW+timedelta(days=1))
        self.assertEqual(second['created'],0)
        self.assertEqual(row.occurrences.first().reason_code,'prior_work_open')

    def test_pause_resume_exception_and_scope_loss_hold_generation(self):
        row=self.routine()
        set_paused(user=self.manager,pk=row.pk,expected_revision=row.revision,client_key=uuid.uuid4(),paused=True)
        self.assertEqual(generate(routine_id=row.pk,now=NOW),{'paused':1})
        row.refresh_from_db();set_paused(user=self.manager,pk=row.pk,expected_revision=row.revision,client_key=uuid.uuid4(),paused=False)
        row.refresh_from_db()
        with patch('django.utils.timezone.now',return_value=NOW-timedelta(hours=5)):
            skip_date(user=self.manager,pk=row.pk,expected_revision=row.revision,client_key=uuid.uuid4(),scheduled_on=NOW.date(),reason='Company holiday')
        self.assertEqual(generate(routine_id=row.pk,now=NOW)['skipped'],1)
        TeamMembership.objects.filter(team=self.team,user=self.worker).update(is_active=False)
        self.assertEqual(generate(routine_id=row.pk,now=NOW+timedelta(days=1)),{'held':1})
        row.refresh_from_db();self.assertTrue(row.paused and row.needs_review)

    def test_latest_policy_collapses_long_missed_ranges_without_a_task_flood(self):
        row=self.routine(overlap_policy='ALLOW')
        result=generate(routine_id=row.pk,now=NOW+timedelta(days=100))
        self.assertEqual(result['created'],1)
        self.assertEqual(row.occurrences.count(),2)
        self.assertEqual(Task.objects.count(),1)

    def test_all_policy_bounds_each_job_and_skip_policy_does_not_backfill_tasks(self):
        row=self.routine(missed_policy='ALL',overlap_policy='ALLOW')
        self.assertEqual(generate(routine_id=row.pk,now=NOW+timedelta(days=40))['created'],20)
        other=self.routine(missed_policy='SKIP',overlap_policy='ALLOW')
        self.assertEqual(generate(routine_id=other.pk,now=NOW+timedelta(days=40))['created'],1)

    def test_calendar_month_end_and_dst_ambiguities_are_explicit(self):
        row=self.routine(frequency='MONTHLY',start_on=date(2027,1,31))
        from tasker.services.routines import next_date
        self.assertEqual(next_date(row,date(2027,2,1)),date(2027,2,28))
        row.timezone_name='America/New_York';row.local_time=time(2,30)
        with self.assertRaises(ValidationError):scheduled_at(row,date(2027,3,14))
        row.local_time=time(1,30)
        with self.assertRaises(ValidationError):scheduled_at(row,date(2027,11,7))

    def test_disabled_generation_has_no_domain_effects(self):
        row=self.routine()
        with override_settings(TASKER_RECURRENCE_ENABLED=False):
            self.assertEqual(generate(routine_id=row.pk,now=NOW),{'disabled':1})
            self.assertEqual(reconcile_routines(),{'queued':0})
        self.assertEqual(Task.objects.count(),0)

    def test_repeated_carry_over_requires_a_fresh_explicit_decision(self):
        task=Task.objects.create(owner=self.worker,title='Repeated work',due_date=timezone.localdate())
        for days in (1,2):
            plan=DailyPlan.objects.create(owner=self.worker,plan_date=timezone.localdate()-timedelta(days=days));plan.planned_tasks.add(task)
        self.assertTrue(carry_candidates(self.worker).filter(pk=task.pk).exists())
        token=uuid.uuid4();version=work_version(task)
        decision=decide_carry(user=self.worker,task_id=task.pk,expected_version=version,client_key=token,
            action='CLARIFY',reason='Make the output testable',definition_of_done='Customer approves the proof')
        self.assertEqual(decide_carry(user=self.worker,task_id=task.pk,expected_version=version,client_key=token,
            action='CLARIFY',reason='Make the output testable',definition_of_done='Customer approves the proof').pk,decision.pk)
        self.assertFalse(carry_candidates(self.worker).filter(pk=task.pk).exists())

    def test_carry_split_reschedule_stop_and_unauthorized_delegation(self):
        task=Task.objects.create(owner=self.worker,company=self.company,team=self.team,title='Big work')
        split=decide_carry(user=self.worker,task_id=task.pk,expected_version=work_version(task),client_key=uuid.uuid4(),
            action='SPLIT',reason='Make one small deliverable',title='First step',definition_of_done='Reviewed proof')
        self.assertEqual(split.result_task.parent_task_id,task.pk)
        task.refresh_from_db()
        decide_carry(user=self.worker,task_id=task.pk,expected_version=work_version(task),client_key=uuid.uuid4(),
            action='RESCHEDULE',reason='Supplier available tomorrow',due_date=timezone.localdate()+timedelta(days=1))
        task.refresh_from_db()
        with self.assertRaises((PermissionDenied,ValidationError)):
            decide_carry(user=self.worker,task_id=task.pk,expected_version=work_version(task),client_key=uuid.uuid4(),
                action='DELEGATE',reason='Move to peer',assignee=self.other)
        decide_carry(user=self.worker,task_id=task.pk,expected_version=work_version(task),client_key=uuid.uuid4(),action='STOP',reason='No longer useful')
        task.refresh_from_db();self.assertEqual(task.status,'CANCELLED')

    def test_idea_suggestions_use_only_owned_or_explicitly_shared_ideas(self):
        self.idea();self.idea(title='Printing setup checklist')
        self.assertTrue(idea_review(user=self.worker)['suggestions'])
        self.assertEqual(idea_review(user=self.manager,team=self.team)['total'],0)

    def test_private_idea_body_and_original_url_are_not_copied_into_experiment_work(self):
        capture=self.idea(url='https://example.invalid/private-original')
        trial=self.experiment(capture)
        self.assertNotIn('PRIVATE_IDEA_SENTINEL',trial.task.description)
        self.assertNotIn('private-original',trial.task.description)
        from tasker.services.improvement import experiments_for
        self.assertFalse(experiments_for(self.manager).filter(pk=trial.pk).exists())
        self.assertEqual(capture.experiment.pk,trial.pk)

    def test_team_experiment_requires_deliberate_sharing_and_results_need_current_completion(self):
        capture=self.idea()
        with self.assertRaises(ValidationError):self.experiment(capture,team=self.team)
        capture=mutate_capture(user=self.worker,pk=capture.pk,version=capture.version,action='share',team=self.team)
        trial=self.experiment(capture,team=self.team)
        with self.assertRaises(ValidationError):
            record_result(user=self.worker,pk=trial.pk,expected_revision=trial.revision,client_key=uuid.uuid4(),value=18,evidence='Observed three setups')
        Task.objects.filter(pk=trial.task_id).update(status='COMPLETED',completed_at=timezone.now())
        result=record_result(user=self.worker,pk=trial.pk,expected_revision=trial.revision,client_key=uuid.uuid4(),value=18,evidence='Observed three setups')
        with self.assertRaises(PermissionDenied):accept_result(user=self.worker,pk=trial.pk,result_id=result.pk,expected_revision=trial.revision,client_key=uuid.uuid4())
        accepted=accept_result(user=self.manager,pk=trial.pk,result_id=result.pk,expected_revision=trial.revision,client_key=uuid.uuid4())
        self.assertIsNotNone(accepted.accepted_at)
        Task.objects.filter(pk=trial.task_id).update(status='PENDING')
        from tasker.services.improvement_reviews import result_current
        accepted=ExperimentResult.objects.select_related('experiment__task').get(pk=accepted.pk)
        self.assertFalse(result_current(accepted))

    def test_weekly_review_does_not_include_private_reflections_or_private_ideas(self):
        plan=DailyPlan.objects.create(owner=self.worker)
        DailyReview.objects.create(plan=plan,lessons_learned='PRIVATE_REFLECTION_SENTINEL')
        self.idea()
        facts=weekly_facts(user=self.manager,team=self.team)
        self.assertNotIn('PRIVATE_REFLECTION_SENTINEL',str(facts))
        self.assertNotIn('PRIVATE_IDEA_SENTINEL',str(facts))

    def test_delegated_experiment_requires_manager_alignment_and_new_evidence(self):
        from tasker.services.improvement_reviews import sync_experiment_owner,result_current
        from tasker.services.workbench import apply_work_actions
        capture=self.idea();capture=mutate_capture(user=self.worker,pk=capture.pk,version=capture.version,action='share',team=self.team)
        trial=self.experiment(capture,team=self.team)
        Task.objects.filter(pk=trial.task_id).update(status='COMPLETED',completed_at=timezone.now())
        result=record_result(user=self.worker,pk=trial.pk,expected_revision=trial.revision,client_key=uuid.uuid4(),value=18,evidence='Sample measurement')
        task=Task.objects.get(pk=trial.task_id)
        task=apply_work_actions(user=self.manager,task_ids=[task.pk],action='assign',value=self.manager.pk,versions={task.pk:work_version(task)})[0]
        with self.assertRaises(PermissionDenied):
            sync_experiment_owner(user=self.worker,pk=trial.pk,expected_revision=trial.revision,expected_task_version=work_version(task),client_key=uuid.uuid4())
        trial=sync_experiment_owner(user=self.manager,pk=trial.pk,expected_revision=trial.revision,expected_task_version=work_version(task),client_key=uuid.uuid4())
        self.assertEqual(trial.owner_id,self.manager.pk)
        result=ExperimentResult.objects.select_related('experiment__task').get(pk=result.pk)
        self.assertFalse(result_current(result))
