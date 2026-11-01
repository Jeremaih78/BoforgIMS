"""Explicit close, guarded real-transport boundary and private correction proofs."""
from datetime import time,timedelta
import json,uuid
from unittest.mock import Mock,patch
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.test import TestCase,TransactionTestCase,override_settings
from django.urls import reverse
from tasker.tests.test_improvement import fixtures,NOW
from tasker.models import DailyPlan,DailyReview,Task,TaskTimeEntry
from tasker.forms import DailyReviewForm
from tasker.improvement_models import DayClosure,DayRevision,ImprovementEvent
from tasker.notification_models import NotificationDelivery,NotificationPreference
from tasker.report_models import EndDaySubmission,ReportPolicy
from tasker.services.day_closure import change_day,review_version,require_open_focus
from tasker.services.planner import plan_version,save_daily_review
from tasker.services.reporting import save_report_policy,build_team_report,render_report_email
from tasker.services.report_email import handle_smtp_delivery,SMTPReportEmailAdapter
from tasker.services.jobs import JobCancelled,JobDeferred


def setup_day(obj):
    fixtures(obj)
    obj.plan=DailyPlan.objects.create(owner=obj.worker,plan_date=NOW.date())
    obj.review=DailyReview.objects.create(plan=obj.plan,accomplishments='PRIVATE_REFLECTION',energy_level=3)
    obj.task=Task.objects.create(owner=obj.worker,team=obj.team,company=obj.company,title='Shared next step')
    obj.plan.planned_tasks.add(obj.task)
    NotificationPreference.objects.create(owner=obj.manager,respect_work_hours=False,suppress_during_focus=False)


