"""Reviewed, scoped and reversible AI mutations; no provider is needed."""
from datetime import date, time, timedelta
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounting.models import Company
from tasker.ai.workflow_context import build_workflow_context
from tasker.ai_workflow_models import AIWorkRequest
from tasker.capture_models import CaptureItem
from tasker.models import DailyPlan, DailyReview, Task, TaskActivity, TaskChecklistItem, Team, TeamMembership
from tasker.planning_models import ScheduleBlock, WorkPolicy
from tasker.proposal_models import AIProposal, AIProposalEvent
from tasker.services.ai_proposals import (
    AIProposalConflict, apply_proposal, cancel_proposal, create_proposal,
    get_proposal, proposal_digest, undo_proposal,
)
from tasker.services.capacity import aware_local


class AIProposalTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("proposal-owner")
        cls.other = get_user_model().objects.create_user("proposal-other")
        for user in (cls.owner, cls.other):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))

    def setUp(self):
        self.day = date(2030, 1, 7)
        self.now = aware_local(self.day, time(10), "Africa/Harare")
        clock = patch("tasker.services.ai_proposals.timezone.now", return_value=self.now)
        clock.start()
        self.addCleanup(clock.stop)

    def task(self, **changes):
        return Task.objects.create(**{"owner": self.owner, "title": "Recorded personal work",
            "estimated_duration": timedelta(hours=2), "created_by": self.owner, "updated_by": self.owner, **changes})

    def payload(self, actions, **changes):
        return {"schema_version": "3", "headline": "Review these suggestions", "summary": "Based on recorded work.",
            "reasoning": "Review each action before saving.", "actions": actions,
            "questions": [], "citations": [], "date_hints": [], **changes}

    def proposal(self, feature, actions, *, task=None, capture=None, payload_changes=None):
        context = build_workflow_context(user=self.owner, feature=feature,
            task_id=task.pk if task else None, capture_id=capture.pk if capture else None)
        request = AIWorkRequest.objects.create(owner=self.owner, feature=feature, scope="PERSONAL", task=task,
            capture=capture, status="COMPLETED", context_snapshot=context, context_digest=proposal_digest(context),
            request_digest="a" * 64, expires_at=self.now + timedelta(hours=1))
        return create_proposal(user=self.owner, request_id=request.pk, feature=feature,
            payload=self.payload(actions, **(payload_changes or {})), context=context)

    def checklist(self, task=None, title="Confirm the agreed outcome"):
        task = task or self.task()
        return task, self.proposal("BREAKDOWN", [{"id": "step_1", "kind": "CHECKLIST_ADD", "task_id": task.pk, "title": title}], task=task)

    def apply(self, proposal, ids=None, revision=None):
        return apply_proposal(user=self.owner, proposal_id=proposal.pk,
            expected_revision=revision or proposal.revision,
            selected_action_ids=ids or [action["id"] for action in proposal.payload["actions"]])

    def test_proposal_creation_only_records_preview_and_is_idempotent(self):
        task, proposal = self.checklist()
        self.assertFalse(TaskChecklistItem.objects.exists())
        request = proposal.request
        same = create_proposal(user=self.owner, request_id=request.pk, feature="BREAKDOWN",
            payload=proposal.payload, context=request.context_snapshot)
        self.assertEqual(same.pk, proposal.pk)
        self.assertEqual(AIProposalEvent.objects.filter(proposal=proposal, action="created").count(), 1)

    def test_checklist_apply_and_replay_write_once_then_undo_and_replay(self):
        task, proposal = self.checklist()
        reviewed = proposal.revision
        applied = self.apply(proposal)
        applied_revision = applied.revision
        same = self.apply(applied, revision=reviewed)
        self.assertEqual(same.pk, applied.pk)
        self.assertEqual(TaskChecklistItem.objects.filter(task=task).count(), 1)
        self.assertEqual(AIProposalEvent.objects.filter(proposal=proposal, action="applied").count(), 1)
        undone = undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied_revision)
        self.assertEqual(undone.state, "UNDONE")
        self.assertFalse(TaskChecklistItem.objects.filter(task=task).exists())
        undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied_revision)
        self.assertEqual(AIProposalEvent.objects.filter(proposal=proposal, action="undone").count(), 1)
        self.assertEqual(TaskChecklistItem.all_objects.filter(task=task).count(), 1)

    def test_undo_restores_preexisting_progress_without_accepting_an_outcome(self):
        task = self.task(progress=73)
        task, proposal = self.checklist(task)
        applied = self.apply(proposal)
        task.refresh_from_db()
        self.assertEqual(task.progress, 0)
        undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        task.refresh_from_db()
        self.assertEqual(task.progress, 73)
        self.assertEqual(task.status, Task.Status.PENDING)

    def test_human_task_edit_blocks_apply_without_any_partial_mutation(self):
        task, proposal = self.checklist()
        Task.objects.filter(pk=task.pk).update(notes="New human context")
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertFalse(TaskChecklistItem.objects.filter(task=task).exists())

    def test_human_checklist_edit_is_detected_even_if_task_timestamp_did_not_change(self):
        task = self.task()
        old = TaskChecklistItem.objects.create(task=task, title="Human row", created_by=self.owner)
        task, proposal = self.checklist(task)
        before = task.updated_at
        TaskChecklistItem.objects.filter(pk=old.pk).update(title="Same-clock human revision")
        task.refresh_from_db()
        self.assertEqual(task.updated_at, before)
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertEqual(TaskChecklistItem.objects.filter(task=task).count(), 1)

    def test_equivalent_existing_checklist_step_is_not_duplicated(self):
        task = self.task()
        TaskChecklistItem.objects.create(task=task, title="Confirm  the outcome", created_by=self.owner)
        task, proposal = self.checklist(task, title=" confirm the OUTCOME ")
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertEqual(TaskChecklistItem.objects.filter(task=task).count(), 1)

    def test_human_intervention_after_apply_prevents_compensating_undo(self):
        task, proposal = self.checklist()
        applied = self.apply(proposal)
        TaskChecklistItem.objects.filter(task=task).update(title="Edited by the employee")
        with self.assertRaises(AIProposalConflict):
            undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        self.assertEqual(TaskChecklistItem.objects.get(task=task).title, "Edited by the employee")

    def test_human_activity_after_apply_prevents_undo(self):
        task, proposal = self.checklist()
        applied = self.apply(proposal)
        TaskActivity.objects.create(task=task, event_type=TaskActivity.EventType.COMMENT,
            description="Human follow-up", created_by=self.owner)
        with self.assertRaises(AIProposalConflict):
            undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)

    def test_invalid_selection_wrong_owner_and_stale_revision_fail_closed(self):
        task, proposal = self.checklist()
        with self.assertRaises(ValidationError):
            self.apply(proposal, ids=["not_offered"])
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal, revision=uuid.uuid4())
        with self.assertRaises(PermissionDenied):
            apply_proposal(user=self.other, proposal_id=proposal.pk, expected_revision=proposal.revision, selected_action_ids=["step_1"])
        self.assertFalse(TaskChecklistItem.objects.filter(task=task).exists())

    def test_source_ownership_and_revoked_capability_are_rechecked(self):
        task, proposal = self.checklist()
        Task.objects.filter(pk=task.pk).update(owner=self.other)
        with self.assertRaises((PermissionDenied, AIProposalConflict)):
            self.apply(proposal)
        with self.assertRaises(PermissionDenied):
            get_proposal(user=self.owner, proposal_id=proposal.pk)
        self.assertFalse(TaskChecklistItem.objects.filter(task=task).exists())

    def test_revoked_ai_permission_denies_apply_but_owner_may_cancel(self):
        task, proposal = self.checklist()
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="use_ai"))
        with self.assertRaises(PermissionDenied):
            self.apply(proposal)
        cancelled = cancel_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=proposal.revision)
        self.assertEqual(cancelled.state, "CANCELLED")

    def test_revoked_team_membership_blocks_old_owned_source_preview_and_apply(self):
        company = Company.objects.create(name="Proposal scope company")
        team = Team.objects.create(company=company, name="Proposal scope team")
        membership = TeamMembership.objects.create(team=team, user=self.owner)
        task, proposal = self.checklist(self.task(company=company, team=team))
        TeamMembership.objects.filter(pk=membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            get_proposal(user=self.owner, proposal_id=proposal.pk)
        with self.assertRaises(PermissionDenied):
            self.apply(proposal)

    def test_manager_briefing_is_read_only_and_old_team_scope_is_rechecked(self):
        company = Company.objects.create(name="Manager proposal company")
        team = Team.objects.create(company=company, name="Manager proposal team")
        membership = TeamMembership.objects.create(team=team, user=self.owner, role="MANAGER")
        TeamMembership.objects.create(team=team, user=self.other)
        task = self.task(owner=self.other, company=company, team=team)
        context = build_workflow_context(user=self.owner, feature="BRIEFING", team_id=team.pk)
        request = AIWorkRequest.objects.create(owner=self.owner, feature="BRIEFING", scope="MANAGER", team=team,
            status="COMPLETED", context_snapshot=context, context_digest=proposal_digest(context),
            request_digest="c" * 64, expires_at=self.now + timedelta(hours=1))
        proposal = create_proposal(user=self.owner, request_id=request.pk, feature="BRIEFING", payload=self.payload([]), context=context)
        self.assertEqual(get_proposal(user=self.owner, proposal_id=proposal.pk).pk, proposal.pk)
        with self.assertRaises(PermissionDenied):
            apply_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=proposal.revision, selected_action_ids=[])
        TeamMembership.objects.filter(pk=membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            get_proposal(user=self.owner, proposal_id=proposal.pk)

    def test_result_request_must_match_stored_server_context(self):
        task, proposal = self.checklist()
        request = proposal.request
        forged = {**request.context_snapshot, "actor_id": self.other.pk}
        with self.assertRaises(PermissionDenied):
            create_proposal(user=self.owner, request_id=request.pk, feature=request.feature,
                payload=proposal.payload, context=forged)
        AIWorkRequest.objects.filter(pk=request.pk).update(status="CANCELLED")
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)

    def test_expired_and_cancelled_proposals_cannot_be_applied(self):
        task, proposal = self.checklist()
        AIProposal.objects.filter(pk=proposal.pk).update(expires_at=self.now - timedelta(seconds=1))
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        task, second = self.checklist()
        cancelled = cancel_proposal(user=self.owner, proposal_id=second.pk, expected_revision=second.revision)
        self.assertEqual(cancelled.state, "CANCELLED")
        with self.assertRaises(AIProposalConflict):
            self.apply(cancelled)

    def test_proposal_content_is_immutable_and_audit_does_not_copy_private_text(self):
        task, proposal = self.checklist(title="PRIVATE_SUGGESTION_SENTINEL")
        applied = self.apply(proposal)
        self.assertNotIn("PRIVATE_SUGGESTION_SENTINEL", str(list(AIProposalEvent.objects.filter(proposal=proposal).values("details"))))
        applied.payload = {**applied.payload, "summary": "Different result"}
        with self.assertRaises(ValidationError):
            applied.save()

    def test_capture_metadata_apply_and_undo_preserve_body_original_link_and_audience(self):
        item = CaptureItem.objects.create(owner=self.owner, content="ORIGINAL_BODY_SENTINEL", original_text="ORIGINAL_BODY_SENTINEL",
            url="https://example.com/source", title="Original", tags=["old"], pinned=True)
        proposal = self.proposal("CAPTURE", [{"id": "metadata", "kind": "CAPTURE_METADATA", "capture_id": item.pk,
            "capture_kind": "IDEA", "title": "Review this idea", "tags": ["improvement"]}], capture=item)
        applied = self.apply(proposal)
        item.refresh_from_db()
        self.assertEqual((item.kind, item.title, item.tags), ("IDEA", "Review this idea", ["improvement"]))
        self.assertEqual((item.content, item.original_text, item.url, item.pinned, item.visibility),
            ("ORIGINAL_BODY_SENTINEL", "ORIGINAL_BODY_SENTINEL", "https://example.com/source", True, "PRIVATE"))
        undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        item.refresh_from_db()
        self.assertEqual((item.kind, item.title, item.tags), ("NOTE", "Original", ["old"]))
        self.assertEqual(item.original_text, "ORIGINAL_BODY_SENTINEL")

    def test_capture_human_update_prevents_overwrite(self):
        item = CaptureItem.objects.create(owner=self.owner, content="Retained source", original_text="Retained source")
        proposal = self.proposal("CAPTURE", [{"id": "metadata", "kind": "CAPTURE_METADATA", "capture_id": item.pk,
            "capture_kind": "IDEA", "title": "Suggested", "tags": []}], capture=item)
        CaptureItem.objects.filter(pk=item.pk).update(version=item.version + 1, title="Human title")
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertEqual(CaptureItem.objects.get(pk=item.pk).title, "Human title")

    def test_plan_intentions_preserve_private_fields_and_undo_existing_plan(self):
        original, suggested = self.task(title="Original intention"), self.task(title="Suggested intention")
        plan = DailyPlan.objects.create(owner=self.owner, plan_date=self.day, focus_area="Human focus",
            motivational_quote="Human quote", quick_notes="PRIVATE_SCRATCHPAD_SENTINEL")
        plan.planned_tasks.add(original)
        proposal = self.proposal("PLAN", [{"id": "intentions", "kind": "PLAN_INTENTIONS", "plan_date": self.day.isoformat(),
            "task_ids": [suggested.pk], "priority_ids": [suggested.pk]}])
        applied = self.apply(proposal)
        plan.refresh_from_db()
        self.assertEqual(list(plan.planned_tasks.values_list("pk", flat=True)), [suggested.pk])
        self.assertEqual((plan.focus_area, plan.quick_notes, plan.motivational_quote), ("Human focus", "PRIVATE_SCRATCHPAD_SENTINEL", "Human quote"))
        undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        self.assertEqual(list(plan.planned_tasks.values_list("pk", flat=True)), [original.pk])

    def test_new_plan_can_be_undone_with_employee_change_permission(self):
        task = self.task()
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="delete_dailyplan"))
        proposal = self.proposal("PLAN", [{"id": "intentions", "kind": "PLAN_INTENTIONS", "plan_date": self.day.isoformat(),
            "task_ids": [task.pk], "priority_ids": [task.pk]}])
        applied = self.apply(proposal)
        undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        self.assertFalse(DailyPlan.objects.filter(owner=self.owner, plan_date=self.day).exists())
        self.assertTrue(DailyPlan.all_objects.filter(owner=self.owner, plan_date=self.day, is_deleted=True).exists())

    def test_end_of_day_review_prevents_undo_of_generated_plan(self):
        task = self.task()
        proposal = self.proposal("PLAN", [{"id": "intentions", "kind": "PLAN_INTENTIONS", "plan_date": self.day.isoformat(),
            "task_ids": [task.pk], "priority_ids": []}])
        applied = self.apply(proposal)
        plan = DailyPlan.objects.get(owner=self.owner, plan_date=self.day)
        DailyReview.objects.create(plan=plan, accomplishments="Employee used this plan")
        with self.assertRaises(AIProposalConflict):
            undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        self.assertTrue(DailyPlan.objects.filter(pk=plan.pk).exists())

    def test_ambiguous_date_can_be_reviewed_but_cannot_commit_intentions(self):
        task = self.task()
        proposal = self.proposal("PLAN", [{"id": "intentions", "kind": "PLAN_INTENTIONS", "plan_date": self.day.isoformat(),
            "task_ids": [task.pk], "priority_ids": []}], payload_changes={"date_hints": [{"phrase": "next Friday",
                "interpreted_date": self.day.isoformat(), "ambiguous": True, "requires_confirmation": True}]})
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertFalse(DailyPlan.objects.exists())

    def test_feasible_schedule_acceptance_replay_and_compensation(self):
        WorkPolicy.objects.create(owner=self.owner, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(17), buffer_minutes=60)
        task = self.task()
        proposal = self.proposal("PLAN", [{"id": "block", "kind": "SCHEDULE_BLOCK", "task_id": task.pk,
            "starts_at": aware_local(self.day, time(11), "Africa/Harare").isoformat(),
            "ends_at": aware_local(self.day, time(11, 30), "Africa/Harare").isoformat()}])
        reviewed = proposal.revision
        applied = self.apply(proposal)
        self.apply(applied, revision=reviewed)
        block = ScheduleBlock.objects.get(owner=self.owner)
        self.assertEqual(block.state, "ACCEPTED")
        undone = undo_proposal(user=self.owner, proposal_id=proposal.pk, expected_revision=applied.revision)
        block.refresh_from_db()
        self.assertEqual(block.state, "CANCELLED")
        self.assertEqual(undone.state, "UNDONE")

    def test_unknown_schedule_capacity_rejects_without_leaving_a_draft(self):
        task = self.task()
        proposal = self.proposal("PLAN", [{"id": "block", "kind": "SCHEDULE_BLOCK", "task_id": task.pk,
            "starts_at": aware_local(self.day, time(11), "Africa/Harare").isoformat(),
            "ends_at": aware_local(self.day, time(11, 30), "Africa/Harare").isoformat()}])
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertFalse(ScheduleBlock.objects.exists())

    def test_conflicting_selected_schedule_batch_rolls_back_every_block(self):
        WorkPolicy.objects.create(owner=self.owner, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(17), buffer_minutes=60)
        task = self.task(estimated_duration=timedelta(hours=5))
        actions = [{"id": name, "kind": "SCHEDULE_BLOCK", "task_id": task.pk,
            "starts_at": aware_local(self.day, time(start_h, start_m), "Africa/Harare").isoformat(),
            "ends_at": aware_local(self.day, time(end_h, end_m), "Africa/Harare").isoformat()}
            for name, start_h, start_m, end_h, end_m in (("first", 11, 0, 12, 0), ("second", 11, 30, 12, 30))]
        proposal = self.proposal("PLAN", actions)
        with self.assertRaises(AIProposalConflict):
            self.apply(proposal)
        self.assertFalse(ScheduleBlock.all_objects.exists())
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "PENDING")
        self.assertFalse(AIProposalEvent.objects.filter(proposal=proposal, action="applied").exists())


