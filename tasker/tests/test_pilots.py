import json,uuid
from types import SimpleNamespace
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch,Mock
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError,transaction
from django.test import TestCase,TransactionTestCase,override_settings
from django.urls import reverse
from django.test.utils import CaptureQueriesContext
from django.db import connection
from django.contrib.auth.models import Permission
from tasker.models import BaselineObservation,TeamMembership
from tasker.pilot_models import Pilot,PilotMember,PilotObservation,PilotEvent,PilotValidation
from tasker.services import pilots
from tasker.services.pilot_metrics import pilot_report
from tasker.services.pilot_release import release_readiness,record_release_check,LOCAL_CHECKS,OPERATING_CHECKS,requirement_catalog
from tasker.services.pilot_access import feature_access
from .pilot_fixtures import fixtures,NOW,START,END


@override_settings(TASKER_PILOT_ENABLED=True)
class PilotTests(TestCase):
    @classmethod
    def setUpTestData(cls):fixtures(cls)

    def report(self,user=None):return pilot_report(user=user or self.manager,pilot=self.pilot)
    def control(self,action,**extra):
        self.pilot.refresh_from_db()
        return pilots.control_pilot(user=self.manager,pk=self.pilot.pk,expected_revision=self.pilot.revision,client_key=uuid.uuid4(),action=action,**extra)
    def test_complete_comparison_requires_independent_validation_receipt(self):
        r=self.report();self.assertEqual(r['productivity_gate'],'READY_FOR_INDEPENDENT_REVIEW')
        self.assertEqual(r['point_percent'],Decimal(105));self.assertGreater(r['interval']['low'],70)
        self.assertEqual(r['interval']['replicates'],1000)
        token=uuid.uuid4();args=dict(user=self.manager,pk=self.pilot.pk,expected_revision=self.pilot.revision,
            client_key=token,report_digest=r['report_digest'],reference='SYNTHETIC test review')
        pilots.validate_productivity(**args);pilots.validate_productivity(**args)
        self.assertEqual(PilotValidation.objects.count(),1)
        self.assertEqual(self.report()['productivity_gate'],'VALIDATED_UNDER_PROTOCOL')

    def test_baseline_retraction_invalidates_current_validation(self):
        r=self.report();pilots.validate_productivity(user=self.manager,pk=self.pilot.pk,expected_revision=self.pilot.revision,
            client_key=uuid.uuid4(),report_digest=r['report_digest'],reference='SYNTHETIC review')
        BaselineObservation.objects.filter(employee=self.worker).update(status='RETRACTED')
        r=self.report();self.assertIn('source_evidence_invalidated',r['blockers'])
        self.assertEqual(r['productivity_gate'],'PENDING_REAL_VALIDATION');self.assertIsNone(r['point_percent'])

    def test_missing_day_is_not_zero_or_removed_from_denominator(self):
        PilotObservation.objects.filter(employee=self.worker,phase='FOLLOWUP').first().delete()
        cell=self.report()['cells']['PILOT_FOLLOWUP']
        self.assertEqual((cell['expected'],cell['accepted'],cell['missing_days']),(80,79,1))
        self.assertIsNone(cell['metrics']['output_per_hour'])
        self.assertIsNone(self.report()['point_percent'])

    def test_unknown_and_declared_zero_are_different(self):
        PilotObservation.objects.filter(employee=self.worker,phase='FOLLOWUP').update(blocked_hours=None)
        r=self.report();self.assertIn('missing_or_incomplete_employee_days',r['blockers'])
        self.assertIsNone(r['cells']['PILOT_FOLLOWUP']['metrics']['blocked_hours'])
        self.assertEqual(r['cells']['COMPARISON_FOLLOWUP']['metrics']['blocked_hours'],0)

    def test_quality_overtime_and_admin_guardrails(self):
        PilotObservation.objects.filter(employee=self.worker,phase='FOLLOWUP').update(quality_passed=1,overtime_hours=1,admin_hours=2)
        r=self.report();self.assertIn('quality_rework_or_effort_regressed',r['blockers'])
        self.assertFalse(r['eligible_for_validation'])

    def test_contaminated_control_and_unknown_exposure_block_claim(self):
        PilotObservation.objects.filter(employee=self.workers[1],phase='FOLLOWUP').update(tasker_exposed=True)
        PilotObservation.objects.filter(employee=self.worker,phase='BASELINE').update(tasker_exposed=None)
        r=self.report();self.assertEqual(r['contamination'],40);self.assertIn('exposure_unknown_or_contaminated',r['blockers'])

    def test_target_is_strictly_greater_not_equal(self):
        PilotObservation.objects.filter(phase='FOLLOWUP',employee_id__in=[u.pk for u in self.workers[::2]]).update(accepted_output=68)
        r=self.report();self.assertEqual(r['point_percent'],70)
        self.assertIn('greater_than_target_not_supported_at_95_percent',r['blockers'])

    def test_synthetic_can_never_validate_or_release(self):
        Pilot.objects.filter(pk=self.pilot.pk).update(is_synthetic=True)
        self.pilot.refresh_from_db();r=self.report()
        self.assertEqual(r['productivity_gate'],'SYNTHETIC_NOT_EVIDENCE')
        with self.assertRaises(ValidationError):pilots.validate_productivity(user=self.manager,pk=self.pilot.pk,
            expected_revision=self.pilot.revision,client_key=uuid.uuid4(),report_digest=r['report_digest'],reference='invented')
        self.assertFalse(release_readiness(user=self.manager,pilot=self.pilot)['ready_for_real_pilot'])

    def test_protocol_and_baseline_drift_are_detected(self):
        self.study.output_definition='Changed output';self.study.save(update_fields=['output_definition'])
        self.assertIn('protocol_or_baseline_changed',self.report()['blockers'])
        self.pilot.title='Changed title'
        with self.assertRaises(ValidationError):self.pilot.full_clean()

    def test_actual_prior_rollout_authorization_is_required_for_real_validation(self):
        Pilot.objects.filter(pk=self.pilot.pk).update(rollout_authorized_at=None,rollout_authorized_by=None)
        self.assertIn('rollout_not_authorized_before_start',self.report()['blockers'])

    def test_report_window_is_not_complete_until_it_closes(self):
        with patch('django.utils.timezone.now',return_value=NOW-timedelta(days=35)):
            self.assertIn('observation_window_open',self.report()['blockers'])

    def test_accepted_baseline_core_is_reused_when_supplement_is_missing(self):
        PilotObservation.objects.filter(employee=self.worker,phase='BASELINE').delete()
        r=self.report();self.assertEqual(r['canonical_baseline_without_supplement'],20)
        self.assertEqual(r['canonical_summary'][0]['rate'],5)
        self.assertIn('missing_or_incomplete_employee_days',r['blockers'])

    def test_manager_loses_peer_evidence_when_baseline_permission_is_removed(self):
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label='tasker',codename='view_baselineobservation'))
        self.assertFalse(pilots.observations_for(self.manager,self.pilot).filter(phase='BASELINE').exists())
        self.assertIn('missing_or_incomplete_employee_days',self.report()['blockers'])

    def test_queries_are_bounded_for_320_employee_days(self):
        with CaptureQueriesContext(connection) as queries:r=self.report()
        self.assertEqual(len(r['rows']),320)
        self.assertLess(len(queries),90,len(queries))

    def test_posthoc_protocol_rejected_and_pair_roster_frozen(self):
        from .pilot_fixtures import START,END
        row=pilots.save_pilot(user=self.manager,study=self.study,client_key=uuid.uuid4(),
            title='Posthoc draft',owner=self.manager,is_synthetic=False,starts_on=START,ends_on=END,
            weekdays=[0,1,2,3,4],definitions=self.pilot.definitions,features=['core'])
        with self.assertRaisesMessage(ValidationError,'prospectively'):
            pilots.approve_protocol(user=self.manager,pk=row.pk,expected_revision=row.revision,client_key=uuid.uuid4())
        with self.assertRaisesMessage(ValidationError,'frozen'):
            pilots.add_member(user=self.manager,pk=self.pilot.pk,expected_revision=self.pilot.revision,
                client_key=uuid.uuid4(),employee=self.outsider,arm='PILOT',match_key='new',rollout_on=START)

    def test_baseline_core_override_and_nonfinite_effort_rejected(self):
        base=BaselineObservation.objects.filter(employee=self.worker).first()
        with self.assertRaises(ValidationError):
            PilotObservation(pilot=self.pilot,employee=self.worker,phase='BASELINE',baseline_observation=base,
                observed_on=base.observed_on,source_key='bad-override',staffed_hours=1).full_clean()
        with self.assertRaises(ValidationError):
            PilotObservation(pilot=self.pilot,employee=self.worker,phase='FOLLOWUP',
                observed_on=START,source_key='bad-hours',staffed_hours=Decimal('NaN')).full_clean()

    def test_withdrawal_retains_expected_denominator_and_disables_access(self):
        member=self.pilot.members.get(employee=self.worker)
        self.control('withdraw',member_id=member.pk,reason='SYNTHETIC withdrawal')
        member.refresh_from_db();self.assertFalse(member.access_enabled)
        r=self.report();self.assertEqual(r['cells']['PILOT_FOLLOWUP']['expected'],80)
        self.assertIn('cohort_withdrawal_requires_review',r['blockers'])

    def test_pause_resume_close_are_audited_and_closed_is_terminal(self):
        self.control('pause',reason='Investigate quality');self.pilot.refresh_from_db();self.assertEqual(self.pilot.state,'PAUSED')
        self.control('resume');self.control('close',reason='Observation complete')
        with self.assertRaises(ValidationError):self.control('pause',reason='Cannot reopen')
        self.assertEqual(PilotEvent.objects.filter(action='pilot.control').count(),3)

    def test_own_evidence_only_and_outsider_cannot_enumerate(self):
        r=self.report(self.worker)
        self.assertEqual(len(r['members']),1);self.assertEqual(len(r['rows']),40);self.assertIsNone(r['point_percent'])
        self.assertEqual(r['cells']['COMPARISON_BASELINE']['expected'],0)
        self.assertFalse(pilots.visible_pilots(self.outsider).exists())
        with self.assertRaises(Pilot.DoesNotExist):self.report(self.outsider)

    def test_current_membership_and_permission_rechecked(self):
        TeamMembership.objects.filter(team=self.team,user=self.worker).update(is_active=False)
        self.assertFalse(pilots.visible_pilots(self.worker).exists())
        self.manager.is_active=False;self.manager.save(update_fields=['is_active'])
        with self.assertRaises(PermissionDenied):self.report()

    def test_draft_measurements_replay_independent_review_and_stale_edit(self):
        old=PilotObservation.objects.filter(employee=self.worker,phase='FOLLOWUP').first()
        old.status='RETRACTED';old.save(update_fields=['status'])
        token=uuid.uuid4();args=dict(user=self.worker,pk=self.pilot.pk,client_key=token,employee=self.worker,phase='FOLLOWUP',
            observed_on=old.observed_on,source_key='corrected-source',staffed_hours=Decimal(8),accepted_output=Decimal(76),evidence='Corrected independent register')
        row=pilots.save_observation(**args)
        self.assertEqual(pilots.save_observation(**args).pk,row.pk)
        with self.assertRaises(ValidationError):pilots.save_observation(**{**args,'evidence':'different'})
        with self.assertRaises(PermissionDenied):pilots.review_observation(user=self.worker,pk=self.pilot.pk,observation_id=row.pk,
            expected_revision=row.revision,client_key=uuid.uuid4(),action='accept')
        oldrev=row.revision
        pilots.review_observation(user=self.manager,pk=self.pilot.pk,observation_id=row.pk,expected_revision=row.revision,client_key=uuid.uuid4(),action='accept')
        with self.assertRaises(ValidationError):pilots.review_observation(user=self.manager,pk=self.pilot.pk,observation_id=row.pk,
            expected_revision=oldrev,client_key=uuid.uuid4(),action='retract',reason='stale')

    def test_linked_source_changes_on_accepted_rows_are_blocked(self):
        row=PilotObservation.objects.filter(phase='FOLLOWUP').first()
        with self.assertRaises(ValidationError):row.outcomes.clear()

    def test_database_prevents_duplicate_employee_days_and_missing_acceptor(self):
        row=PilotObservation.objects.filter(phase='FOLLOWUP').first()
        with self.assertRaises(IntegrityError),transaction.atomic():PilotObservation.objects.create(pilot=self.pilot,employee=row.employee,
            phase='FOLLOWUP',observed_on=row.observed_on,source_key='duplicate')
        with self.assertRaises(IntegrityError),transaction.atomic():PilotObservation.objects.create(pilot=self.pilot,employee=row.employee,
            phase='FOLLOWUP',observed_on=END+timedelta(days=1),source_key='no-audit',status='ACCEPTED')

    def test_read_only_release_gates_and_conditional_ai(self):
        before=PilotEvent.objects.count()
        r=release_readiness(user=self.manager,pilot=self.pilot)
        self.assertFalse(r['ready_for_real_pilot']);self.assertEqual(PilotEvent.objects.count(),before)
        self.assertEqual(next(v for v in r['operating'] if v['key']=='ai_evaluation')['status'],'DEFERRED')
        with override_settings(TASKER_ROLLOUT_WRITES_ENABLED=False):
            with self.assertRaises(ValidationError):self.control('authorize_rollout')

    def test_operating_checks_idempotent_immutable_and_scoped(self):
        args=dict(user=self.manager,pk=self.pilot.pk,client_key=uuid.uuid4(),check_key='staff_usability',status='PENDING',reference='Real staff sessions pending')
        with patch('tasker.services.pilot_release.source_fingerprint',return_value=('a'*64,1)):
            record_release_check(**args);record_release_check(**args)
        row=self.pilot.release_evidence.get()
        with self.assertRaises(ValidationError):row.save()
        with self.assertRaises(PermissionDenied):record_release_check(**{**args,'user':self.worker,'client_key':uuid.uuid4()})

    def test_real_readiness_requires_every_current_operating_check_and_both_heartbeats(self):
        from tasker.pilot_models import PilotReleaseEvidence
        from tasker.job_models import JobWorkerHeartbeat
        local={'current':True,'source_digest':'b'*64,'checks':{v:'VERIFIED' for v in LOCAL_CHECKS}}
        PilotReleaseEvidence.objects.bulk_create([PilotReleaseEvidence(pilot=self.pilot,check_key=key,status='VERIFIED',
            reference='SYNTHETIC test operating evidence',source_digest='b'*64,recorded_by=self.manager) for key in OPERATING_CHECKS if key!='ai_evaluation'])
        with patch('tasker.services.pilot_release.local_evidence',return_value=local):
            self.assertFalse(release_readiness(user=self.manager,pilot=self.pilot)['ready_for_real_pilot'])
            JobWorkerHeartbeat.objects.create(name='pilot-worker',role='worker')
            JobWorkerHeartbeat.objects.create(name='pilot-scheduler',role='scheduler')
            self.assertTrue(release_readiness(user=self.manager,pilot=self.pilot)['ready_for_real_pilot'])
            PilotReleaseEvidence.objects.create(pilot=self.pilot,check_key='staff_usability',status='DEFERRED',
                reference='Unresolved mandatory staff check',source_digest='b'*64,recorded_by=self.manager)
            self.assertFalse(release_readiness(user=self.manager,pilot=self.pilot)['ready_for_real_pilot'])

    def test_every_matrix_identifier_is_in_release_catalog(self):
        catalog=requirement_catalog();ids=[v['id'] for v in catalog]
        self.assertEqual(len(ids),len(set(ids)))
        for required in ('T13-01','T13-10','X01','BIZ-R03'):self.assertIn(required,ids)

    def test_dashboard_export_and_draft_error_retention(self):
        self.client.force_login(self.manager)
        for name in ('pilot_list','pilot_detail','pilot_release','pilot_export'):
            url=reverse('ims:tasker:'+name,kwargs={} if name=='pilot_list' else {'pk':self.pilot.pk})
            response=self.client.get(url);self.assertEqual(response.status_code,200,(name,response.content[:400]))
            self.assertIn('no-store',response['Cache-Control'])
        self.client.force_login(self.worker)
        response=self.client.get(reverse('ims:tasker:pilot_export',args=[self.pilot.pk]))
        self.assertNotIn(f'follow-{self.workers[1].pk}-'.encode(),response.content)
        self.assertEqual(self.client.get(reverse('ims:tasker:pilot_release',args=[self.pilot.pk])).status_code,403)
        response=self.client.post(reverse('ims:tasker:pilot_observation_create',args=[self.pilot.pk]),{'evidence':'Retain my draft sentence'})
        self.assertEqual(response.status_code,400);self.assertContains(response,'Retain my draft sentence',status_code=400)
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('ims:tasker:pilot_detail',args=[self.pilot.pk])).status_code,404)

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True)
    def test_cohort_flags_deny_unknown_comparison_and_ai_and_preserve_measurement(self):
        self.assertFalse(feature_access(self.worker));self.assertFalse(feature_access(self.outsider))
        self.assertTrue(feature_access(self.manager));self.assertTrue(feature_access(self.worker,'measurement'))
        Pilot.objects.filter(pk=self.pilot.pk).update(rollout_authorized_at=NOW,rollout_authorized_by=self.manager)
        self.pilot.members.filter(employee__in=[self.worker,self.workers[1]]).update(access_enabled=True)
        with patch('django.utils.timezone.now',return_value=NOW-timedelta(days=35)):
            self.assertTrue(feature_access(self.worker));self.assertFalse(feature_access(self.worker,'ai'))
            self.assertFalse(feature_access(self.workers[1]))
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(reverse('ims:tasker:task_list')).status_code,403)
        self.assertEqual(self.client.get(reverse('ims:tasker:pilot_list')).status_code,200)

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True)
    def test_ai_gateway_denies_before_provider_or_budget(self):
        from tasker.ai.service import AIService
        from tasker.ai.exceptions import AIPermissionError
        from tasker.models import AIConfiguration
        AIConfiguration.objects.update(is_active=False)
        configuration=AIConfiguration.objects.create(name='SYNTHETIC',is_active=True,is_enabled=True)
        service=AIService(provider_factory=Mock(side_effect=AssertionError('Provider must not be constructed')))
        with self.assertRaises(AIPermissionError):service.generate_text(user=self.worker,operation='pilot-test',prompt_key='task_summary',variables={})

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True,TASKER_IMPROVEMENT_ENABLED=True,TASKER_STRATEGY_ENABLED=True)
    def test_unselected_embedded_modules_and_now_are_not_exposed(self):
        self.pilot.members.filter(employee=self.worker).update(access_enabled=True)
        self.client.force_login(self.worker)
        response=self.client.get(reverse('ims:tasker:task_list'))
        self.assertEqual(response.status_code,200)
        self.assertFalse(response.context['tasker_improvement_enabled'])
        self.assertFalse(response.context['tasker_strategy_enabled'])
        keys=[v['key'] for v in response.context['tasker_primary_navigation']]
        self.assertNotIn('now',keys);self.assertNotIn('improve',keys)
        self.assertEqual(self.client.get(reverse('ims:tasker:index')).status_code,403)
        self.assertEqual(self.client.get(reverse('ims:tasker:improvement_home')).status_code,403)

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True)
    def test_closed_or_paused_pilot_cannot_supply_a_feature_grant(self):
        self.pilot.members.filter(employee=self.worker).update(access_enabled=True)
        self.assertTrue(feature_access(self.worker))
        self.control('pause',reason='Quality review')
        self.assertFalse(feature_access(self.worker))
        self.assertTrue(feature_access(self.worker,'measurement'))
        with override_settings(TASKER_PILOT_ACCESS_ENFORCED=False):self.assertTrue(feature_access(self.worker))

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True)
    def test_legacy_ai_retry_rechecks_release_before_dispatching_a_hold(self):
        from tasker.ai.service import AIService
        from tasker.ai.exceptions import AIPermissionError
        budget=Mock();limiter=Mock();guard=Mock()
        service=AIService(rate_limiter=limiter,money_budget=budget)
        with self.assertRaises(AIPermissionError):
            service._begin_attempt(SimpleNamespace(user=self.worker,before_provider_attempt=guard))
        budget.begin_attempt.assert_not_called();limiter.begin_attempt.assert_not_called();guard.assert_not_called()

    @override_settings(TASKER_PILOT_ACCESS_ENFORCED=True,TASKER_IMPROVEMENT_ENABLED=True,TASKER_RECURRENCE_ENABLED=True)
    @patch('django.utils.timezone.now',return_value=NOW)
    def test_unreleased_recurring_job_preserves_cursor_and_creates_no_task(self,clock):
        from tasker.services.improvement import publish_sop
        from tasker.services.routines import save_routine,generate
        from tasker.models import Task
        from datetime import time
        sop=publish_sop(user=self.manager,client_key=uuid.uuid4(),team=self.team,title='SYNTHETIC recurring check',
            instructions='Review accepted evidence',definition_of_done='Source review completed',steps=['Review'])
        row=save_routine(user=self.manager,template=sop,client_key=uuid.uuid4(),owner_id=self.worker.pk,
            frequency='DAILY',interval=1,weekdays=[],timezone_name='Africa/Harare',local_time=time(9),
            start_on=NOW.date(),end_on=None,missed_policy='LATEST',overlap_policy='SKIP_OPEN',paused=False)
        cursor=row.next_on;before=Task.objects.count()
        self.assertEqual(generate(routine_id=row.pk,now=NOW),{'cohort_held':1})
        row.refresh_from_db();self.assertEqual(row.next_on,cursor)
        self.assertEqual(Task.objects.count(),before);self.assertFalse(row.occurrences.exists())
