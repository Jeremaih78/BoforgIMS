"""Deterministic scenario previews, fresh transactional commits and safe attention."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta
from threading import Barrier, Event
from unittest.mock import patch
import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase, override_settings

from accounting.models import Company
from tasker.models import (Availability, DailyPlan, Notification, NotificationDelivery, OperatorState,
    OutboxJob, ScheduleBlock, Task, TaskActivity, TaskTimeEntry, Team, TeamMembership, WorkPolicy)
from tasker.scenario_models import StrategyScenario, StrategyScenarioEvent
from tasker.services.capacity import aware_local
from tasker.services.scenarios import (ScenarioConflict, commit_scenario, create_scenario,
    discard_scenario, get_scenario, visible_scenarios_for)


class ScenarioFixture:
    def setup_scope(self):
        self.manager = get_user_model().objects.create_user("scenario-manager")
        self.worker = get_user_model().objects.create_user("scenario-worker")
        self.recipient = get_user_model().objects.create_user("scenario-recipient")
        self.outsider = get_user_model().objects.create_user("scenario-outsider")
        for user in (self.manager, self.worker, self.recipient, self.outsider):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        self.company = Company.objects.create(name="Scenario sample company")
        self.team = Team.objects.create(company=self.company, name="Scenario sample team")
        self.management = TeamMembership.objects.create(team=self.team, user=self.manager, role="MANAGER")
        for user in (self.worker, self.recipient):
            TeamMembership.objects.create(team=self.team, user=user)
            WorkPolicy.objects.create(owner=user, configured=True, timezone_name="Africa/Harare",
                weekdays=list(range(7)), start_time=time(8), end_time=time(16), buffer_minutes=0)
        self.day = date(2030, 1, 7)
        self.now = self.at(10)

    def at(self, hour, minute=0, day=None):
        return aware_local(day or self.day, time(hour, minute), "Africa/Harare")

    def task(self, **changes):
        return Task.objects.create(**{"owner": self.worker, "company": self.company, "team": self.team,
            "title": "Recorded scenario work", "estimated_duration": timedelta(minutes=60), **changes})

    def change(self, task, **changes):
        return {"task_id": task.pk, "owner_id": self.recipient.pk, "priority": "HIGH",
            "due_date": None, **changes}

    def scenario(self, task=None, changes=None, **values):
        return create_scenario(user=self.manager, team_id=self.team.pk, title="Review moving customer work",
            changes=changes or [self.change(task or self.task())], client_key=values.get("client_key", uuid.uuid4()))

    def commit(self, scenario, **changes):
        return commit_scenario(**{"user": self.manager, "scenario_id": scenario.pk,
            "expected_revision": scenario.revision, **changes})


@override_settings(TASKER_STRATEGY_ENABLED=True)
class StrategyScenarioTests(ScenarioFixture, TestCase):
    def setUp(self):
        self.setup_scope()
        self.clock = patch("django.utils.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def test_preview_writes_only_draft_evidence_and_never_live_work(self):
        task = self.task()
        fields = Task.objects.filter(pk=task.pk).values().get()
        before = (TaskActivity.objects.count(), WorkPolicy.all_objects.count(), OutboxJob.objects.count(),
            OperatorState.objects.count(), ScheduleBlock.objects.count())
        result = self.scenario(task)
        self.assertTrue(result.preview["can_commit"])
        self.assertEqual(Task.objects.filter(pk=task.pk).values().get(), fields)
        self.assertEqual(before, (TaskActivity.objects.count(), WorkPolicy.all_objects.count(), OutboxJob.objects.count(),
            OperatorState.objects.count(), ScheduleBlock.objects.count()))
        self.assertEqual(result.state, "DRAFT")
        self.assertIn("not promised completion", result.preview["uncertainty"])
        self.assertEqual(StrategyScenarioEvent.objects.filter(scenario=result, action="CREATED").count(), 1)

    def test_preview_discard_and_replay_never_change_assignments(self):
        task = self.task()
        scenario = self.scenario(task)
        reviewed = scenario.revision
        discarded = discard_scenario(user=self.manager, scenario_id=scenario.pk, expected_revision=reviewed)
        self.assertEqual(discarded.state, "DISCARDED")
        self.assertEqual(discard_scenario(user=self.manager, scenario_id=scenario.pk, expected_revision=reviewed).pk, scenario.pk)
        task.refresh_from_db()
        self.assertEqual(task.owner_id, self.worker.pk)
        self.assertFalse(Notification.objects.exists())

    def test_duplicate_request_is_one_draft_and_changed_payload_is_rejected(self):
        task, key = self.task(), uuid.uuid4()
        first = self.scenario(task, client_key=key)
        self.assertEqual(self.scenario(task, client_key=key).pk, first.pk)
        with self.assertRaises(ScenarioConflict):
            self.scenario(changes=[self.change(task, priority="LOW")], client_key=key)

    def test_commit_replay_changes_once_and_queues_only_old_and_new_owners(self):
        task = self.task()
        scenario = self.scenario(task)
        reviewed = scenario.revision
        committed = self.commit(scenario)
        task.refresh_from_db()
        self.assertEqual((task.owner_id, task.priority), (self.recipient.pk, "HIGH"))
        self.assertEqual(self.commit(committed, expected_revision=reviewed).revision, committed.revision)
        self.assertEqual(StrategyScenarioEvent.objects.filter(scenario=scenario, action="COMMITTED").count(), 1)
        self.assertEqual(set(Notification.objects.values_list("owner_id", flat=True)), {self.worker.pk, self.recipient.pk})
        self.assertIsNone(Notification.objects.get(owner=self.worker).task_id)
        self.assertEqual(Notification.objects.get(owner=self.recipient).task_id, task.pk)
        self.assertTrue(NotificationDelivery.objects.filter(channel="IN_APP", state="QUEUED").exists())
        self.assertFalse(NotificationDelivery.objects.exclude(channel="IN_APP").exists())
        with self.assertRaises(ScenarioConflict):
            self.commit(committed, expected_revision=uuid.uuid4())

    def test_private_content_is_not_in_evidence_and_unchanged_notes_are_preserved(self):
        task = self.task(notes="PRIVATE_TASK_SENTINEL\r\nsecond line")
        private = Task.objects.create(owner=self.recipient, title="PRIVATE_OTHER_TASK_SENTINEL",
            notes="PRIVATE_OTHER_NOTE_SENTINEL", estimated_duration=timedelta(minutes=10))
        Availability.objects.create(owner=self.recipient, kind="COMMITMENT", starts_at=self.at(14), ends_at=self.at(15),
            private_note="PRIVATE_AVAILABILITY_SENTINEL")
        DailyPlan.objects.create(owner=self.worker, plan_date=self.day, quick_notes="PRIVATE_PLAN_SENTINEL")
        scenario = self.scenario(task)
        saved = json.dumps({"preview": scenario.preview, "proofs": scenario.source_proofs})
        for marker in ("PRIVATE_TASK_SENTINEL", "PRIVATE_OTHER_TASK_SENTINEL", "PRIVATE_OTHER_NOTE_SENTINEL",
                "PRIVATE_AVAILABILITY_SENTINEL", "PRIVATE_PLAN_SENTINEL"):
            self.assertNotIn(marker, saved)
        self.commit(scenario)
        task.refresh_from_db()
        self.assertEqual(task.notes, "PRIVATE_TASK_SENTINEL\r\nsecond line")
        self.assertEqual(DailyPlan.objects.get(owner=self.worker).quick_notes, "PRIVATE_PLAN_SENTINEL")

    def test_private_uncommitted_work_does_not_leak_via_unknown_workload_counts(self):
        task = self.task()
        Task.objects.create(owner=self.recipient, title="PRIVATE_UNPLANNED_SENTINEL", estimated_duration=None)
        scenario = self.scenario(task)
        self.assertTrue(scenario.preview["can_commit"])
        self.assertEqual(scenario.preview["rows"][0]["capacity_after"]["unknown_workload_count"], 0)
        self.assertNotIn("PRIVATE_UNPLANNED_SENTINEL", json.dumps(scenario.preview))

    def test_missing_capacity_or_effort_blocks_reassignment_but_not_metadata_edit(self):
        task = self.task(estimated_duration=None)
        WorkPolicy.objects.filter(owner=self.recipient).update(configured=False)
        scenario = self.scenario(task)
        self.assertFalse(scenario.preview["can_commit"])
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)
        same_owner = self.scenario(changes=[self.change(task, owner_id=self.worker.pk)])
        self.assertTrue(same_owner.preview["can_commit"])
        self.commit(same_owner)
        task.refresh_from_db()
        self.assertEqual(task.priority, "HIGH")

    def test_combined_incoming_work_cannot_claim_capacity_twice(self):
        first, second = self.task(estimated_duration=timedelta(minutes=2000)), self.task(estimated_duration=timedelta(minutes=2000))
        self.assertTrue(self.scenario(first).preview["can_commit"])
        combined = self.scenario(changes=[self.change(first), self.change(second)])
        self.assertFalse(combined.preview["can_commit"])
        with self.assertRaises(ScenarioConflict):
            self.commit(combined)
        self.assertEqual(Task.objects.filter(owner=self.worker).count(), 2)

    def test_existing_accepted_reservation_is_not_double_counted_as_remaining_demand(self):
        existing = self.task(owner=self.recipient, estimated_duration=timedelta(minutes=3000))
        ScheduleBlock.objects.create(task=existing, owner=self.recipient, starts_at=self.at(10), ends_at=self.at(15), state="ACCEPTED")
        scenario = self.scenario(self.task(estimated_duration=timedelta(minutes=100)))
        self.assertTrue(scenario.preview["can_commit"])
        self.assertGreaterEqual(scenario.preview["rows"][0]["capacity_after"]["projected_available_minutes"], 0)

    def test_elapsed_hours_do_not_make_urgent_work_appear_feasible(self):
        task = self.task(estimated_duration=timedelta(minutes=120))
        scenario = self.scenario(changes=[self.change(task, due_date=self.day.isoformat(), due_time="10:30")])
        self.assertFalse(scenario.preview["can_commit"])
        self.assertIn("before this deadline", " ".join(scenario.preview["blockers"]))

    def test_deadline_cannot_shorten_below_an_accepted_block(self):
        task = self.task()
        ScheduleBlock.objects.create(task=task, owner=self.worker, starts_at=self.at(11), ends_at=self.at(12), state="ACCEPTED")
        scenario = self.scenario(changes=[self.change(task, owner_id=self.worker.pk, due_date=self.day.isoformat(), due_time="11:30")])
        self.assertFalse(scenario.preview["can_commit"])
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)

    def test_reassignment_discloses_and_cancels_previous_accepted_blocks(self):
        task = self.task()
        block = ScheduleBlock.objects.create(task=task, owner=self.worker, starts_at=self.at(11), ends_at=self.at(12), state="ACCEPTED")
        scenario = self.scenario(task)
        self.assertEqual(scenario.preview["rows"][0]["displaced_blocks"][0]["id"], block.pk)
        self.commit(scenario)
        block.refresh_from_db()
        self.assertEqual(block.state, "CANCELLED")
        self.assertFalse(ScheduleBlock.objects.filter(task=task, owner=self.recipient, state="ACCEPTED").exists())

    def test_active_focus_and_blocked_readiness_prevent_executable_reassignment(self):
        task = self.task()
        TaskTimeEntry.objects.create(task=task, user=self.worker, started_at=self.now, ended_at=None)
        scenario = self.scenario(task)
        self.assertFalse(scenario.preview["can_commit"])
        self.assertIn("Focus", " ".join(scenario.preview["blockers"]))
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)
        self.assertTrue(TaskTimeEntry.objects.filter(task=task, ended_at__isnull=True).exists())

    def test_human_task_or_capacity_changes_invalidate_preview(self):
        task = self.task()
        scenario = self.scenario(task)
        Task.objects.filter(pk=task.pk).update(title="Changed by the owner")
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)
        current = self.scenario(task)
        Availability.objects.create(owner=self.recipient, kind="LEAVE", starts_at=self.at(11), ends_at=self.at(13))
        with self.assertRaises(ScenarioConflict):
            self.commit(current)

    def test_new_recipient_workload_invalidates_preview(self):
        scenario = self.scenario()
        self.task(owner=self.recipient, title="A new agreed commitment")
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)

    def test_revoked_membership_and_wrong_manager_fail_closed(self):
        scenario = self.scenario()
        TeamMembership.objects.filter(team=self.team, user=self.recipient).update(is_active=False)
        with self.assertRaises(ValidationError):
            self.commit(scenario)
        self.assertFalse(Notification.objects.exists())
        with self.assertRaises(PermissionDenied):
            get_scenario(user=self.outsider, scenario_id=scenario.pk)
        self.assertFalse(visible_scenarios_for(self.outsider).exists())

    def test_inactive_previous_owner_does_not_prevent_recovery_to_qualified_member(self):
        task = self.task()
        TeamMembership.objects.filter(team=self.team, user=self.worker).update(is_active=False)
        get_user_model().objects.filter(pk=self.worker.pk).update(is_active=False)
        scenario = self.scenario(task)
        self.assertTrue(scenario.preview["can_commit"])
        self.assertFalse(scenario.preview["rows"][0]["capacity_before"]["known"])
        self.commit(scenario)
        task.refresh_from_db()
        self.assertEqual(task.owner_id, self.recipient.pk)
        self.assertFalse(Notification.objects.filter(owner=self.worker).exists())

    def test_manager_revocation_hides_source_and_stops_commit_but_owner_may_discard(self):
        scenario = self.scenario()
        self.management.is_active = False
        self.management.save()
        with self.assertRaises(PermissionDenied):
            get_scenario(user=self.manager, scenario_id=scenario.pk)
        with self.assertRaises(PermissionDenied):
            self.commit(scenario)
        self.assertEqual(discard_scenario(user=self.manager, scenario_id=scenario.pk,
            expected_revision=scenario.revision).state, "DISCARDED")

    def test_expired_or_forged_revision_and_evidence_cannot_commit(self):
        scenario = self.scenario()
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario, expected_revision=uuid.uuid4())
        StrategyScenario.objects.filter(pk=scenario.pk).update(preview={"can_commit": True})
        with self.assertRaises(ScenarioConflict):
            self.commit(scenario)
        expired = self.scenario()
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(minutes=16)):
            with self.assertRaises(ScenarioConflict):
                self.commit(expired)

    def test_batch_domain_failure_rolls_back_all_work_and_attention(self):
        first, second = self.task(), self.task()
        scenario = self.scenario(changes=[self.change(first), self.change(second)])
        from tasker.services.tasks import save_task
        calls = []
        def fail_second(**kwargs):
            calls.append(1)
            if len(calls) == 2:
                raise ValidationError("Synthetic final mutation failure")
            return save_task(**kwargs)
        with patch("tasker.services.tasks.save_task", side_effect=fail_second):
            with self.assertRaises(ValidationError):
                self.commit(scenario)
        self.assertEqual(Task.objects.filter(owner=self.worker).count(), 2)
        self.assertFalse(Notification.objects.exists())
        scenario.refresh_from_db()
        self.assertEqual(scenario.state, "DRAFT")

    def test_payload_is_bounded_allowlisted_and_identity_cannot_be_injected(self):
        task = self.task()
        for changes in ([{**self.change(task), "actor_id": self.outsider.pk}],
                [{**self.change(task), "task_id": True}], [self.change(task)]*11,
                [self.change(task, owner_id=self.outsider.pk)], [self.change(task, due_date=None, due_time="11:00")],
                [self.change(task, due_date="9999-12-31")], [self.change(task, due_date="0000-01-01")],
                [self.change(task, due_date=True)]):
            with self.subTest(changes=changes), self.assertRaises(ValidationError):
                self.scenario(changes=changes)

    @override_settings(TASKER_STRATEGY_ENABLED=False)
    def test_rollout_disabled_stops_creation_and_commit_but_allows_owner_discard(self):
        with override_settings(TASKER_STRATEGY_ENABLED=True):
            scenario = self.scenario()
        with self.assertRaises(ValidationError):
            self.scenario()
        with self.assertRaises(ValidationError):
            self.commit(scenario)
        self.assertEqual(discard_scenario(user=self.manager, scenario_id=scenario.pk,
            expected_revision=scenario.revision).state, "DISCARDED")


@override_settings(TASKER_STRATEGY_ENABLED=True)
class ScenarioSerializationTests(ScenarioFixture, TransactionTestCase):
    def test_concurrent_commit_retries_share_one_change_and_notification_set(self):
        if connection.vendor != "postgresql":
            self.skipTest("Scenario row locks require PostgreSQL.")
        self.setup_scope()
        barrier = Barrier(2)
        def commit_once():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=self.manager.pk)
                barrier.wait(timeout=20)
                return str(commit_scenario(user=actor, scenario_id=scenario.pk,
                    expected_revision=scenario.revision).revision)
            finally:
                close_old_connections()
        with patch("django.utils.timezone.now", return_value=self.now):
            scenario = self.scenario()
            with ThreadPoolExecutor(max_workers=2) as executor:
                futures = [executor.submit(commit_once) for _ in range(2)]
                revisions = [future.result(timeout=60) for future in futures]
        self.assertEqual(revisions[0], revisions[1])
        self.assertEqual(StrategyScenarioEvent.objects.filter(scenario=scenario, action="COMMITTED").count(), 1)
        self.assertEqual(Notification.objects.count(), 2)

    def test_busy_task_writer_refuses_commit_without_partial_work(self):
        if connection.vendor != "postgresql":
            self.skipTest("Allocation table fencing requires PostgreSQL.")
        self.setup_scope()
        busy, release = Event(), Event()
        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    Task.objects.create(owner=self.recipient, title="Another allocation is being saved")
                    busy.set()
                    release.wait(timeout=20)
            finally:
                close_old_connections()
        with patch("django.utils.timezone.now", return_value=self.now):
            scenario = self.scenario()
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(writer)
                try:
                    self.assertTrue(busy.wait(timeout=15))
                    with self.assertRaises(ScenarioConflict):
                        self.commit(scenario)
                    self.assertFalse(Notification.objects.exists())
                finally:
                    release.set()
                future.result(timeout=30)
        scenario.refresh_from_db()
        self.assertEqual(scenario.state, "DRAFT")
