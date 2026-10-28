"""Deterministic guidance: real hard gates, sticky focus and private evidence."""
from datetime import date, time, timedelta
import json
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from accounting.models import Company
from tasker.goal_models import Goal, Initiative, TaskContribution
from tasker.models import ScheduleBlock, Task, TaskChecklistItem, TaskTimeEntry, Team, TeamMembership, WorkPolicy
from tasker.operator_models import DEFAULT_WEIGHTS, OperatorOverride, OperatorPolicy, OperatorSnapshot, OperatorState
from tasker.services.capacity import aware_local, save_availability, save_work_policy
from tasker.services.dependencies import add_dependency, readiness_for
from tasker.services.focus import complete_focus, start_focus
from tasker.services.operator import OperatorConflict, build_operator, save_operator_policy


class OperatorTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("operator-owner")
        cls.peer = get_user_model().objects.create_user("operator-peer")
        for user in (cls.owner, cls.peer):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        cls.company = Company.objects.create(name="Operator test company")
        cls.team = Team.objects.create(name="Operator test team", company=cls.company)
        cls.membership = TeamMembership.objects.create(user=cls.owner, team=cls.team)

    def setUp(self):
        self.day = date(2030, 1, 7)
        self.now = self.at(10)
        self.clock = patch("tasker.services.operator.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.policy = save_work_policy(user=self.owner, timezone_name="Africa/Harare", weekdays=list(range(7)),
                                       start_time=time(8), end_time=time(16), buffer_minutes=60)

    def at(self, hour, minute=0):
        return aware_local(self.day, time(hour, minute), "Africa/Harare")

    def task(self, **changes):
        return Task.objects.create(**{"owner": self.owner, "title": "Operator sample task",
            "estimated_duration": timedelta(minutes=60), "definition_of_done": "Outcome reviewed and accepted.", **changes})

    def review(self, **changes):
        return build_operator(user=self.owner, now=self.now, **changes)

    def test_empty_and_all_blocked_produce_no_executable_now(self):
        self.assertIsNone(self.review()["now"])
        task = self.task(status="BLOCKED", blocked_reason="Waiting for sample approval", priority="CRITICAL")
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["waiting"][0]["task_id"], task.pk)
        self.assertFalse(result["waiting"][0]["can_start"])
        self.assertEqual(result["exclusions"]["blocked"], 1)

    def test_undated_critical_leads_feasible_work_even_with_older_long_goal_work(self):
        other = self.task(priority="HIGH", due_date=self.day, estimated_duration=timedelta(minutes=5))
        Task.objects.filter(pk=other.pk).update(created_at=self.now-timedelta(days=80))
        critical = self.task(priority="CRITICAL", due_date=None)
        result = self.review()
        self.assertEqual(result["now"]["task_id"], critical.pk)
        self.assertIn("Critical priority", result["now"]["reason"])

    def test_old_long_work_beats_repeated_fresh_short_jobs(self):
        long = self.task(priority="MEDIUM", estimated_duration=timedelta(hours=12))
        Task.objects.filter(pk=long.pk).update(created_at=self.now-timedelta(days=8))
        for iteration in range(4):
            short = self.task(priority="HIGH", estimated_duration=timedelta(minutes=2), title=f"Fresh short task {iteration}")
            result = self.review(persist=False)
            self.assertEqual(result["now"]["task_id"], long.pk)
            self.assertEqual(result["now"]["slice_minutes"], 25)
            self.assertTrue(result["now"]["fairness_protected"])
            complete_focus(task=short, user=self.owner, accept_definition=True)

    def test_unknown_effort_is_a_decision_never_a_zero_length_now(self):
        task = self.task(estimated_duration=None)
        result = self.review()
        self.assertIsNone(result["now"])
        decision = next(item for item in result["decisions"] if item["task_id"] == task.pk)
        self.assertIsNone(decision["remaining_minutes"])
        self.assertEqual(decision["code"], "EFFORT_UNKNOWN")

    def test_unconfigured_hours_and_closed_hours_do_not_claim_feasibility(self):
        self.task()
        self.policy.configured = False
        self.policy.save()
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["decisions"][0]["code"], "HOURS_UNKNOWN")
        self.policy.configured = True
        self.policy.save()
        result = build_operator(user=self.owner, now=self.at(18))
        self.assertIsNone(result["now"])
        self.assertEqual(result["decisions"][0]["code"], "OUTSIDE_HOURS")

    def test_leave_fixed_commitments_and_insufficient_contiguous_time_gate_work(self):
        self.task()
        save_availability(user=self.owner, kind="LEAVE", starts_at=self.at(9), ends_at=self.at(11), private_note="Private medical detail")
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["decisions"][0]["code"], "OUTSIDE_HOURS")
        self.assertNotIn("Private medical detail", json.dumps(result["snapshot"].exclusions))

    def test_missing_dst_midnight_returns_review_guidance_without_inventing_capacity(self):
        self.task()
        self.policy.timezone_name = "America/Havana"
        self.policy.save()
        now = aware_local(date(2030, 3, 10), time(10), "America/Havana")
        result = build_operator(user=self.owner, now=now)
        self.assertIsNone(result["now"])
        self.assertFalse(result["capacity"]["known"])
        self.assertIsNone(result["capacity"]["usable_minutes"])
        self.assertTrue(result["decisions"])

    def test_overloaded_plan_is_reviewed_before_ranking(self):
        planned = self.task()
        ScheduleBlock.objects.create(task=planned, owner=self.owner, starts_at=self.at(8), ends_at=self.at(16), state="ACCEPTED")
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["decisions"][0]["code"], "OVERCAPACITY")

    def test_current_accepted_reservation_can_supply_its_own_capacity(self):
        planned, other = self.task(priority="LOW"), self.task(priority="CRITICAL")
        # 420 usable minutes are reserved by this task. Unallocated is zero.
        ScheduleBlock.objects.create(task=planned, owner=self.owner, starts_at=self.at(8), ends_at=self.at(15), state="ACCEPTED")
        result = self.review()
        self.assertEqual(result["capacity"]["available_minutes"], 0)
        self.assertEqual(result["now"]["task_id"], planned.pk)
        self.assertFalse(any(item["task_id"] == other.pk for item in result["next"]))

    def test_own_reserved_slice_does_not_cross_a_future_conflicting_block(self):
        planned, later = self.task(priority="HIGH"), self.task(priority="LOW")
        ScheduleBlock.objects.create(task=planned, owner=self.owner, starts_at=self.at(10), ends_at=self.at(12), state="ACCEPTED")
        ScheduleBlock.objects.create(task=later, owner=self.owner, starts_at=self.at(11), ends_at=self.at(12), state="ACCEPTED")
        result = build_operator(user=self.owner, now=self.at(10, 50))
        self.assertIsNone(result["now"])
        self.assertTrue(all(item["code"] == "NO_CAPACITY" for item in result["decisions"]))

    def test_missing_capability_cannot_become_executable(self):
        self.task()
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="change_task"))
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["decisions"][0]["code"], "CAPABILITY")

    def test_terminal_archived_deleted_and_other_owner_are_never_candidates(self):
        self.task(status="COMPLETED", progress=100)
        self.task(status="CANCELLED")
        self.task(archived=True)
        self.task(is_deleted=True)
        self.task(owner=self.peer)
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["counts"]["reviewed"], 0)

    def test_revoked_team_access_does_not_leak_title_or_execute_owned_assignment(self):
        task = self.task(company=self.company, team=self.team, title="Sensitive revoked assignment")
        TeamMembership.objects.filter(pk=self.membership.pk).update(is_active=False)
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertEqual(result["exclusions"]["restricted"], 1)
        self.assertNotIn("Sensitive revoked assignment", str(result["decisions"]))
        self.assertNotIn(task.title, json.dumps(result["snapshot"].candidates))

    def test_unfinished_dependency_retains_waiting_then_moves_to_now(self):
        prerequisite = self.task()
        task = self.task(priority="CRITICAL")
        add_dependency(task=task, prerequisite=prerequisite, user=self.owner,
                       expected_version=readiness_for(task=task, user=self.owner).version)
        result = self.review()
        self.assertEqual(result["now"]["task_id"], prerequisite.pk)
        self.assertEqual(result["waiting"][0]["task_id"], task.pk)
        complete_focus(task=prerequisite, user=self.owner, accept_definition=True)
        result = self.review()
        self.assertEqual(result["now"]["task_id"], task.pk)
        self.assertFalse(result["waiting"])

    def test_healthy_focus_stays_now_and_urgent_work_requires_a_decision(self):
        active = self.task(priority="LOW")
        start_focus(task=active, user=self.owner)
        critical = self.task(priority="CRITICAL")
        result = self.review()
        self.assertTrue(result["focus_active"])
        self.assertEqual(result["now"]["task_id"], active.pk)
        self.assertTrue(result["now"]["is_active_focus"])
        self.assertFalse(result["now"]["can_start"])
        self.assertEqual(result["decisions"][0]["task_id"], critical.pk)
        self.assertEqual(result["decisions"][0]["code"], "URGENT_REVIEW")
        self.assertEqual(TaskTimeEntry.objects.filter(ended_at__isnull=True).count(), 1)

    def test_exhausted_initial_estimate_does_not_interrupt_running_focus(self):
        task = self.task()
        Task.objects.filter(pk=task.pk).update(created_at=self.now-timedelta(days=12))
        entry = start_focus(task=task, user=self.owner)
        TaskTimeEntry.objects.filter(pk=entry.pk).update(started_at=self.now-timedelta(minutes=1))
        result = self.review()
        self.assertEqual(result["now"]["task_id"], task.pk)
        self.assertTrue(any(item.get("code") == "FOCUS_EFFORT_REVIEW" for item in result["decisions"]))
        self.assertIsNone(result["now"]["features"]["effort"])
        entry.refresh_from_db()
        self.assertIsNone(entry.ended_at)

    def test_running_focus_near_workday_end_is_reviewed_without_autostopping(self):
        task = self.task()
        entry = start_focus(task=task, user=self.owner)
        result = build_operator(user=self.owner, now=self.at(15, 50))
        self.assertIsNone(result["now"])
        self.assertTrue(result["focus_active"])
        self.assertTrue(any(item.get("code") == "FOCUS_REVIEW" for item in result["decisions"]))
        entry.refresh_from_db()
        self.assertIsNone(entry.ended_at)

    def test_invalid_running_focus_is_explicitly_reviewed_without_replacement(self):
        task = self.task()
        entry = start_focus(task=task, user=self.owner)
        Task.objects.filter(pk=task.pk).update(archived=True)
        self.task(priority="CRITICAL")
        result = self.review()
        self.assertIsNone(result["now"])
        self.assertTrue(result["focus_active"])
        self.assertEqual(result["decisions"][0]["code"], "FOCUS_REVIEW")
        entry.refresh_from_db()
        self.assertIsNone(entry.ended_at)

    def test_first_action_is_live_checklist_and_missing_done_is_explicit(self):
        task = self.task(definition_of_done="")
        TaskChecklistItem.objects.create(task=task, title="Completed source step", position=0, is_completed=True)
        TaskChecklistItem.objects.create(task=task, title="Check the measured sample", position=1)
        result = self.review()
        self.assertEqual(result["now"]["first_action"], "Check the measured sample")
        self.assertEqual(result["now"]["definition_of_done"], "")
        self.assertTrue(any(item["code"] == "DEFINE_DONE" for item in result["decisions"]))

    def test_snapshot_has_version_expiry_exclusions_and_no_private_source_bodies(self):
        task = self.task(description="Private description body", notes="Private shared note body", definition_of_done="Private detailed acceptance body")
        result = self.review()
        snapshot = result["snapshot"]
        self.assertEqual(snapshot.selected_task_id, task.pk)
        self.assertEqual(snapshot.expires_at, self.now+timedelta(minutes=5))
        self.assertEqual(snapshot.policy["version"], "tasker-operator-v1")
        self.assertEqual(snapshot.candidates[0]["queue"], "NOW")
        encoded = json.dumps(snapshot.candidates)
        for text in (task.title, task.description, task.notes, task.definition_of_done):
            self.assertNotIn(text, encoded)
        self.assertEqual(len(snapshot.candidates[0]["task_version"]), 64)
        self.assertEqual(snapshot.state_revision, OperatorState.objects.get(owner=self.owner).revision)
        snapshot.exclusions = {}
        with self.assertRaises(ValidationError): snapshot.save()

    def test_freshness_digest_is_stable_across_routine_clock_ticks_but_changes_for_state(self):
        task = self.task()
        before = self.review(persist=False)
        after = build_operator(user=self.owner, now=self.now+timedelta(seconds=30), persist=False)
        self.assertEqual(before["source_digest"], after["source_digest"])
        Task.objects.filter(pk=task.pk).update(priority="CRITICAL")
        self.assertNotEqual(before["source_digest"], self.review(persist=False)["source_digest"])

    def test_direct_checklist_edits_change_the_first_action_proof(self):
        task = self.task()
        step = TaskChecklistItem.objects.create(task=task, title="Canonical first action")
        before = self.review(persist=False)
        TaskChecklistItem.objects.filter(pk=step.pk).update(title="Changed first action")
        after = self.review(persist=False)
        self.assertNotEqual(before["now"]["first_action_version"], after["now"]["first_action_version"])
        self.assertNotEqual(before["source_digest"], after["source_digest"])

    def test_sources_changing_during_a_review_expire_the_mixed_snapshot(self):
        self.task()
        from tasker.services import operator as operator_module
        original = operator_module._scope_access

        def moving_scope(user, tasks):
            result = original(user, tasks)
            import uuid
            OperatorState.objects.filter(owner=user).update(revision=uuid.uuid4())
            return result

        with patch("tasker.services.operator._scope_access", side_effect=moving_scope):
            result = self.review()
        self.assertIsNone(result["now"])
        self.assertFalse(result["next"])
        self.assertEqual(result["decisions"][0]["code"], "SOURCES_CHANGED")
        self.assertTrue(result["freshness"]["stale"])
        self.assertEqual(result["snapshot"].expires_at, self.now)
        self.assertTrue(all(item["queue"] == "EXPIRED" for item in result["snapshot"].candidates))

    def test_explicit_configuration_is_versioned_audited_and_keeps_critical_first(self):
        weights = {**DEFAULT_WEIGHTS, "goal": 100, "urgency": 0}
        saved = save_operator_policy(user=self.owner, weights=weights, expected_revision="default-v1")
        self.assertEqual(OperatorOverride.objects.get(action="POLICY_CHANGED").details["after"], weights)
        self.assertEqual(self.review()["policy"]["revision"], str(saved.revision))
        with self.assertRaises(OperatorConflict):
            save_operator_policy(user=self.owner, weights=weights, expected_revision="default-v1")
        for bad in ({**weights, "age": 0}, {**weights, "goal": True}, {"age": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                save_operator_policy(user=self.owner, weights=bad, expected_revision=str(saved.revision))
        self.assertEqual(OperatorPolicy.objects.count(), 1)

    def test_active_goal_link_is_evidenced_without_invented_impact(self):
        task = self.task(company=self.company, team=self.team)
        goal = Goal.objects.create(company=self.company, team=self.team, title="Accepted output goal", status="ACTIVE")
        initiative = Initiative.objects.create(goal=goal, title="Sample initiative")
        TaskContribution.objects.create(task=task, initiative=initiative)
        result = self.review()
        self.assertEqual(result["now"]["goal_id"], goal.pk)
        self.assertTrue(result["now"]["goal_impact_known"])
        other = self.task(priority="CRITICAL")
        result = self.review()
        self.assertEqual(result["now"]["task_id"], other.pk)
        self.assertIsNone(result["now"]["features"]["goal"])
        self.assertFalse(result["now"]["goal_impact_known"])

    def test_more_than_100_tasks_disclose_omissions_and_retain_undated_critical(self):
        for i in range(105): self.task(title=f"Bounded sample {i}")
        critical = self.task(priority="CRITICAL", due_date=None)
        result = self.review()
        self.assertEqual(result["now"]["task_id"], critical.pk)
        self.assertEqual(result["counts"]["reviewed"], 100)
        self.assertEqual(result["counts"]["omitted"], 6)
        self.assertTrue(any(item["code"] == "CANDIDATE_LIMIT" for item in result["decisions"]))

    def test_many_old_blocked_critical_tasks_do_not_hide_new_ready_undated_critical(self):
        for i in range(105):
            self.task(title=f"Blocked critical sample {i}", priority="CRITICAL", status="BLOCKED", blocked_reason="Awaiting supplied sample")
        self.task(priority="MEDIUM")
        critical = self.task(priority="CRITICAL", due_date=None)
        result = self.review()
        self.assertEqual(result["now"]["task_id"], critical.pk)
        self.assertEqual(result["counts"]["reviewed"], 100)
        self.assertEqual(result["counts"]["omitted"], 7)
        self.assertTrue(result["waiting"])

    def test_unlock_evidence_ignores_owned_assignments_after_scope_revocation(self):
        from tasker.services.operator import _unlocked_work
        prerequisite = self.task(company=self.company, team=self.team)
        dependent = self.task(company=self.company, team=self.team)
        add_dependency(task=dependent, prerequisite=prerequisite, user=self.owner,
                       expected_version=readiness_for(task=dependent, user=self.owner).version)
        self.assertEqual(_unlocked_work(self.owner, [prerequisite]), {prerequisite.pk: 1})
        TeamMembership.objects.filter(pk=self.membership.pk).update(is_active=False)
        self.assertEqual(_unlocked_work(self.owner, [prerequisite]), {})

    def test_batch_queries_do_not_grow_per_candidate(self):
        self.task()
        with CaptureQueriesContext(connection) as one:
            self.review(persist=False)
        for i in range(30):
            task = self.task(title=f"Batch candidate {i}")
            TaskChecklistItem.objects.create(task=task, title="First canonical step")
        with CaptureQueriesContext(connection) as many:
            self.review(persist=False)
        self.assertLessEqual(len(many), len(one)+2)
        self.assertLess(len(many), 50)

    def test_persist_false_has_no_operator_record_side_effects(self):
        self.task()
        result = self.review(persist=False)
        self.assertIsNotNone(result["now"])
        self.assertFalse(OperatorState.objects.exists())
        self.assertFalse(OperatorSnapshot.objects.exists())
        self.assertFalse(OperatorPolicy.objects.exists())