class DayHelpers:
    def declare(self,closed=True,**changes):
        self.plan.refresh_from_db();self.review.refresh_from_db()
        closure=DayClosure.objects.filter(plan=self.plan).first()
        args=dict(user=self.worker,plan_id=self.plan.pk,expected_plan_version=plan_version(self.plan),
            expected_review_version=review_version(self.review),expected_revision=str(closure.revision) if closure else 'new',
            client_key=uuid.uuid4(),closed=closed)
        args.update(changes);return change_day(**args)
    def policy(self,**changes):
        old=ReportPolicy.objects.filter(company=self.company).first()
        args=dict(user=self.manager,company=self.company,expected_revision=str(old.revision) if old else 'unconfigured-v1',
            cutoff_time=time(17),consolidation='PER_SUBMISSION',correction_policy='APPEND_REVISION',
            missing_policy='SHOW_MISSING',weekdays=list(range(7)),email_mode='SMTP',email_enabled=True)
        args.update(changes);return save_report_policy(**args)
    def delivery(self):
        self.policy();self.declare()
        report=build_team_report(user=self.manager,team=self.team,report_date=NOW.date())
        return NotificationDelivery.objects.get(report_snapshot=report,recipient=self.manager,channel='EMAIL')
    def review_form(self,**changes):
        closure=DayClosure.objects.filter(plan=self.plan).first()
        values=dict(accomplishments='PRIVATE_CORRECTION',energy_level=3,most_productive_time='',biggest_distraction='',
            lessons_learned='',tomorrow_focus='',review_version=review_version(self.review),review_client_key=uuid.uuid4(),
            close_revision=str(closure.revision) if closure else 'new',correction_reason='')
        values.update(changes)
        form=DailyReviewForm(values,instance=self.review,user=self.worker,plan=self.plan)
        self.assertTrue(form.is_valid(),form.errors);return form


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_REPORTS_ENABLED=True,TASKER_REPORT_EMAIL_LIVE_ENABLED=False)
class ClosureTests(DayHelpers,TestCase):
    def setUp(self):
        self.clock=patch('django.utils.timezone.now',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop)
        setup_day(self)
    def test_saving_review_is_private_draft_until_explicit_close(self):
        form=self.review_form();save_daily_review(form=form,user=self.worker,plan=self.plan)
        self.assertFalse(EndDaySubmission.objects.exists());self.assertFalse(DayClosure.objects.exists())
        self.review.refresh_from_db();self.declare()
        self.assertEqual(EndDaySubmission.objects.count(),1)
    def test_close_replay_and_second_click_preserve_one_declaration_and_submission(self):
        args=dict(expected_plan_version=plan_version(self.plan),expected_review_version=review_version(self.review),expected_revision='new',client_key=uuid.uuid4())
        first=self.declare(**args);self.assertEqual(self.declare(**args).pk,first.pk)
        self.declare();self.assertEqual(DayRevision.objects.count(),1);self.assertEqual(EndDaySubmission.objects.count(),1)
    def test_close_requires_current_plan_and_review_and_own_current_day(self):
        with self.assertRaises(ValidationError):self.declare(expected_plan_version='stale')
        with self.assertRaises(ValidationError):self.declare(expected_review_version='stale')
        with self.assertRaises(PermissionDenied):self.declare(user=self.other)
        DailyPlan.objects.filter(pk=self.plan.pk).update(plan_date=NOW.date()-timedelta(days=1))
        with self.assertRaises(PermissionDenied):self.declare()
    def test_running_focus_requires_explicit_stop_and_closed_day_blocks_new_focus(self):
        entry=TaskTimeEntry.objects.create(task=self.task,user=self.worker,started_at=NOW-timedelta(minutes=10))
        with self.assertRaises(ValidationError):self.declare()
        self.declare(stop_focus=True);entry.refresh_from_db();self.assertIsNotNone(entry.ended_at)
        with self.assertRaisesMessage(ValueError,'Reopen'):require_open_focus(self.worker)
        self.declare(False,reason='PRIVATE_REOPEN_REASON');require_open_focus(self.worker)
    def test_reopen_appends_factual_revision_without_private_reason(self):
        self.declare()
        with self.assertRaises(ValidationError):self.declare(False)
        self.declare(False,reason='PRIVATE_REOPEN_REASON')
        self.assertEqual(DayRevision.objects.count(),2);self.assertEqual(EndDaySubmission.objects.count(),2)
        report=build_team_report(user=self.manager,team=self.team,report_date=NOW.date())
        facts=json.dumps([report.facts,render_report_email(report),list(EndDaySubmission.objects.values_list('facts',flat=True))])
        self.assertIn('REOPENED',facts);self.assertNotIn('PRIVATE_',facts)
    def test_closed_review_correction_requires_reason_and_retains_versions(self):
        self.declare()
        with self.assertRaises(ValidationError):save_daily_review(form=self.review_form(),user=self.worker,plan=self.plan)
        self.review.refresh_from_db()
        form=self.review_form(correction_reason='PRIVATE_CORRECTION_REASON');save_daily_review(form=form,user=self.worker,plan=self.plan)
        self.assertEqual(DayRevision.objects.filter(action='CORRECTION').count(),1)
        self.assertEqual(EndDaySubmission.objects.count(),1) # Reflection edits do not invent new shared work.
        self.assertNotIn('PRIVATE_',json.dumps(list(EndDaySubmission.objects.values_list('facts',flat=True))))
    def test_review_stale_tab_is_rejected_and_same_request_replays(self):
        form=self.review_form();save_daily_review(form=form,user=self.worker,plan=self.plan)
        self.assertEqual(save_daily_review(form=form,user=self.worker,plan=self.plan).pk,self.review.pk)
        self.review.refresh_from_db()
        with self.assertRaises(ValidationError):save_daily_review(form=self.review_form(review_version='stale'),user=self.worker,plan=self.plan)
    def test_email_rollout_off_holds_without_attempt_or_transport(self):
        delivery=self.delivery();adapter=Mock()
        result=handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter)
        self.assertIsInstance(result,JobDeferred);delivery.refresh_from_db()
        self.assertEqual(delivery.state,'HELD');self.assertEqual(delivery.attempt_count,0);adapter.send.assert_not_called()
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True,EMAIL_HOST='localhost',DEFAULT_FROM_EMAIL='webmaster@localhost')
    def test_unconfigured_default_sender_cannot_activate_report_mail(self):
        delivery=self.delivery();adapter=Mock()
        self.assertIsInstance(handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter),JobDeferred)
        adapter.send.assert_not_called()
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True)
    def test_company_test_mode_stays_local_when_other_companies_can_use_smtp(self):
        from tasker.services.reporting import handle_report_delivery
        self.policy(email_mode='TEST',email_enabled=False);self.declare()
        report=build_team_report(user=self.manager,team=self.team,report_date=NOW.date())
        delivery=NotificationDelivery.objects.get(report_snapshot=report,recipient=self.manager,channel='EMAIL')
        handle_report_delivery({'delivery_id':delivery.pk});delivery.refresh_from_db()
        self.assertEqual(delivery.state,'TEST_ACCEPTED')
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True,EMAIL_HOST='smtp.example.invalid',DEFAULT_FROM_EMAIL='Tasker <tasker@example.invalid>')
    def test_smtp_acceptance_is_one_attempt_and_is_not_delivery_confirmation(self):
        delivery=self.delivery();adapter=Mock();adapter.send.return_value={'state':'PROVIDER_ACCEPTED'}
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter)
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter)
        delivery.refresh_from_db();self.assertEqual(delivery.state,'PROVIDER_ACCEPTED');self.assertIsNone(delivery.delivered_at)
        self.assertEqual(delivery.attempt_count,1);adapter.send.assert_called_once()
        self.assertNotIn('PRIVATE_',adapter.send.call_args.kwargs['body'])
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True,EMAIL_HOST='smtp.example.invalid',DEFAULT_FROM_EMAIL='tasker@example.invalid')
    def test_ambiguous_transport_is_unknown_and_never_automatically_resent(self):
        delivery=self.delivery();adapter=Mock();adapter.send.side_effect=TimeoutError('Do not persist raw provider details')
        self.assertIsInstance(handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter),JobCancelled)
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter);delivery.refresh_from_db()
        self.assertEqual(delivery.state,'UNKNOWN');self.assertEqual(delivery.error_code,'report_delivery_uncertain');adapter.send.assert_called_once()
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True,EMAIL_HOST='smtp.example.invalid',DEFAULT_FROM_EMAIL='tasker@example.invalid')
    def test_recipient_revocation_and_source_change_cancel_before_network(self):
        delivery=self.delivery();adapter=Mock()
        self.manager.tasker_organization_access.update(can_receive_reports=False) if hasattr(self.manager,'tasker_organization_access') else None
        from tasker.models import OrganizationAccess
        OrganizationAccess.objects.filter(user=self.manager).update(can_receive_reports=False)
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter);delivery.refresh_from_db()
        self.assertEqual(delivery.state,'CANCELLED');adapter.send.assert_not_called()
    @override_settings(TASKER_REPORT_EMAIL_LIVE_ENABLED=True,EMAIL_HOST='smtp.example.invalid',DEFAULT_FROM_EMAIL='tasker@example.invalid')
    def test_changed_work_supersedes_pending_email(self):
        delivery=self.delivery();adapter=Mock();self.task.title='Changed after snapshot';self.task.save()
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter);delivery.refresh_from_db()
        self.assertEqual(delivery.state,'CANCELLED');adapter.send.assert_not_called()
    @override_settings(EMAIL_TIMEOUT=999,DEFAULT_FROM_EMAIL='Tasker <tasker@example.invalid>')
    def test_real_adapter_uses_bounded_timeout_stable_message_id_and_acceptance(self):
        with patch('tasker.services.report_email.get_connection') as connect,patch('tasker.services.report_email.EmailMessage') as message:
            message.return_value.send.return_value=1
            adapter=SMTPReportEmailAdapter();adapter.send(recipient=self.manager,subject='Synthetic',body='Safe',idempotency_key='same')
            first=message.call_args.kwargs['headers'];adapter.send(recipient=self.manager,subject='Synthetic',body='Safe',idempotency_key='same')
            self.assertEqual(first,message.call_args.kwargs['headers']);self.assertEqual(connect.call_args.kwargs['timeout'],15)
    def test_ui_retains_conflicting_reflection_and_requires_csrf(self):
        self.client.force_login(self.worker);url=reverse('ims:tasker:daily_review')
        data=dict(self.review_form(review_version='stale').data)
        response=self.client.post(url,data);self.assertEqual(response.status_code,409);self.assertContains(response,'PRIVATE_CORRECTION',status_code=409)
        from django.test import Client
        client=Client(enforce_csrf_checks=True);client.force_login(self.worker)
        self.assertEqual(client.post(reverse('ims:tasker:day_action',args=[self.plan.pk,'close']),{}).status_code,403)
    def test_readiness_command_is_offline_and_displays_no_addresses_or_credentials(self):
        from io import StringIO
        from django.core.management import call_command
        output=StringIO()
        with self.assertNumQueries(0):call_command('check_tasker_report_readiness',stdout=output)
        self.assertNotIn('example.invalid',output.getvalue());self.assertIn('No email sent',output.getvalue())
    def test_email_cannot_be_routed_from_a_personal_notification(self):
        from tasker.notification_models import Notification
        from tasker.services.notifications import enqueue_delivery
        notification=Notification.objects.create(owner=self.worker,kind='REQUEST',task=self.task,dedupe_key='personal-alert')
        with self.assertRaises(ValueError):enqueue_delivery(recipient=self.worker,channel='EMAIL',notification=notification,dedupe_key='unsupported-email')
        self.assertFalse(NotificationDelivery.objects.exists())


