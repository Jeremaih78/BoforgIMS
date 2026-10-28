"""Factual reports, explicit audiences, correction versions and mock delivery."""
from datetime import datetime, time, timedelta, timezone as dt_timezone
import json
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from accounting.models import Company
from tasker.models import DailyPlan, DailyReview, OrganizationAccess, Task, TaskChecklistItem, TaskTimeEntry, Team, TeamMembership, WorkPolicy
from tasker.notification_models import NotificationDelivery, NotificationPreference
from tasker.report_models import EndDaySubmission, ManagerReportSnapshot, ReportPolicy
from tasker.services.arrival import correct_arrival, register_arrival
from tasker.services.reporting import (
    MockReportEmailAdapter, ReportConflict, ReportTransportError, build_team_report,
    close_day, eligible_report_recipients, get_report, handle_build_report,
    handle_report_delivery, reconcile_reports, render_report_email, report_status_summary,
    report_teams_for, save_report_policy, visible_reports_for,
)


NOW = datetime(2026, 10, 27, 11, tzinfo=dt_timezone.utc)


@override_settings(TASKER_REPORTS_ENABLED=True, TASKER_REPORT_EMAIL_LIVE_ENABLED=False)
class ReportingTests(TestCase):
    def setUp(self):
        self.clock = patch("django.utils.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        users = get_user_model()
        self.employee = users.objects.create_user("report-employee")
        self.peer = users.objects.create_user("report-peer")
        self.manager = users.objects.create_user("report-manager")
        self.executive = users.objects.create_user("report-executive")
        self.outsider = users.objects.create_user("report-outsider")
        self.company_admin = users.objects.create_superuser("report-admin", password="test-only")
        for user in (self.employee, self.peer, self.manager, self.executive, self.outsider):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        self.company = Company.objects.create(name="Reporting sample company")
        self.other_company = Company.objects.create(name="Other reporting sample company")
        self.team = Team.objects.create(company=self.company, name="Sample printing team")
        self.other_team = Team.objects.create(company=self.other_company, name="Other sample team")
        self.membership = TeamMembership.objects.create(team=self.team, user=self.employee)
        TeamMembership.objects.create(team=self.team, user=self.peer)
        TeamMembership.objects.create(team=self.team, user=self.manager, role="MANAGER")
        self.grant = OrganizationAccess.objects.create(company=self.company, user=self.executive, can_receive_reports=True)
        WorkPolicy.objects.create(owner=self.employee, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(17), buffer_minutes=30)
        WorkPolicy.objects.create(owner=self.executive, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(17), buffer_minutes=30)
        self.day = NOW.astimezone(__import__("zoneinfo").ZoneInfo("Africa/Harare")).date()
        self.plan = DailyPlan.objects.create(owner=self.employee, plan_date=self.day, quick_notes="PRIVATE_SCRATCHPAD_SENTINEL")
        self.review = DailyReview.objects.create(plan=self.plan, accomplishments="PRIVATE_REFLECTION_SENTINEL",
            biggest_distraction="PRIVATE_DISTRACTION_SENTINEL", lessons_learned="PRIVATE_LESSON_SENTINEL", tomorrow_focus="PRIVATE_TOMORROW_SENTINEL")
        self.task = Task.objects.create(owner=self.employee, team=self.team, company=self.company, title="Shared equipment follow-up",
            notes="PRIVATE_TASK_NOTES_SENTINEL", description="PRIVATE_TASK_DESCRIPTION_SENTINEL", estimated_duration=timedelta(minutes=30), due_date=self.day)
        self.plan.planned_tasks.add(self.task)
        TaskChecklistItem.objects.create(task=self.task, title="Confirm the sample requirements")

    def close(self):
        return close_day(user=self.employee, review=self.review)

    def build(self):
        return build_team_report(user=self.manager, team=self.team, report_date=self.day)

    def configure(self, **changes):
        policy = ReportPolicy.objects.filter(company=self.company).first()
        return save_report_policy(user=self.company_admin, company=self.company,
            expected_revision=str(policy.revision) if policy else "unconfigured-v1",
            **{"timezone_name": "Africa/Harare", "cutoff_time": time(17), "consolidation": "PER_SUBMISSION",
                "correction_policy": "APPEND_REVISION", "missing_policy": "SHOW_MISSING", "weekdays": list(range(7)), "email_mode": "TEST", **changes})

    def test_close_is_explicit_and_no_legacy_review_is_inferred(self):
        report = self.build()
        employee = next(row for row in report.facts["members"] if row["employee_id"] == self.employee.pk)
        self.assertEqual(employee["submission"]["state"], "MISSING")
        self.assertFalse(EndDaySubmission.objects.exists())
        first = self.close()[0]
        second = self.close()[0]
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(EndDaySubmission.objects.count(), 1)
        revised = self.build()
        employee = next(row for row in revised.facts["members"] if row["employee_id"] == self.employee.pk)
        self.assertEqual(employee["submission"]["state"], "CLOSED")
        self.assertEqual(revised.facts["counts"]["missing"], 2)

    def test_private_fields_never_enter_submission_snapshot_or_email(self):
        submission = self.close()[0]
        report = self.build()
        combined = json.dumps([submission.facts, report.facts, render_report_email(report)])
        for secret in ("PRIVATE_SCRATCHPAD_SENTINEL", "PRIVATE_REFLECTION_SENTINEL", "PRIVATE_DISTRACTION_SENTINEL",
                       "PRIVATE_LESSON_SENTINEL", "PRIVATE_TOMORROW_SENTINEL", "PRIVATE_TASK_NOTES_SENTINEL", "PRIVATE_TASK_DESCRIPTION_SENTINEL"):
            self.assertNotIn(secret, combined)
        self.assertIn(self.task.title, combined)
        self.assertIn("Confirm the sample requirements", combined)

    def test_private_review_edits_are_not_factual_corrections(self):
        first = self.close()[0]
        self.review.lessons_learned = "Another private reflection"
        self.review.save()
        self.assertEqual(self.close()[0].pk, first.pk)

    def test_a_to_b_to_a_appends_three_consecutive_versions(self):
        first = self.close()[0]
        report_a = self.build()
        self.task.priority = "CRITICAL"
        self.task.save()
        second = self.close()[0]
        report_b = self.build()
        self.task.priority = "MEDIUM"
        self.task.save()
        third = self.close()[0]
        report_c = self.build()
        self.assertEqual([first.version, second.version, third.version], [1, 2, 3])
        self.assertEqual([report_a.version, report_b.version, report_c.version], [1, 2, 3])
        self.assertEqual(first.source_digest, third.source_digest)
        self.assertNotEqual(first.pk, third.pk)

    def test_repeated_build_and_clock_ticks_do_not_append_revisions(self):
        self.close()
        first = self.build()
        with patch("django.utils.timezone.now", return_value=NOW+timedelta(minutes=10)):
            second = self.build()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(NotificationDelivery.objects.filter(report_snapshot=first).count(), 1)

    def test_managers_and_explicit_executives_have_separate_report_access(self):
        report = self.build()
        self.assertEqual(get_report(user=self.manager, pk=report.pk).pk, report.pk)
        self.assertEqual(get_report(user=self.executive, pk=report.pk).pk, report.pk)
        self.assertFalse(visible_reports_for(self.employee).exists())
        self.assertFalse(report_teams_for(self.outsider).exists())
        self.assertEqual([user.pk for user in eligible_report_recipients(self.team)], [self.executive.pk])
        self.assertNotIn(self.company_admin.pk, [user.pk for user in eligible_report_recipients(self.team)])
        with self.assertRaises(PermissionDenied):
            get_report(user=self.outsider, pk=report.pk)

    def test_report_grant_does_not_expand_general_task_visibility(self):
        from tasker.policies import visible_tasks_for
        self.assertIn(self.team.pk, report_teams_for(self.executive).values_list("pk", flat=True))
        self.assertNotIn(self.task.pk, visible_tasks_for(self.executive).values_list("pk", flat=True))

    def test_member_and_task_rescope_hide_historical_sources(self):
        report = self.build()
        TeamMembership.objects.filter(pk=self.membership.pk).update(is_active=False)
        with self.assertRaises(ReportConflict):
            get_report(user=self.manager, pk=report.pk)
        TeamMembership.objects.filter(pk=self.membership.pk).update(is_active=True)
        Task.objects.filter(pk=self.task.pk).update(team=self.other_team, company=self.other_company)
        with self.assertRaises(ReportConflict):
            get_report(user=self.manager, pk=report.pk)

    def test_older_report_remains_readable_after_a_factual_revision(self):
        first = self.build()
        self.task.priority = "CRITICAL"
        self.task.save()
        second = self.build()
        self.assertEqual(second.version, first.version+1)
        self.assertEqual(get_report(user=self.manager, pk=first.pk).pk, first.pk)

    def test_arrival_requires_recorded_and_current_team_and_retains_correction_provenance(self):
        arrival, _ = register_arrival(user=self.employee)
        correct_arrival(user=self.employee, arrival_id=arrival.pk, effective_at=NOW-timedelta(minutes=15),
            reason="PRIVATE_CORRECTION_REASON", client_key=__import__("uuid").uuid4(), expected_revision=0)
        report = self.build()
        employee = next(row for row in report.facts["members"] if row["employee_id"] == self.employee.pk)
        self.assertEqual(employee["arrival"]["state"], "DECLARED")
        self.assertEqual(employee["arrival"]["correction_count"], 1)
        self.assertNotEqual(employee["arrival"]["registered_at"], employee["arrival"]["effective_at"])
        self.assertNotIn("PRIVATE_CORRECTION_REASON", json.dumps(report.facts))

    def test_unconfigured_delivery_is_held_without_reading_email_backend(self):
        report = self.build()
        delivery = NotificationDelivery.objects.get(report_snapshot=report)
        with patch("django.core.mail.send_mail", side_effect=AssertionError("No live email")):
            handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "HELD")
        self.assertEqual(delivery.attempt_count, 0)

    def test_mock_acceptance_is_idempotent_and_never_claims_actual_delivery(self):
        self.configure()
        report = self.build()
        delivery = NotificationDelivery.objects.get(report_snapshot=report)
        adapter = MockReportEmailAdapter()
        with patch.object(adapter, "send", wraps=adapter.send) as send, patch("django.core.mail.send_mail", side_effect=AssertionError("No live email")):
            handle_report_delivery({"delivery_id": delivery.pk}, adapter=adapter)
            handle_report_delivery({"delivery_id": delivery.pk}, adapter=adapter)
        self.assertEqual(send.call_count, 1)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "TEST_ACCEPTED")
        self.assertIsNone(delivery.delivered_at)
        self.assertEqual(delivery.attempt_count, 1)
        self.assertEqual(report_status_summary(report)["delivery_states"], {"TEST_ACCEPTED": 1})

    def test_revoked_recipient_is_cancelled_at_delivery_even_with_cached_permissions(self):
        self.configure()
        report = self.build()
        delivery = NotificationDelivery.objects.get(report_snapshot=report)
        self.grant.can_receive_reports = False
        self.grant.save()
        handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")
        self.assertEqual(delivery.error_code, "recipient_access_changed")

    def test_superseded_snapshot_and_changed_sources_do_not_send_stale_facts(self):
        self.configure()
        report = self.build()
        delivery = NotificationDelivery.objects.get(report_snapshot=report)
        self.task.priority = "CRITICAL"
        self.task.save()
        with patch.object(MockReportEmailAdapter, "send", side_effect=AssertionError("Stale report")):
            handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")
        self.assertEqual(delivery.error_code, "report_sources_changed")
        latest = self.build()
        self.assertEqual(latest.version, report.version+1)

    def test_cutoff_holds_and_exposes_a_future_retry_without_claiming_a_send(self):
        self.configure(consolidation="AT_CUTOFF")
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        result = handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "HELD")
        self.assertEqual(delivery.error_code, "awaiting_report_cutoff")
        self.assertGreater(result.run_after, NOW)
        self.assertEqual(delivery.attempt_count, 0)

    def test_report_email_respects_recipient_quiet_hours(self):
        from tasker.services.jobs import JobDeferred
        self.configure()
        NotificationPreference.objects.create(owner=self.executive, quiet_enabled=True, quiet_start=time(12), quiet_end=time(14))
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        with patch.object(MockReportEmailAdapter, "send", side_effect=AssertionError("Quiet hours")):
            result = handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertIsInstance(result, JobDeferred)
        self.assertEqual((delivery.state, delivery.error_code, delivery.attempt_count), ("HELD", "quiet_hours", 0))
        self.assertEqual(delivery.next_attempt_at, result.run_after)

    def test_report_email_respects_recipient_focus(self):
        self.configure()
        focused = Task.objects.create(owner=self.executive, title="Executive focus")
        TaskTimeEntry.objects.create(user=self.executive, task=focused, started_at=NOW, entry_type="FOCUS")
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        with patch.object(MockReportEmailAdapter, "send", side_effect=AssertionError("Focus active")):
            result = handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual((delivery.state, delivery.error_code, delivery.attempt_count), ("HELD", "focus_active", 0))
        self.assertEqual(delivery.next_attempt_at, result.run_after)

    def test_report_email_respects_unknown_recipient_work_hours(self):
        self.configure()
        WorkPolicy.objects.filter(owner=self.executive).update(configured=False)
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        with patch.object(MockReportEmailAdapter, "send", side_effect=AssertionError("Unknown work hours")):
            result = handle_report_delivery({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        self.assertEqual((delivery.state, delivery.error_code, delivery.attempt_count), ("HELD", "work_hours_unknown", 0))
        self.assertEqual(delivery.next_attempt_at, result.run_after)

    def test_known_transport_failures_commit_and_exhaust_the_job_retry_budget(self):
        from tasker.job_models import OutboxJob
        from tasker.services.jobs import run_claimed_job
        self.configure()
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        job = OutboxJob.objects.get(dedupe_key=f"delivery:{delivery.pk}")
        with patch.object(MockReportEmailAdapter, "send", side_effect=ReportTransportError()):
            for attempt, expected in ((1, "retry"), (2, "failed")):
                job.state, job.attempts, job.max_attempts = "RUNNING", attempt, 2
                job.lease_token, job.lease_expires_at, job.locked_by = uuid.uuid4(), NOW+timedelta(minutes=10), "report-test"
                job.save()
                self.assertEqual(run_claimed_job(job)["outcome"], expected)
                job.refresh_from_db()
                delivery.refresh_from_db()
                self.assertEqual(delivery.state, "FAILED")
                self.assertEqual(delivery.attempt_count, attempt)
                self.assertEqual(job.attempts, attempt)
                self.assertEqual(job.deferrals, 0)
        self.assertEqual(job.state, "FAILED")
        self.assertEqual(job.last_error_code, "report_test_adapter_failed")
        before = OutboxJob.objects.filter(kind="reports.deliver").count()
        reconcile_reports()
        self.assertEqual(OutboxJob.objects.filter(kind="reports.deliver").count(), before)

    def test_uncertain_transport_result_never_blindly_retries(self):
        self.configure()
        delivery = NotificationDelivery.objects.get(report_snapshot=self.build())
        class Uncertain:
            def send(self, **kwargs):
                raise ReportTransportError(uncertain=True)
        handle_report_delivery({"delivery_id": delivery.pk}, adapter=Uncertain())
        handle_report_delivery({"delivery_id": delivery.pk}, adapter=Uncertain())
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "UNKNOWN")
        self.assertEqual(delivery.attempt_count, 1)

    def test_configuration_is_scoped_versioned_and_never_enables_live_email(self):
        with self.assertRaises(PermissionDenied):
            save_report_policy(user=self.manager, company=self.company)
        policy = self.configure()
        self.assertTrue(policy.configured)
        with self.assertRaises(ReportConflict):
            save_report_policy(user=self.company_admin, company=self.company, expected_revision="unconfigured-v1")
        policy.email_enabled = True
        with self.assertRaises(ValidationError):
            policy.save()

    def test_reconciliation_repairs_explicit_submission_jobs_without_inventing_legacy_closes(self):
        from tasker.job_models import OutboxJob
        self.assertEqual(reconcile_reports()["reviewed_pairs"], 0)
        self.close()
        OutboxJob.objects.filter(kind="reports.build").delete()
        self.assertGreater(reconcile_reports()["requested"], 0)
        self.assertTrue(OutboxJob.objects.filter(kind="reports.build").exists())
        self.assertEqual(EndDaySubmission.objects.count(), 1)

    def test_reconciliation_rotates_old_explicit_dates_and_prioritizes_never_built_reports(self):
        from tasker.job_models import OutboxJob
        self.close()
        self.build()
        old_days = (self.day-timedelta(days=31), self.day-timedelta(days=30))
        for day in old_days:
            plan = DailyPlan.objects.create(owner=self.employee, plan_date=day)
            review = DailyReview.objects.create(plan=plan)
            EndDaySubmission.objects.create(owner=self.employee, team=self.team, report_date=day, version=1,
                source_digest="a"*64, facts={}, review=review, created_at=NOW-timedelta(days=30))
        OutboxJob.objects.filter(kind="reports.build").delete()
        for _ in old_days:
            self.assertEqual(reconcile_reports(limit=1)["reviewed_pairs"], 1)
        dates = set(OutboxJob.objects.filter(kind="reports.build").values_list("payload__report_date", flat=True))
        self.assertEqual(dates, {day.isoformat() for day in old_days})
        self.assertFalse(EndDaySubmission.objects.filter(report_date__in=old_days, last_checked_at__isnull=True).exists())
        self.assertTrue(EndDaySubmission.objects.filter(report_date=self.day, last_checked_at__isnull=True).exists())
        # A stopped/exhausted worker leaves these old pairs unbuilt. They must
        # still yield to an existing report's unchecked correction opportunity.
        reconcile_reports(limit=1)
        self.assertFalse(EndDaySubmission.objects.filter(report_date=self.day, last_checked_at__isnull=True).exists())

    def test_cutoff_reconciliation_rotates_policies(self):
        from tasker.job_models import OutboxJob
        self.configure(consolidation="AT_CUTOFF", cutoff_time=time(9))
        save_report_policy(user=self.company_admin, company=self.other_company, cutoff_time=time(9), consolidation="AT_CUTOFF",
            correction_policy="APPEND_REVISION", missing_policy="SHOW_MISSING", weekdays=list(range(7)), email_mode="TEST")
        for _ in range(2):
            self.assertEqual(reconcile_reports(limit=1)["reviewed_policies"], 1)
        self.assertFalse(ReportPolicy.objects.filter(last_checked_at__isnull=True).exists())
        teams = set(OutboxJob.objects.filter(kind="reports.build").values_list("payload__team_id", flat=True))
        self.assertEqual(teams, {self.team.pk, self.other_team.pk})

    def test_cutoff_reconciliation_rotates_teams_even_before_workers_build_reports(self):
        from tasker.job_models import OutboxJob
        self.configure(consolidation="AT_CUTOFF", cutoff_time=time(9))
        other_teams = [Team.objects.create(company=self.company, name=f"Additional report team {index}") for index in range(2)]
        for _ in range(3):
            self.assertEqual(reconcile_reports(limit=1)["reviewed_pairs"], 1)
        teams = set(OutboxJob.objects.filter(kind="reports.build").values_list("payload__team_id", flat=True))
        self.assertEqual(teams, {self.team.pk, *(team.pk for team in other_teams)})

    def test_factual_snapshot_is_immutable_and_future_report_is_rejected(self):
        report = self.build()
        report.facts = {}
        with self.assertRaises(ValidationError):
            report.save()
        with self.assertRaises(ValidationError):
            build_team_report(user=self.manager, team=self.team, report_date=self.day+timedelta(days=1))

    def test_teamless_end_day_does_not_claim_a_team_report_was_queued(self):
        self.membership.is_active = False
        self.membership.save()
        self.assertEqual(self.close(), ())
        self.assertFalse(EndDaySubmission.objects.exists())