class AIProposalSerializationTests(TransactionTestCase):
    def test_concurrent_apply_requests_share_one_audited_result(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock serialization requires PostgreSQL.")
        owner = get_user_model().objects.create_user("concurrent-proposal-owner")
        owner.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        task = Task.objects.create(owner=owner, title="One reviewed checklist addition")
        context = build_workflow_context(user=owner, feature="BREAKDOWN", task_id=task.pk)
        request = AIWorkRequest.objects.create(owner=owner, feature="BREAKDOWN", status="COMPLETED", task=task,
            context_snapshot=context, context_digest=proposal_digest(context), request_digest="b" * 64,
            expires_at=timezone.now() + timedelta(hours=1))
        payload = {"schema_version": "3", "headline": "One suggestion", "summary": "Review once", "reasoning": "A recorded source",
            "actions": [{"id": "step", "kind": "CHECKLIST_ADD", "task_id": task.pk, "title": "One step"}],
            "questions": [], "citations": [], "date_hints": []}
        proposal = create_proposal(user=owner, request_id=request.pk, feature="BREAKDOWN", payload=payload, context=context)
        barrier = Barrier(2)

        def apply_once():
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=owner.pk)
                barrier.wait(timeout=15)
                return str(apply_proposal(user=actor, proposal_id=proposal.pk, expected_revision=proposal.revision,
                    selected_action_ids=["step"]).revision)
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(apply_once) for _ in range(2)]
            revisions = [future.result(timeout=30) for future in futures]
        self.assertEqual(revisions[0], revisions[1])
        self.assertEqual(TaskChecklistItem.objects.filter(task=task).count(), 1)
        self.assertEqual(AIProposalEvent.objects.filter(proposal=proposal, action="applied").count(), 1)