@override_settings(TASKER_IMPROVEMENT_ENABLED=True,TASKER_REPORTS_ENABLED=True,TASKER_REPORT_EMAIL_LIVE_ENABLED=True,
    EMAIL_HOST='smtp.example.invalid',DEFAULT_FROM_EMAIL='tasker@example.invalid')
class SMTPDurabilityTests(DayHelpers,TransactionTestCase):
    def setUp(self):
        self.clock=patch('django.utils.timezone.now',return_value=NOW);self.clock.start();self.addCleanup(self.clock.stop);setup_day(self)
    def test_crash_after_network_preserves_unknown_intent_and_cannot_resend(self):
        delivery=self.delivery();adapter=Mock();adapter.send.return_value={'state':'PROVIDER_ACCEPTED'}
        original=NotificationDelivery.save
        def crash(row,*args,**kwargs):
            if row.state=='PROVIDER_ACCEPTED':raise RuntimeError('Simulated DB failure after acceptance')
            return original(row,*args,**kwargs)
        with patch.object(NotificationDelivery,'save',crash):
            with self.assertRaises(RuntimeError):handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter)
        delivery.refresh_from_db();self.assertEqual(delivery.state,'UNKNOWN')
        handle_smtp_delivery({'delivery_id':delivery.pk},adapter=adapter);adapter.send.assert_called_once()
