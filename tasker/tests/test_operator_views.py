"""Guidance journeys, retained conflicts and usable policy control."""
from datetime import datetime, time, timedelta, timezone as dt_timezone
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase
from django.urls import reverse

from tasker.models import OperatorOverride, OperatorPolicy, OperatorSnapshot, Task, TaskTimeEntry, WorkPolicy
from tasker.operator_models import DEFAULT_WEIGHTS
from tasker.services.operator import build_operator


NOW = datetime(2026, 10, 27, 8, tzinfo=dt_timezone.utc)


@patch("django.utils.timezone.now", return_value=NOW)
class OperatorViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator-view-owner")
        self.peer = get_user_model().objects.create_user("operator-view-peer")
        for user in (self.user, self.peer):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        WorkPolicy.objects.create(owner=self.user, configured=True, weekdays=list(range(7)),
            start_time=time(8), end_time=time(16), buffer_minutes=60)
        self.task = Task.objects.create(owner=self.user, title="Stage 7 view sample", priority="CRITICAL",
            estimated_duration=timedelta(minutes=60), definition_of_done="Sample outcome confirmed")
        self.other = Task.objects.create(owner=self.user, title="Stage 7 alternative", estimated_duration=timedelta(minutes=30))
        self.client.force_login(self.user)

    def url(self, name):
        return reverse("ims:tasker:" + name)

    def review(self):
        return self.client.get(self.url("operator_home")).context["operator"]

    def test_now_has_canonical_action_and_separate_queues(self, clock):
        response = self.client.get(self.url("operator_home"))
        for text in (self.task.title, "First action", "Definition of done", "Sample outcome confirmed",
                     "Now", "Next", "Waiting", "Decisions", "Start 25-minute Focus", "Works with AI off."):
            self.assertContains(response, text)
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(OperatorSnapshot.objects.count(), 1)

    def test_start_enters_existing_focus_and_second_post_does_not_duplicate(self, clock):
        review = self.review()
        data = {"snapshot_id": review["snapshot"].pk, "task_id": self.task.pk}
        response = self.client.post(self.url("operator_start"), data)
        self.assertRedirects(response, reverse("ims:tasker:focus_mode", args=(self.task.slug,)))
        self.assertEqual(self.client.post(self.url("operator_start"), data).status_code, 409)
        self.assertEqual(TaskTimeEntry.objects.filter(ended_at__isnull=True).count(), 1)
        self.assertEqual(OperatorOverride.objects.filter(action="START").count(), 1)

    def test_blocked_move_stays_waiting_and_alternative_becomes_now(self, clock):
        review = self.review()
        response = self.client.post(self.url("operator_block"), {"snapshot_id": review["snapshot"].pk,
            "task_id": self.task.pk, "reason": "Synthetic supplier approval pending"})
        self.assertRedirects(response, self.url("operator_home"))
        refreshed = self.review()
        self.assertEqual(refreshed["now"]["task_id"], self.other.pk)
        self.assertIn(self.task.pk, [item["task_id"] for item in refreshed["waiting"]])
        self.task.refresh_from_db()
        self.assertEqual(self.task.blocked_reason, "Synthetic supplier approval pending")

    def test_choose_another_requires_reason_and_keeps_policy(self, clock):
        review = self.review()
        data = {"snapshot_id": review["snapshot"].pk, "task_id": self.other.pk, "choose_another": "1"}
        self.assertEqual(self.client.post(self.url("operator_start"), data).status_code, 409)
        self.assertFalse(TaskTimeEntry.objects.exists())
        data["reason"] = "Prepare this before the customer call"
        self.assertEqual(self.client.post(self.url("operator_start"), data).status_code, 302)
        self.assertEqual(OperatorOverride.objects.get(action="CHOOSE_ANOTHER").reason, data["reason"])
        self.assertFalse(OperatorPolicy.objects.exists())

    def test_conflict_retains_escaped_choice_without_mutation(self, clock):
        review = self.review()
        Task.objects.filter(pk=self.other.pk).update(archived=True)
        response = self.client.post(self.url("operator_start"), {"snapshot_id": review["snapshot"].pk,
            "task_id": self.other.pk, "choose_another": "1", "reason": "<script>private draft</script>"})
        self.assertEqual(response.status_code, 409)
        self.assertContains(response, "&lt;script&gt;private draft&lt;/script&gt;", status_code=409)
        self.assertNotContains(response, "<script>private draft</script>", status_code=409)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_forged_or_other_owner_review_is_unavailable(self, clock):
        review = self.review()
        self.client.force_login(self.peer)
        response = self.client.post(self.url("operator_start"), {"snapshot_id": review["snapshot"].pk, "task_id": self.task.pk})
        self.assertEqual(response.status_code, 409)
        self.assertNotContains(response, self.task.title, status_code=409)
        self.assertEqual(self.client.post(self.url("operator_start"), {"snapshot_id": "invalid", "task_id": "1"}).status_code, 400)

    def test_actions_are_post_only_and_need_current_capability(self, clock):
        self.assertEqual(self.client.get(self.url("operator_start")).status_code, 405)
        self.assertEqual(self.client.get(self.url("operator_block")).status_code, 405)
        self.user.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="change_task"))
        self.assertEqual(self.client.post(self.url("operator_start"), {"snapshot_id": uuid.uuid4(), "task_id": self.task.pk}).status_code, 403)

    def test_work_hours_unknown_is_a_linked_decision(self, clock):
        WorkPolicy.objects.filter(owner=self.user).update(configured=False)
        response = self.client.get(self.url("operator_home"))
        self.assertIsNone(response.context["operator"]["now"])
        self.assertContains(response, "Configure work hours")
        self.assertNotContains(response, "Start 25-minute Focus")

    def test_effort_and_done_decisions_have_specific_destinations(self, clock):
        self.task.estimated_duration = None
        self.task.save()
        response = self.client.get(self.url("operator_home"))
        self.assertContains(response, reverse("ims:tasker:task_effort", args=(self.task.slug,)))
        self.assertContains(response, reverse("ims:tasker:task_edit", args=(self.other.slug,)))

    def test_policy_form_versions_saves_and_preserves_stale_input(self, clock):
        url = self.url("operator_policy")
        self.assertContains(self.client.get(url), "Time waiting for attention")
        data = {**DEFAULT_WEIGHTS, "expected_revision": "default-v1", "goal": 75}
        self.assertRedirects(self.client.post(url, data), self.url("operator_home"))
        self.assertEqual(OperatorPolicy.objects.get(owner=self.user).weights["goal"], 75)
        data["goal"] = 99
        response = self.client.post(url, data)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.context["form"]["goal"].value(), "99")
        self.assertEqual(OperatorPolicy.objects.get(owner=self.user).weights["goal"], 75)
        self.assertEqual(OperatorOverride.objects.filter(action="POLICY_CHANGED").count(), 1)

    def test_invalid_policy_cannot_disable_fairness(self, clock):
        response = self.client.post(self.url("operator_policy"), {**DEFAULT_WEIGHTS, "expected_revision": "default-v1", "age": 0})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OperatorPolicy.objects.exists())

    def test_wrap_up_and_dashboard_share_the_same_operator(self, clock):
        dashboard = self.client.get(self.url("index"))
        wrapup = self.client.get(reverse("ims:tasker:focus_wrap_up", args=(self.task.slug,)))
        self.assertEqual(dashboard.context["operator"]["now"]["task_id"], wrapup.context["operator"]["now"]["task_id"])
        self.assertContains(dashboard, "Daily overview, deadlines and notes")
        self.assertContains(wrapup, "One clear next move")

    def test_private_capture_to_focus_works_with_no_provider_and_exhausted_token_quota(self, clock):
        from tasker.models import AIConfiguration, AIUsageLog, CaptureItem
        from tasker.services.captures import promote_capture
        from tasker.services.effort import effort_for_task, set_remaining_effort
        config, _ = AIConfiguration.objects.get_or_create(provider="OPENAI", is_active=True)
        config.is_enabled, config.daily_token_limit = False, 1
        config.save()
        AIUsageLog.objects.create(user=self.user, configuration=config, operation="synthetic-exhausted-quota",
            status="SUCCEEDED", input_tokens=1, total_tokens=1, quota_charged_tokens=1,
            quota_reserved_at=NOW, quota_settled_at=NOW)
        key = uuid.uuid4()
        with patch("tasker.ai.service.AIService", side_effect=AssertionError("Core work must not request a provider")):
            response = self.client.post(self.url("capture_create"), {"client_key": key, "kind": "IDEA",
                "title": "Captured customer solution", "content": "Private original idea stays available", "tags": "sales"})
            self.assertEqual(response.status_code, 302)
            item = CaptureItem.objects.get(client_key=key)
            task = promote_capture(user=self.user, pk=item.pk, version=item.version)
            self.assertIsNone(task.due_date)
            self.assertEqual(item.visibility, "PRIVATE")
            task.priority = "CRITICAL"
            task.save()
            set_remaining_effort(user=self.user, task=task, remaining_minutes=20,
                expected_version=effort_for_task(task=task)["version"])
            review = self.review()
            offered = ([review["now"]] if review["now"] else []) + review["next"]
            self.assertIn(task.pk, [candidate["task_id"] for candidate in offered])
            response = self.client.post(self.url("operator_start"), {"snapshot_id": review["snapshot"].pk,
                "task_id": task.pk, "choose_another": "1" if review["now"]["task_id"] != task.pk else "",
                "reason": "Act on the captured customer solution"})
            self.assertEqual(response.status_code, 302)
        self.assertEqual(TaskTimeEntry.objects.get(ended_at__isnull=True).task_id, task.pk)
        self.assertEqual(AIUsageLog.objects.count(), 1)
        item.refresh_from_db()
        self.assertEqual(item.original_text, "Private original idea stays available")
