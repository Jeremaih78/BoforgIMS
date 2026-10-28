"""Schedule preview/acceptance, lifecycle scope and actual serialization."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, time, timedelta, timezone as datetime_timezone
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, connections, transaction
from django.test import TestCase, TransactionTestCase

from accounting.models import Company
from tasker.models import PlanningEvent, ScheduleBlock, Task, Team, TeamMembership, WorkPolicy
from tasker.services.capacity import PlanningConflict, aware_local, capacity_for_day, save_availability, save_work_policy
from tasker.services.dependencies import add_dependency, readiness_for
from tasker.services.effort import effort_for_task, set_remaining_effort
from tasker.services.focus import complete_focus
from tasker.services.tasks import update_progress
from tasker.services.workbench import apply_work_actions, work_version
from tasker.services.scheduling import (
    ScheduleReviewRequired, accept_schedule, cancel_schedule, invalidate_task_schedule,
    preview_schedule, save_schedule_draft,
)


class SchedulingTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("schedule-owner")
        cls.peer = get_user_model().objects.create_user("schedule-peer")
        cls.manager = get_user_model().objects.create_user("schedule-manager")
        for user in (cls.user, cls.peer, cls.manager): user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        cls.company = Company.objects.create(name="Scheduling sample company")
        cls.team = Team.objects.create(company=cls.company,name="Scheduling sample team")
        TeamMembership.objects.create(team=cls.team,user=cls.user)
        cls.management = TeamMembership.objects.create(team=cls.team,user=cls.manager,role="MANAGER")

    def setUp(self):
        self.day = date(2030,1,7)
        self.now = aware_local(self.day,time(0),"Africa/Harare").astimezone(datetime_timezone.utc)
        self.clock = patch("tasker.services.scheduling.timezone.now",return_value=self.now)
        self.clock.start(); self.addCleanup(self.clock.stop)
        self.policy = save_work_policy(user=self.user,timezone_name="Africa/Harare",weekdays=list(range(7)),start_time=time(8),end_time=time(16),buffer_minutes=60)

    def at(self,hour,minute=0,day=None): return aware_local(day or self.day,time(hour,minute),"Africa/Harare")

    def task(self,**changes): return Task.objects.create(**{"owner":self.user,"title":"Scheduled sample work","estimated_duration":timedelta(minutes=600),**changes})

    def draft(self,task=None,start=8,end=9,**changes):
        task = task or self.task()
        return save_schedule_draft(user=self.user,task=task,starts_at=self.at(start),ends_at=self.at(end),**changes)

    def preview(self,block,user=None): return preview_schedule(user=user or self.user,task=block.task,starts_at=block.starts_at,ends_at=block.ends_at,block=block)

    def accept(self,block,*,user=None,reason="",preview=None):
        preview = preview or self.preview(block,user)
        return accept_schedule(user=user or self.user,block=block,expected_version=str(block.revision),preview_version=preview["version"],override_reason=reason)

    def test_draft_is_not_a_commitment_and_clean_acceptance_is_audited(self):
        block = self.draft()
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)
        preview = self.preview(block)
        self.assertEqual(preview["warnings"],[]); self.assertEqual(preview["hard_errors"],[])
        accepted = self.accept(block,preview=preview)
        self.assertEqual(accepted.state,"ACCEPTED")
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],60)
        self.assertTrue(PlanningEvent.objects.filter(action="schedule.accepted").exists())

    def test_overloaded_plan_is_rejected_until_explicit_reason_is_recorded(self):
        block = self.draft(start=8,end=16)
        preview = self.preview(block)
        self.assertIn("CAPACITY_OVERLOAD",[item["code"] for item in preview["warnings"]])
        with self.assertRaises(ScheduleReviewRequired): self.accept(block,preview=preview)
        block.refresh_from_db(); self.assertEqual(block.state,"DRAFT")
        accepted = self.accept(block,reason="Authorized sample launch window; move follow-up work")
        self.assertEqual(accepted.state,"ACCEPTED")
        self.assertEqual(accepted.acceptance_snapshot["warnings"][0]["code"],"CAPACITY_OVERLOAD")
        self.assertTrue(PlanningEvent.objects.get(action="schedule.accepted").details["override_reason"])

    def test_unknown_capacity_and_estimate_are_not_accepted_as_zero(self):
        self.policy.configured=False; self.policy.save()
        block = self.draft(task=self.task(estimated_duration=None))
        preview = self.preview(block)
        codes = [item["code"] for item in preview["warnings"]]
        self.assertIn("CAPACITY_UNKNOWN",codes); self.assertIn("EFFORT_UNKNOWN",codes)
        self.assertIsNone(preview["effort"]["remaining_minutes"])
        with self.assertRaises(ScheduleReviewRequired): self.accept(block)

    def test_fixed_commitment_and_leave_conflicts_are_explicit_without_private_details(self):
        save_availability(user=self.user,kind="LEAVE",starts_at=self.at(9),ends_at=self.at(10),private_note="Restricted leave explanation")
        block = self.draft(start=8,end=10)
        preview = self.preview(block)
        self.assertIn("AVAILABILITY_CONFLICT",[item["code"] for item in preview["warnings"]])
        self.assertNotIn("Restricted leave explanation",str(preview))
        with self.assertRaises(ScheduleReviewRequired): self.accept(block)

    def test_overlap_and_outside_hours_are_separate_warnings(self):
        self.accept(self.draft(start=8,end=10))
        block = self.draft(start=9,end=17)
        codes = [item["code"] for item in self.preview(block)["warnings"]]
        self.assertIn("BLOCK_CONFLICT",codes); self.assertIn("OUTSIDE_HOURS",codes); self.assertIn("CAPACITY_OVERLOAD",codes)

    def test_stale_preview_after_availability_or_policy_change_is_rejected(self):
        block = self.draft(); preview = self.preview(block)
        save_availability(user=self.user,kind="COMMITMENT",starts_at=self.at(14),ends_at=self.at(15))
        with self.assertRaises(PlanningConflict): self.accept(block,preview=preview,reason="No silent stale override")
        block.refresh_from_db(); self.assertEqual(block.state,"DRAFT")
        preview = self.preview(block)
        save_work_policy(user=self.user,timezone_name="Africa/Harare",weekdays=list(range(7)),start_time=time(8),end_time=time(16),buffer_minutes=90,expected_version=str(self.policy.revision))
        with self.assertRaises(PlanningConflict): self.accept(block,preview=preview)

    def test_stale_same_clock_task_and_effort_changes_invalidate_the_preview(self):
        task = self.task(); block = self.draft(task=task); preview = self.preview(block)
        Task.objects.filter(pk=task.pk).update(notes="Newer context at the same timestamp")
        with self.assertRaises(PlanningConflict): self.accept(block,preview=preview)
        preview = self.preview(block); task.refresh_from_db()
        set_remaining_effort(user=self.user,task=task,remaining_minutes=120,expected_version=effort_for_task(task=task)["version"])
        with self.assertRaises(PlanningConflict): self.accept(block,preview=preview)

    def test_moving_or_accepting_a_block_preserves_the_task_deadline(self):
        task = self.task(due_date=self.day,due_time=time(9))
        block = self.draft(task=task,start=10,end=11)
        self.assertIn("AFTER_DEADLINE",[item["code"] for item in self.preview(block)["warnings"]])
        accepted = self.accept(block,reason="Review sample deadline separately")
        moved = save_schedule_draft(user=self.user,task=task,starts_at=self.at(12),ends_at=self.at(13),block=accepted,expected_version=str(accepted.revision))
        self.assertEqual(moved.state,"DRAFT")
        task.refresh_from_db(); self.assertEqual((task.due_date,task.due_time),(self.day,time(9)))

    def test_dependencies_are_hard_constraints_and_reopening_recomputes_readiness(self):
        task, prerequisite = self.task(),self.task(title="Prerequisite sample")
        add_dependency(task=task,prerequisite=prerequisite,user=self.user,expected_version=readiness_for(task=task,user=self.user).version)
        block = self.draft(task=task)
        self.assertFalse(self.preview(block)["can_accept"])
        with self.assertRaises(ScheduleReviewRequired): self.accept(block,reason="Cannot override readiness")
        prerequisite.status="COMPLETED"; prerequisite.completed_at=self.now; prerequisite.save()
        fresh = self.preview(block); self.assertTrue(fresh["can_accept"])
        prerequisite.status="PENDING"; prerequisite.completed_at=None; prerequisite.save()
        with self.assertRaises(PlanningConflict): self.accept(block,preview=fresh)

    def test_blocked_terminal_and_past_work_cannot_be_overridden(self):
        for changes in ({"status":"BLOCKED","blocked_reason":"Awaiting answer"},{"status":"COMPLETED"},{"archived":True}):
            task = self.task(**changes); block = self.draft(task=task)
            self.assertFalse(self.preview(block)["can_accept"])
            with self.assertRaises(ScheduleReviewRequired): self.accept(block,reason="Override cannot bypass lifecycle")
        task = self.task()
        block = save_schedule_draft(user=self.user,task=task,starts_at=self.at(8,day=self.day-timedelta(days=1)),ends_at=self.at(9,day=self.day-timedelta(days=1)))
        self.assertIn("PAST_BLOCK",[item["code"] for item in self.preview(block)["hard_errors"]])
        with self.assertRaises(ScheduleReviewRequired): self.accept(block,reason="No backdated commitments")

    def test_current_team_manager_can_plan_but_peer_and_revoked_manager_cannot(self):
        task = self.task(company=self.company,team=self.team)
        block = save_schedule_draft(user=self.manager,task=task,starts_at=self.at(8),ends_at=self.at(9))
        with self.assertRaises(PermissionDenied): self.preview(block,self.peer)
        self.assertEqual(self.accept(block,user=self.manager).state,"ACCEPTED")
        TeamMembership.objects.filter(pk=self.management.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied): self.preview(block,self.manager)

    def test_cancel_and_task_invalidation_are_recoverable_and_free_capacity(self):
        accepted = self.accept(self.draft())
        with self.assertRaises(PlanningConflict): cancel_schedule(user=self.user,block=accepted,expected_version="stale")
        cancelled = cancel_schedule(user=self.user,block=accepted,expected_version=str(accepted.revision))
        self.assertEqual(cancelled.state,"CANCELLED")
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)
        accepted = self.accept(self.draft())
        with transaction.atomic():
            task = Task.objects.select_for_update().get(pk=accepted.task_id)
            invalidate_task_schedule(task=task,actor=self.user,reason="Sample lifecycle change")
        accepted.refresh_from_db(); self.assertEqual(accepted.state,"CANCELLED")
        self.assertTrue(accepted.acceptance_snapshot)
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)

    def test_direct_reassignment_or_deletion_never_consumes_former_owner_capacity(self):
        task = self.task(); block = self.accept(self.draft(task=task))
        Task.objects.filter(pk=task.pk).update(owner=self.peer)
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)
        with self.assertRaises(PermissionDenied): self.accept(block)
        task = self.task(); block = self.accept(self.draft(task=task))
        Task.objects.filter(pk=task.pk).update(is_deleted=True)
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)

    def test_concurrent_edit_proofs_and_replayed_acceptance_cannot_overwrite(self):
        block = self.draft(); original = str(block.revision)
        updated = save_schedule_draft(user=self.user,task=block.task,block=block,expected_version=original,starts_at=self.at(10),ends_at=self.at(11))
        with self.assertRaises(PlanningConflict): save_schedule_draft(user=self.user,task=block.task,block=block,expected_version=original,starts_at=self.at(12),ends_at=self.at(13))
        preview = self.preview(updated)
        accepted = self.accept(updated,preview=preview)
        with self.assertRaises(PlanningConflict): accept_schedule(user=self.user,block=accepted,expected_version=str(updated.revision),preview_version=preview["version"])
        self.assertEqual(PlanningEvent.objects.filter(action="schedule.accepted").count(),1)

    def test_completion_then_reopening_does_not_revive_the_cancelled_plan(self):
        task=self.task(); block=self.accept(self.draft(task=task))
        complete_focus(task=task,user=self.user)
        block.refresh_from_db(); self.assertEqual(block.state,"CANCELLED")
        task.refresh_from_db()
        update_progress(task=task,user=self.user,progress=50)
        block.refresh_from_db(); self.assertEqual(block.state,"CANCELLED")
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)

    def test_authorized_assignment_invalidates_old_owner_blocks(self):
        TeamMembership.objects.create(team=self.team,user=self.peer)
        task=self.task(company=self.company,team=self.team); block=self.accept(self.draft(task=task))
        apply_work_actions(user=self.manager,task_ids=[task.pk],action="assign",value=str(self.peer.pk),versions={str(task.pk):work_version(task)})
        task.refresh_from_db(); block.refresh_from_db()
        self.assertEqual(task.owner_id,self.peer.pk)
        self.assertEqual(block.state,"CANCELLED")
        self.assertTrue(block.acceptance_snapshot)
        self.assertEqual(capacity_for_day(user=self.user,day=self.day)["committed_minutes"],0)


class ConcurrentScheduleAcceptanceTests(TransactionTestCase):
    reset_sequences = True

    def test_different_task_acceptance_serializes_the_shared_owner_capacity(self):
        user = get_user_model().objects.create_user("concurrent-schedule-owner")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        day = date(2030,1,7)
        at = lambda hour: aware_local(day,time(hour),"Africa/Harare")
        save_work_policy(user=user,timezone_name="Africa/Harare",weekdays=list(range(7)),start_time=time(8),end_time=time(16),buffer_minutes=0)
        blocks = []
        for index in range(2):
            task = Task.objects.create(owner=user,title=f"Concurrent sample {index}",estimated_duration=timedelta(hours=8))
            block = save_schedule_draft(user=user,task=task,starts_at=at(8),ends_at=at(14))
            preview = preview_schedule(user=user,task=task,starts_at=block.starts_at,ends_at=block.ends_at,block=block)
            blocks.append((block.pk,str(block.revision),preview["version"]))
        barrier = Barrier(2)
        def worker(data):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET statement_timeout = '7000ms'")
                    cursor.execute("SET lock_timeout = '5000ms'")
                actor = get_user_model().objects.get(pk=user.pk)
                block = ScheduleBlock.objects.get(pk=data[0])
                barrier.wait(timeout=5)
                try:
                    accept_schedule(user=actor,block=block,expected_version=data[1],preview_version=data[2])
                    return "accepted"
                except PlanningConflict:
                    return "stale"
            finally:
                connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker,data) for data in blocks]
            result = [future.result(timeout=15) for future in futures]
        self.assertCountEqual(result,["accepted","stale"])
        self.assertEqual(ScheduleBlock.objects.filter(state="ACCEPTED").count(),1)
        self.assertEqual(capacity_for_day(user=user,day=day)["committed_minutes"],360)
