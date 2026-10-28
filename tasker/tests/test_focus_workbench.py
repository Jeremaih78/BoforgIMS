"""Focus continuity, private distraction capture and deliberate acceptance."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time, timedelta, timezone as dt_timezone
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.messages import get_messages
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from accounting.models import Company
from tasker.capture_models import CaptureItem
from tasker.models import Goal, Initiative, Task, TaskActivity, TaskChecklistItem, TaskContribution, TaskTimeEntry, Team, TeamMembership
from tasker.services.focus import (
    ActiveTimerConflict, complete_focus, completion_definition_version,
    focus_interval_context, pause_focus, start_focus, stop_focus, stop_own_focus,
)
from tasker.services.next_actions import suggest_next_work


class FocusWorkbenchTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("workbench-owner")
        self.peer = get_user_model().objects.create_user("workbench-peer")
        self.manager = get_user_model().objects.create_user("workbench-manager")
        permissions = Permission.objects.filter(content_type__app_label="tasker", codename__in=(
            "view_task", "change_task", "change_taskchecklistitem", "assign_task"))
        for user in (self.user, self.peer, self.manager):
            user.user_permissions.set(permissions)
        self.task = Task.objects.create(title="Prepare printing proposal", owner=self.user, notes="Shared decisions")
        self.client.force_login(self.user)

    def url(self, name, task=None):
        return reverse(f"ims:tasker:{name}", kwargs={"slug": (task or self.task).slug})

    def test_setting_interval_reuses_the_timer_and_excludes_earlier_time(self):
        now = timezone.now()
        with patch("tasker.services.focus.timezone.now", return_value=now):
            entry = start_focus(task=self.task, user=self.user)
        with patch("tasker.services.focus.timezone.now", return_value=now + timedelta(minutes=2)):
            same = start_focus(task=self.task, user=self.user, interval_minutes="25")
        self.assertEqual(entry.pk, same.pk)
        self.assertEqual(same.started_at, now)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.user).count(), 1)
        context = focus_interval_context(task=self.task, user=self.user, running=same)
        self.assertEqual(context["minutes"], 25)
        self.assertEqual(context["base_seconds"], -120)

    def test_pause_resume_counts_working_time_and_stop_ends_target(self):
        now = timezone.now()
        with patch("tasker.services.focus.timezone.now", return_value=now):
            original = start_focus(task=self.task, user=self.user, interval_minutes=25)
        with patch("tasker.services.focus.timezone.now", return_value=now + timedelta(minutes=5)):
            pause_focus(task=self.task, user=self.user)
        context = focus_interval_context(task=self.task, user=self.user)
        self.assertEqual(context["base_seconds"], 300)
        self.assertFalse(context["running"])
        with patch("tasker.services.focus.timezone.now", return_value=now + timedelta(minutes=40)):
            resumed = start_focus(task=self.task, user=self.user, resume_interval=True)
        self.assertEqual(resumed.ai_metadata["focus_interval_id"], original.ai_metadata["focus_interval_id"])
        context = focus_interval_context(task=self.task, user=self.user, running=resumed)
        self.assertEqual(context["base_seconds"], 300)
        self.assertEqual(context["started_at"], now + timedelta(minutes=40))
        with patch("tasker.services.focus.timezone.now", return_value=now + timedelta(minutes=42)):
            stop_focus(task=self.task, user=self.user)
        self.task.refresh_from_db()
        self.assertEqual(self.task.actual_duration, timedelta(minutes=7))
        self.assertIsNone(focus_interval_context(task=self.task, user=self.user)["minutes"])

    def test_stop_while_paused_ends_target(self):
        start_focus(task=self.task, user=self.user, interval_minutes=15)
        pause_focus(task=self.task, user=self.user)
        stop_focus(task=self.task, user=self.user)
        resumed = start_focus(task=self.task, user=self.user, resume_interval=True)
        self.assertEqual(resumed.ai_metadata, {})

    def test_invalid_interval_does_not_start_or_modify_task(self):
        for value in (0, -1, 241, True, "1.5", "abc", "01"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                start_focus(task=self.task, user=self.user, interval_minutes=value)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.PENDING)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_definition_acceptance_and_stale_review_leave_timer_running(self):
        self.task.definition_of_done = "Proposal approved by customer"
        self.task.save()
        start_focus(task=self.task, user=self.user)
        with self.assertRaises(ValueError):
            complete_focus(task=self.task, user=self.user)
        version = completion_definition_version(self.task)
        Task.objects.filter(pk=self.task.pk).update(definition_of_done="Signed order received")
        with self.assertRaisesMessage(ValueError, "changed"):
            complete_focus(task=self.task, user=self.user, accept_definition=True, definition_version=version)
        self.assertTrue(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())
        self.task.refresh_from_db()
        complete_focus(task=self.task, user=self.user, accept_definition=True,
                       definition_version=completion_definition_version(self.task))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        self.assertFalse(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())

    def test_completion_http_requires_current_explicit_definition_review(self):
        self.task.definition_of_done = "Customer accepted delivery"
        self.task.save()
        start_focus(task=self.task, user=self.user)
        page = self.client.get(self.url("focus_mode"))
        self.assertContains(page, 'name="accept_definition" required')
        self.assertContains(page, self.task.definition_of_done)
        response = self.client.post(self.url("focus_complete"), {"accept_definition": "on"})
        self.assertRedirects(response, self.url("focus_mode"))
        self.assertTrue(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())
        response = self.client.post(self.url("focus_complete"), {"accept_definition": "on",
            "definition_version": page.context["definition_version"]})
        self.assertRedirects(response, self.url("focus_wrap_up"))

    def test_legacy_task_can_complete_without_inventing_an_outcome(self):
        response = self.client.post(self.url("focus_complete"))
        self.assertRedirects(response, self.url("focus_wrap_up"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.definition_of_done, "")
        self.assertEqual(self.task.status, Task.Status.COMPLETED)

    def test_parking_is_private_idempotent_and_does_not_touch_task_or_timer(self):
        entry = start_focus(task=self.task, user=self.user)
        before = TaskActivity.objects.filter(task=self.task).count()
        key = str(uuid.uuid4())
        payload = {"content": "Private new product idea", "client_key": key}
        first = self.client.post(self.url("focus_park"), payload)
        second = self.client.post(self.url("focus_park"), payload)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["capture_url"], second.json()["capture_url"])
        self.assertNotEqual(first.json()["new_client_key"], key)
        self.assertEqual(CaptureItem.objects.count(), 1)
        capture = CaptureItem.objects.get()
        self.assertEqual(capture.owner, self.user)
        self.assertEqual(capture.kind, CaptureItem.Kind.IDEA)
        self.assertEqual(capture.visibility, CaptureItem.Visibility.PRIVATE)
        self.assertEqual(capture.original_text, payload["content"])
        self.assertIsNone(capture.promoted_task)
        self.task.refresh_from_db()
        entry.refresh_from_db()
        self.assertEqual(self.task.notes, "Shared decisions")
        self.assertIsNone(entry.ended_at)
        self.assertEqual(TaskActivity.objects.filter(task=self.task).count(), before)
        self.client.force_login(self.peer)
        self.assertEqual(self.client.get(capture.get_absolute_url()).status_code, 404)

    def test_invalid_and_conflicting_parking_never_overwrites_capture(self):
        url = self.url("focus_park")
        for content in ("", "  ", "x" * 10001):
            self.assertEqual(self.client.post(url, {"content": content, "client_key": str(uuid.uuid4())}).status_code, 400)
        self.assertEqual(self.client.post(url, {"content": "Idea", "client_key": "invalid"}).status_code, 400)
        key = str(uuid.uuid4())
        self.client.post(url, {"content": "First", "client_key": key})
        self.assertEqual(self.client.post(url, {"content": "Changed", "client_key": key}).status_code, 409)
        self.assertEqual(CaptureItem.objects.get().content, "First")

    def test_parking_requires_current_task_scope_and_nonterminal_task(self):
        self.client.force_login(self.peer)
        self.assertIn(self.client.post(self.url("focus_park"), {
            "content": "Idea", "client_key": str(uuid.uuid4())}).status_code, (403, 404))
        self.client.force_login(self.user)
        Task.objects.filter(pk=self.task.pk).update(status=Task.Status.COMPLETED)
        response = self.client.post(self.url("focus_park"), {
            "content": "Idea", "client_key": str(uuid.uuid4())})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "focus_unavailable")
        self.assertFalse(CaptureItem.objects.exists())

    def test_focus_shows_bounded_checklist_and_current_next_step(self):
        TaskChecklistItem.objects.bulk_create([TaskChecklistItem(task=self.task, title=f"Step {i:02}",
            position=i, is_completed=i == 0) for i in range(55)])
        page = self.client.get(self.url("focus_mode"))
        self.assertEqual(len(page.context["checklist_items"]), 50)
        self.assertEqual(page.context["next_step"].title, "Step 01")
        self.assertContains(page, "View all 55")
        self.assertContains(page, 'id="focusParkingForm"')

    def test_focus_never_reveals_private_source_capture_on_a_shared_task(self):
        company = Company.objects.create(name="Focus source company")
        team = Team.objects.create(name="Source team", company=company)
        TeamMembership.objects.create(user=self.user, team=team)
        self.task.company, self.task.team = company, team
        self.task.save()
        capture = CaptureItem.objects.create(owner=self.manager, content="Private source text",
            original_text="Private source text", title="Private source title", promoted_task=self.task)
        page = self.client.get(self.url("focus_mode"))
        self.assertIsNone(page.context["source_capture"])
        self.assertNotContains(page, capture.title)
        self.assertNotContains(page, capture.content)
        self.assertNotContains(page, capture.get_absolute_url())

    def test_primary_goal_link_requires_current_goal_visibility(self):
        company = Company.objects.create(name="Focus goal company")
        team = Team.objects.create(name="Goal team", company=company)
        TeamMembership.objects.create(user=self.user, team=team)
        self.task.company, self.task.team = company, team
        self.task.save()
        goal = Goal.objects.create(title="Private goal context", company=company, team=team)
        initiative = Initiative.objects.create(title="Goal initiative", goal=goal)
        TaskContribution.objects.create(task=self.task, initiative=initiative)
        self.assertIsNone(self.client.get(self.url("focus_mode")).context["primary_contribution"])
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_goal"))
        page = self.client.get(self.url("focus_mode"))
        self.assertContains(page, goal.title)
        self.assertContains(page, goal.get_absolute_url())

    @patch("django.utils.timezone.now", return_value=datetime(2026, 10, 27, 8, tzinfo=dt_timezone.utc))
    def test_next_work_enforces_feasibility_then_critical_and_deadline_ranking(self, clock):
        from tasker.models import WorkPolicy
        WorkPolicy.objects.create(owner=self.user, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(16), buffer_minutes=60)
        low = Task.objects.create(title="Low earlier", owner=self.user, priority=Task.Priority.LOW,
            due_date=timezone.localdate() - timedelta(days=3), estimated_duration=timedelta(minutes=30))
        critical = Task.objects.create(title="Critical first", owner=self.user, priority=Task.Priority.CRITICAL, estimated_duration=timedelta(minutes=30))
        high = Task.objects.create(title="High second", owner=self.user, priority=Task.Priority.HIGH, estimated_duration=timedelta(minutes=30))
        for status in (Task.Status.BLOCKED, Task.Status.COMPLETED, Task.Status.CANCELLED):
            Task.objects.create(title=status, owner=self.user, priority=Task.Priority.CRITICAL, status=status)
        Task.objects.create(title="Archive", owner=self.user, archived=True, priority=Task.Priority.CRITICAL)
        Task.objects.create(title="Peer's urgent task", owner=self.peer, priority=Task.Priority.CRITICAL)
        self.assertEqual(suggest_next_work(user=self.user, exclude_task=self.task), [critical, low, high])

    @patch("django.utils.timezone.now", return_value=datetime(2026, 10, 27, 8, tzinfo=dt_timezone.utc))
    def test_block_wrap_up_preserves_reason_and_offers_alternative(self, clock):
        from tasker.models import WorkPolicy
        WorkPolicy.objects.create(owner=self.user, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(16), buffer_minutes=60)
        alternative = Task.objects.create(title="Contact supplier", owner=self.user, estimated_duration=timedelta(minutes=30))
        response = self.client.post(self.url("focus_block"), {"reason": "Waiting for stock confirmation"})
        self.assertRedirects(response, self.url("focus_wrap_up"))
        page = self.client.get(self.url("focus_wrap_up"))
        self.assertContains(page, "Waiting for stock confirmation")
        self.assertContains(page, alternative.title)
        self.assertEqual(page.context["operator"]["now"]["task_id"], alternative.pk)
        self.assertIn(self.task.pk, [item["task_id"] for item in page.context["operator"]["waiting"]])

    def test_revoked_view_capability_denies_focus_notes_and_service_writes(self):
        start_focus(task=self.task, user=self.user)
        self.user.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
        self.assertEqual(self.client.get(self.url("focus_mode")).status_code, 403)
        self.assertEqual(self.client.post(self.url("focus_notes"), {"notes": "No access"}).status_code, 403)
        with self.assertRaises(PermissionDenied):
            complete_focus(task=self.task, user=self.user)
        self.assertTrue(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())

    def test_revoked_scope_can_stop_only_own_timer_without_disclosing_task(self):
        company = Company.objects.create(name="Focus scope company")
        team = Team.objects.create(name="Focus scope team", company=company)
        membership = TeamMembership.objects.create(user=self.manager, team=team, role=TeamMembership.Role.MANAGER)
        TeamMembership.objects.create(user=self.peer, team=team)
        scoped = Task.objects.create(title="Confidential shared task", owner=self.peer, team=team, company=company,
                                     notes="Confidential shared notes")
        mine = start_focus(task=scoped, user=self.manager)
        peer_entry = start_focus(task=scoped, user=self.peer)
        mine.started_at = timezone.now() - timedelta(minutes=2)
        mine.save(update_fields=["started_at"])
        membership.is_active = False
        membership.save(update_fields=["is_active"])
        own_task = Task.objects.create(title="Still allowed", owner=self.manager)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url("focus_mode", scoped)).status_code, 404)
        response = self.client.post(self.url("focus_start", own_task))
        self.assertRedirects(response, reverse("ims:tasker:task_list"))
        messages = " ".join(str(item) for item in get_messages(response.wsgi_request))
        self.assertNotIn(scoped.title, messages)
        self.assertNotIn(scoped.notes, messages)
        response = self.client.post(reverse("ims:tasker:focus_stop_active"))
        self.assertRedirects(response, reverse("ims:tasker:task_list"))
        mine.refresh_from_db(); peer_entry.refresh_from_db(); scoped.refresh_from_db()
        self.assertIsNotNone(mine.ended_at)
        self.assertIsNone(peer_entry.ended_at)
        self.assertGreaterEqual(scoped.actual_duration, timedelta(minutes=2))
        self.assertEqual(scoped.status, Task.Status.IN_PROGRESS)
        self.assertEqual(scoped.notes, "Confidential shared notes")
        start_focus(task=own_task, user=self.manager)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.manager, ended_at__isnull=True).count(), 1)


class FocusStartConcurrencyTests(TransactionTestCase):
    def test_simultaneous_cross_task_starts_have_one_winner(self):
        user = get_user_model().objects.create_user("focus-concurrent")
        user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker", codename__in=("view_task", "change_task")))
        first = Task.objects.create(title="Concurrent first", owner=user)
        second = Task.objects.create(title="Concurrent second", owner=user)
        barrier = Barrier(2)
        def start(task_id):
            close_old_connections()
            try:
                with connections["default"].cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                    cursor.execute("SET statement_timeout = '10s'")
                actor = get_user_model().objects.get(pk=user.pk)
                task = Task.objects.get(pk=task_id)
                barrier.wait(timeout=5)
                try:
                    return "started", start_focus(task=task, user=actor).task_id
                except ActiveTimerConflict as exc:
                    return "conflict", exc.entry.task_id
            finally:
                connections["default"].close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(start, task.pk) for task in (first, second)]
            outcomes = [future.result(timeout=15) for future in futures]
        self.assertEqual(sorted(result[0] for result in outcomes), ["conflict", "started"])
        self.assertEqual(outcomes[0][1], outcomes[1][1])
        self.assertEqual(TaskTimeEntry.objects.filter(user=user, ended_at__isnull=True).count(), 1)
