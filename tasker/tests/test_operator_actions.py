"""A saved recommendation is evidence; fresh authority and feasibility decide."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connections
from django.test import TestCase, TransactionTestCase

from tasker.models import Task, TaskTimeEntry
from tasker.operator_models import OperatorOverride, OperatorSnapshot, OperatorState
from tasker.services.capacity import aware_local, save_availability, save_work_policy
from tasker.services.focus import start_focus, stop_focus
from tasker.services.operator import build_operator
from tasker.services.operator_actions import (
    OperatorActionConflict, block_recommendation, execute_recommendation,
)


class OperatorActionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("operator-action-owner")
        self.peer = get_user_model().objects.create_user("operator-action-peer")
        for user in (self.user, self.peer):
            user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        self.day = date(2030, 1, 7)
        self.now = aware_local(self.day, time(10), "Africa/Harare")
        # The services share Django's timezone module; keep the full review and
        # transactional execution on the same explicit employee-local instant.
        self.clock = patch("django.utils.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        save_work_policy(user=self.user, timezone_name="Africa/Harare", weekdays=list(range(7)),
                         start_time=time(8), end_time=time(17), buffer_minutes=30)
        self.critical = self.task("Resolve sample customer deadline", priority=Task.Priority.CRITICAL)
        self.other = self.task("Prepare sample consumables comparison", priority=Task.Priority.MEDIUM)

    def task(self, title, **changes):
        return Task.objects.create(owner=self.user, title=title,
                                   definition_of_done="Sample outcome explicitly accepted",
                                   estimated_duration=timedelta(minutes=90), **changes)

    def review(self):
        data = build_operator(user=self.user)
        self.assertIsNotNone(data["now"])
        self.assertIsNotNone(data["snapshot"])
        return data

    def execute(self, data, task=None, **options):
        return execute_recommendation(user=self.user, snapshot_id=data["snapshot"].pk,
                                      task_id=(task or data["now"]["task"]).pk, **options)

    def test_current_now_starts_existing_focus_lifecycle_without_ai(self):
        data = self.review()
        entry = self.execute(data)
        self.assertEqual(entry.task_id, self.critical.pk)
        self.assertEqual(entry.user_id, self.user.pk)
        self.assertIsNone(entry.ended_at)
        self.critical.refresh_from_db()
        self.assertEqual(self.critical.status, Task.Status.IN_PROGRESS)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.user, ended_at__isnull=True).count(), 1)
        self.assertEqual(OperatorOverride.objects.get(owner=self.user).action, "START")

    def test_next_requires_explicit_reason_and_preserves_priority_policy(self):
        data = self.review()
        alternative = data["next"][0]["task"]
        with self.assertRaises(OperatorActionConflict):
            self.execute(data, task=alternative)
        with self.assertRaises(OperatorActionConflict):
            self.execute(data, task=alternative, choose_another=True)
        self.assertFalse(TaskTimeEntry.objects.exists())
        entry = self.execute(data, task=alternative, choose_another=True,
                             reason="Customer confirmed a sample demonstration window")
        self.assertEqual(entry.task_id, alternative.pk)
        audit = OperatorOverride.objects.get(owner=self.user)
        self.assertEqual(audit.action, "CHOOSE_ANOTHER")
        self.assertEqual(audit.reason, "Customer confirmed a sample demonstration window")
        alternative.refresh_from_db()
        self.assertEqual(alternative.priority, Task.Priority.MEDIUM)

    def test_replayed_snapshot_cannot_create_a_second_timer(self):
        data = self.review()
        first = self.execute(data)
        try:
            repeated = self.execute(data)
        except OperatorActionConflict:
            repeated = first
        self.assertEqual(first.pk, repeated.pk)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.user).count(), 1)
        self.assertEqual(OperatorOverride.objects.filter(owner=self.user, action="START").count(), 1)

    def test_expired_or_foreign_snapshot_cannot_start_work(self):
        data = self.review()
        with self.assertRaises(OperatorActionConflict):
            execute_recommendation(user=self.peer, snapshot_id=data["snapshot"].pk, task_id=self.critical.pk)
        OperatorSnapshot.objects.filter(pk=data["snapshot"].pk).update(expires_at=self.now - timedelta(seconds=1))
        with self.assertRaisesMessage(OperatorActionConflict, "expired"):
            self.execute(data)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_direct_same_timestamp_task_edit_fails_content_proof(self):
        data = self.review()
        previous = OperatorState.objects.get(owner=self.user).revision
        Task.objects.filter(pk=self.critical.pk).update(definition_of_done="Revised acceptance criteria")
        self.assertEqual(OperatorState.objects.get(owner=self.user).revision, previous)
        with self.assertRaises(OperatorActionConflict):
            self.execute(data)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_expiry_during_fresh_review_cannot_extend_the_recommendation(self):
        data = self.review()
        with patch("django.utils.timezone.now", return_value=self.now) as clock:
            def delayed_review(**kwargs):
                result = build_operator(**kwargs)
                clock.return_value = self.now + timedelta(minutes=6)
                return result
            with patch("tasker.services.operator_actions.build_operator", side_effect=delayed_review):
                with self.assertRaisesMessage(OperatorActionConflict, "expired while"):
                    self.execute(data)
        self.assertFalse(TaskTimeEntry.objects.exists())
        self.assertFalse(OperatorOverride.objects.exists())

    def test_direct_assignment_or_terminal_mutation_cannot_execute_stale_now(self):
        data = self.review()
        Task.objects.filter(pk=self.critical.pk).update(owner=self.peer)
        with self.assertRaises(OperatorActionConflict):
            self.execute(data, task=self.critical)
        data = self.review()
        Task.objects.filter(pk=self.other.pk).update(status=Task.Status.COMPLETED, progress=100)
        with self.assertRaises(OperatorActionConflict):
            self.execute(data, task=self.other)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_current_capability_is_rechecked_without_cached_permissions(self):
        data = self.review()
        self.user.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="change_task"))
        with self.assertRaises(PermissionDenied):
            self.execute(data)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_new_unavailability_invalidates_review_without_private_note_leak(self):
        data = self.review()
        save_availability(user=self.user, kind="LEAVE", starts_at=self.now,
                          ends_at=self.now + timedelta(hours=1), private_note="Private sample exclusion detail")
        with self.assertRaises(OperatorActionConflict) as exc:
            self.execute(data)
        self.assertNotIn("Private sample exclusion detail", str(exc.exception))
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_passing_work_window_expires_eligibility_even_before_snapshot_expiry(self):
        data = self.review()
        # Set a long expiry to prove that a snapshot token never authorizes
        # execution after the actual feasible work window has ended.
        OperatorSnapshot.objects.filter(pk=data["snapshot"].pk).update(expires_at=self.now + timedelta(days=1))
        with patch("django.utils.timezone.now", return_value=aware_local(self.day, time(18), "Africa/Harare")):
            with self.assertRaises(OperatorActionConflict):
                self.execute(data)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_existing_focus_is_never_implicitly_switched_to_a_critical_task(self):
        start_focus(task=self.other, user=self.user)
        data = self.review()
        self.assertEqual(data["now"]["task_id"], self.other.pk)
        candidate_ids = [item["task_id"] for item in data["next"]]
        if self.critical.pk in candidate_ids:
            with self.assertRaisesMessage(OperatorActionConflict, "Focus"):
                self.execute(data, task=self.critical, choose_another=True,
                             reason="Review sample urgent work without silently switching")
        self.assertEqual(TaskTimeEntry.objects.get(user=self.user, ended_at__isnull=True).task_id, self.other.pk)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.user).count(), 1)

    def test_blocked_work_remains_waiting_and_operator_offers_alternative(self):
        data = self.review()
        with self.assertRaises(OperatorActionConflict):
            block_recommendation(user=self.user, snapshot_id=data["snapshot"].pk,
                                 task_id=self.critical.pk, reason="  ")
        block_recommendation(user=self.user, snapshot_id=data["snapshot"].pk,
                             task_id=self.critical.pk, reason="Waiting for sample purchase approval")
        refreshed = build_operator(user=self.user)
        self.assertEqual(refreshed["now"]["task_id"], self.other.pk)
        self.assertIn(self.critical.pk, [item["task_id"] for item in refreshed["waiting"]])
        self.critical.refresh_from_db()
        self.assertEqual(self.critical.status, Task.Status.BLOCKED)
        self.assertEqual(self.critical.blocked_reason, "Waiting for sample purchase approval")
        self.assertEqual(OperatorOverride.objects.get(action="BLOCK").task_id, self.critical.pk)

    def test_malformed_ids_and_oversized_reasons_do_not_mutate_work(self):
        data = self.review()
        for value in (True, "-1", "1.0", "1" * 100, "\u0661"):
            with self.subTest(value=value), self.assertRaises(OperatorActionConflict):
                execute_recommendation(user=self.user, snapshot_id=data["snapshot"].pk, task_id=value)
        with self.assertRaises(OperatorActionConflict):
            execute_recommendation(user=self.user, snapshot_id="bad-token", task_id=self.critical.pk)
        with self.assertRaises(OperatorActionConflict):
            self.execute(data, task=self.other, choose_another=True, reason="x" * 1001)
        self.assertFalse(TaskTimeEntry.objects.exists())

    def test_finished_task_provides_a_new_review_before_next_execution(self):
        data = self.review()
        self.execute(data)
        stop_focus(task=self.critical, user=self.user)
        from tasker.services.focus import complete_focus, completion_definition_version
        self.critical.refresh_from_db()
        complete_focus(task=self.critical, user=self.user, accept_definition=True,
                       definition_version=completion_definition_version(self.critical))
        fresh = self.review()
        self.assertEqual(fresh["now"]["task_id"], self.other.pk)
        self.assertNotEqual(fresh["snapshot"].pk, data["snapshot"].pk)
        self.execute(fresh)
        self.assertEqual(TaskTimeEntry.objects.get(user=self.user, ended_at__isnull=True).task_id, self.other.pk)


class OperatorStartConcurrencyTests(TransactionTestCase):
    """Real PostgreSQL actor locks serialize competing operator Starts."""

    def test_two_requests_start_at_most_one_timer_and_one_audit(self):
        user = get_user_model().objects.create_user("concurrent-operator-start")
        user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        now = aware_local(date(2030, 1, 7), time(10), "Africa/Harare")
        with patch("django.utils.timezone.now", return_value=now):
            save_work_policy(user=user, timezone_name="Africa/Harare", weekdays=list(range(7)),
                             start_time=time(8), end_time=time(17), buffer_minutes=30)
            task = Task.objects.create(owner=user, title="Concurrent sample operator work",
                                       definition_of_done="Accepted sample result",
                                       estimated_duration=timedelta(minutes=60))
            data = build_operator(user=user)
            barrier = Barrier(2)

            def execute():
                close_old_connections()
                try:
                    actor = get_user_model().objects.get(pk=user.pk)
                    barrier.wait(timeout=10)
                    try:
                        return execute_recommendation(user=actor, snapshot_id=data["snapshot"].pk, task_id=task.pk).pk
                    except OperatorActionConflict:
                        return None
                finally:
                    connections.close_all()

            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: execute(), range(2)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(TaskTimeEntry.objects.filter(user=user, ended_at__isnull=True).count(), 1)
        self.assertEqual(OperatorOverride.objects.filter(owner=user, action="START").count(), 1)
