"""Stage 8 HTTP journeys and the existing planning/operator boundary."""
from datetime import datetime, time, timedelta, timezone as dt_timezone
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse

from accounting.models import Company
from tasker.models import (CoordinationRequest, DailyPlan, EndDaySubmission, ManagerReportSnapshot,
    Notification, NotificationDelivery, OperatorState, OrganizationAccess, OutboxJob, Task,
    TaskTimeEntry, Team, TeamMembership, WorkPolicy)
from tasker.services.coordination import create_request, dispatch_event
from tasker.services.dependencies import readiness_for
from tasker.services.focus import start_focus
from tasker.services.operator import build_operator
from tasker.services.workbench import work_version


NOW = datetime(2030, 1, 7, 8, tzinfo=dt_timezone.utc)


@override_settings(TASKER_COORDINATION_ENABLED=True, TASKER_REPORTS_ENABLED=True, TASKER_OPERATOR_DISPATCH_ENABLED=False)
@patch("django.utils.timezone.now", return_value=NOW)
class CoordinationViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("stage8-ui-owner")
        cls.manager = get_user_model().objects.create_user("stage8-ui-manager")
        cls.outsider = get_user_model().objects.create_user("stage8-ui-outsider")
        cls.executive = get_user_model().objects.create_user("stage8-ui-executive")
        for user in (cls.owner, cls.manager, cls.outsider):
            user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        cls.executive.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
        cls.company = Company.objects.create(name="Stage 8 UI sample company")
        cls.team = Team.objects.create(company=cls.company, name="Stage 8 sample team")
        cls.other_team = Team.objects.create(company=cls.company, name="Unrelated sample team")
        TeamMembership.objects.create(team=cls.team, user=cls.owner)
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.other_team, user=cls.outsider)
        OrganizationAccess.objects.create(company=cls.company, user=cls.manager, can_manage=True, can_view_all=True, can_receive_reports=True)
        cls.executive_grant = OrganizationAccess.objects.create(company=cls.company, user=cls.executive, can_receive_reports=True)
        for user in (cls.owner, cls.manager, cls.executive):
            WorkPolicy.objects.create(owner=user, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(18), buffer_minutes=0)

    def setUp(self):
        self.task = Task.objects.create(owner=self.owner, team=self.team, company=self.company,
            title="Stage 8 sample customer commitment", estimated_duration=timedelta(minutes=60),
            definition_of_done="Shared sample delivery accepted", due_date=NOW.date())
        self.plan = DailyPlan.objects.create(owner=self.owner, plan_date=NOW.date(), quick_notes="Private scratchpad must stay private")
        self.client.force_login(self.owner)

    def url(self, name, *args):
        return reverse("ims:tasker:" + name, args=args)

    def payload(self, **changes):
        task = Task.objects.get(pk=self.task.pk)
        return {"kind": "BLOCKER", "resolver": self.manager.pk, "reason": "Sample stock approval needed",
            "requested_action": "Confirm the sample allocation", "expected_response_at": "2030-01-07T12:00",
            "escalation_policy": "NONE", "client_key": str(uuid.uuid4()), "expected_task_version": work_version(task), **changes}

    def raise_request(self):
        task = Task.objects.get(pk=self.task.pk)
        return create_request(user=self.owner, task=task, kind="BLOCKER", resolver=self.manager,
            reason="Sample approval pending", requested_action="Approve sample allocation",
            expected_response_at=NOW+timedelta(hours=2), client_key=uuid.uuid4(), expected_task_version=work_version(task))

    def test_form_is_scoped_and_has_explicit_response_fields(self, clock):
        response = self.client.get(self.url("coordination_create", self.task.slug))
        self.assertContains(response, "Who needs to act?")
        self.assertContains(response, "Africa/Harare")
        allowed = set(response.context["form"].fields["resolver"].queryset.values_list("pk", flat=True))
        self.assertIn(self.manager.pk, allowed)
        self.assertNotIn(self.outsider.pk, allowed)

    def test_raise_stops_focus_and_identical_retry_has_one_request(self, clock):
        start_focus(task=self.task, user=self.owner)
        data = self.payload()
        response = self.client.post(self.url("coordination_create", self.task.slug), data)
        item = CoordinationRequest.objects.get()
        self.assertRedirects(response, self.url("coordination_detail", item.pk))
        self.assertRedirects(self.client.post(self.url("coordination_create", self.task.slug), data), self.url("coordination_detail", item.pk))
        self.assertFalse(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "BLOCKED")
        self.assertEqual(CoordinationRequest.objects.count(), 1)

    def test_invalid_and_stale_form_retain_context(self, clock):
        data = self.payload(expected_response_at="missing time", reason="Retained sample explanation")
        response = self.client.post(self.url("coordination_create", self.task.slug), data)
        self.assertEqual(response.status_code, 400)
        self.assertContains(response, "Retained sample explanation", status_code=400)
        data = self.payload(reason="Retained after source changed")
        Task.objects.filter(pk=self.task.pk).update(priority="CRITICAL")
        response = self.client.post(self.url("coordination_create", self.task.slug), data)
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "Retained after source changed", status_code=409)
        self.assertFalse(CoordinationRequest.objects.exists())

    def test_forged_resolver_and_cross_team_task_are_denied(self, clock):
        response = self.client.post(self.url("coordination_create", self.task.slug), self.payload(resolver=self.outsider.pk))
        self.assertEqual(response.status_code, 400)
        self.assertFalse(CoordinationRequest.objects.exists())
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("coordination_create", self.task.slug)).status_code, 404)

    def test_resolver_ack_then_resolve_preserves_owner_and_task_acceptance(self, clock):
        item = self.raise_request()
        self.client.force_login(self.manager)
        url = self.url("coordination_detail", item.pk)
        self.assertEqual(self.client.post(url, {"action": "acknowledge", "expected_revision": item.revision}).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "ACKNOWLEDGED")
        self.assertNotContains(self.client.get(url), ">Acknowledge request</button>")
        self.assertEqual(self.client.post(url, {"action": "resolve", "expected_revision": item.revision, "reason": "Allocation approved"}).status_code, 302)
        item.refresh_from_db(); self.task.refresh_from_db()
        self.assertEqual(item.status, "RESOLVED")
        self.assertEqual(self.task.owner_id, self.owner.pk)
        self.assertEqual(self.task.status, "PENDING")
        self.assertIsNone(self.task.completed_at)

    def test_conflicting_response_retains_resolution_and_escapes_html(self, clock):
        item = self.raise_request()
        old = item.revision
        self.client.force_login(self.manager)
        url = self.url("coordination_detail", item.pk)
        self.client.post(url, {"action": "acknowledge", "expected_revision": old})
        response = self.client.post(url, {"action": "resolve", "expected_revision": old, "reason": "<script>retained response</script>"})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "&lt;script&gt;retained response&lt;/script&gt;", status_code=409)
        item.refresh_from_db()
        self.assertEqual(item.status, "ACKNOWLEDGED")

    def test_request_blocks_manual_status_bypass_in_readiness_and_focus(self, clock):
        self.raise_request()
        Task.objects.filter(pk=self.task.pk).update(status="PENDING", blocked_reason="")
        task = Task.objects.get(pk=self.task.pk)
        ready = readiness_for(task=task, user=self.owner)
        self.assertFalse(ready.ready)
        self.assertEqual(ready.code, "coordination")
        with self.assertRaises(ValueError):
            start_focus(task=task, user=self.owner)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_obsolete_request_cannot_constrain_the_new_task_owner(self, clock):
        self.raise_request()
        Task.objects.filter(pk=self.task.pk).update(owner=self.manager, status="PENDING", blocked_reason="")
        task = Task.objects.get(pk=self.task.pk)
        self.assertTrue(readiness_for(task=task, user=self.manager).ready)
        self.assertEqual(self.client.get(self.url("coordination_detail", CoordinationRequest.objects.get().pk)).status_code, 404)

    def test_notification_center_labels_held_and_does_not_ack_on_get(self, clock):
        item = self.raise_request()
        dispatch_event({"request_id": item.pk, "revision": str(item.revision), "event": "raised"})
        self.client.force_login(self.manager)
        response = self.client.get(self.url("notification_home"))
        self.assertContains(response, "Action requested")
        self.assertContains(response, "Held or queued")
        self.assertContains(response, self.url("coordination_detail", item.pk))
        item.refresh_from_db()
        self.assertEqual(item.status, "OPEN")
        self.assertFalse(Notification.objects.filter(owner=self.manager, read_at__isnull=False).exists())

    def test_notification_preferences_version_and_validation_are_visible(self, clock):
        data = {"timezone_name": "Africa/Harare", "quiet_enabled": "1", "quiet_start": "22:00", "quiet_end": "07:00",
            "respect_work_hours": "1", "suppress_during_focus": "1", "digest_mode": "HOURLY", "expected_revision": "default-v1"}
        url = self.url("notification_preferences")
        self.assertContains(self.client.get(url), "Protect focused work")
        self.assertEqual(self.client.post(url, data).status_code, 302)
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context["form"]["digest_mode"].value(), "HOURLY")

    def test_end_day_records_shared_submission_and_preserves_private_reflection(self, clock):
        url = self.url("daily_review")
        response = self.client.post(url, {"energy_level": "3", "accomplishments": "Private reflection phrase", "tomorrow_focus": "Private next-day reflection"})
        self.assertEqual(response.status_code, 302)
        submission = EndDaySubmission.objects.get(owner=self.owner)
        self.assertNotIn("Private reflection phrase", str(submission.facts))
        self.assertNotIn("Private scratchpad must stay private", str(submission.facts))
        self.assertContains(self.client.get(url), "End day facts recorded")
        self.assertTrue(OutboxJob.objects.filter(kind="reports.build").exists())

    def test_report_only_executive_reads_shared_facts_without_task_link_authority(self, clock):
        self.task.status, self.task.progress, self.task.completed_at = "COMPLETED", 100, NOW
        self.task.save()
        self.client.force_login(self.manager)
        response = self.client.post(self.url("report_build", self.team.pk), {"report_date": NOW.date().isoformat()})
        self.assertEqual(response.status_code, 302)
        report = ManagerReportSnapshot.objects.get()
        self.client.force_login(self.executive)
        response = self.client.get(self.url("report_detail", report.pk))
        self.assertContains(response, self.task.title)
        self.assertContains(response, "No End day submission")
        self.assertNotContains(response, f'href="{self.task.get_absolute_url()}"')
        self.assertNotContains(response, "Private scratchpad must stay private")
        self.assertEqual(self.client.get(self.task.get_absolute_url()).status_code, 404)
        home = self.client.get(self.url("report_home"))
        self.assertNotContains(home, "My End day review")
        with override_settings(TASKER_COORDINATION_ENABLED=False):
            home = self.client.get(self.url("report_home"))
            self.assertNotContains(home, f'href="{self.url("coordination_home")}"')

    def test_outside_team_and_revoked_report_recipient_cannot_read_report(self, clock):
        self.client.force_login(self.manager)
        self.client.post(self.url("report_build", self.team.pk), {"report_date": NOW.date().isoformat()})
        report = ManagerReportSnapshot.objects.get()
        self.client.force_login(self.outsider)
        response = self.client.get(self.url("report_detail", report.pk))
        self.assertIn(response.status_code, (403, 404))
        self.assertNotContains(response, self.task.title, status_code=response.status_code)
        self.client.force_login(self.executive)
        OrganizationAccess.objects.filter(pk=self.executive_grant.pk).update(is_active=False)
        response = self.client.get(self.url("report_detail", report.pk))
        self.assertIn(response.status_code, (403, 404))
        self.assertNotContains(response, self.task.title, status_code=response.status_code)

    def test_report_policy_is_manager_only_and_test_only(self, clock):
        url = self.url("report_policy", self.company.pk)
        self.assertEqual(self.client.get(url).status_code, 403)
        self.client.force_login(self.manager)
        response = self.client.get(url)
        self.assertContains(response, "Live email delivery remains disabled")
        self.assertNotContains(response, 'value="LIVE"')

    def test_job_health_and_retry_are_superuser_only_and_actions_post_only(self, clock):
        self.assertEqual(self.client.get(self.url("jobs_home")).status_code, 403)
        self.assertEqual(self.client.get(self.url("notification_mark", 1)).status_code, 405)
        self.owner.is_superuser = True
        self.owner.save(update_fields=["is_superuser"])
        response = self.client.get(self.url("jobs_home"))
        self.assertContains(response, "Job health")
        self.assertContains(response, "Not recorded")

    @override_settings(TASKER_COORDINATION_ENABLED=False, TASKER_REPORTS_ENABLED=False)
    def test_disabled_feature_has_no_navigation_or_direct_route(self, clock):
        self.assertEqual(self.client.get(self.url("coordination_home")).status_code, 404)
        self.assertEqual(self.client.get(self.url("report_home")).status_code, 404)
        self.assertNotContains(self.client.get(self.url("index")), 'aria-label="Notifications"')

    @override_settings(TASKER_OPERATOR_DISPATCH_ENABLED=True)
    def test_stage7_invalidation_and_outbox_coalesce_and_rollback_together(self, clock):
        from django.db import transaction
        from tasker.operator_events import invalidate_operator
        state = OperatorState.objects.create(owner=self.owner)
        original_revision = state.revision
        with self.assertRaises(RuntimeError):
            with transaction.atomic():
                invalidate_operator(user_ids=[self.owner.pk], reason="task_changed")
                raise RuntimeError("Synthetic rolled-back edit")
        state.refresh_from_db()
        self.assertEqual(state.revision, original_revision)
        self.assertFalse(OutboxJob.objects.filter(kind="operator.refresh").exists())
        invalidate_operator(user_ids=[self.owner.pk], reason="task_changed")
        invalidate_operator(user_ids=[self.owner.pk], reason="availability_changed")
        state.refresh_from_db()
        job = OutboxJob.objects.get(kind="operator.refresh")
        self.assertEqual(job.payload["revision"], str(state.revision))
