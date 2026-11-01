"""Local owner deletion erases content without refunding measured AI usage."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.utils import timezone

from tasker.ai.privacy import erase_late_result, redact_ai_content, redact_conversation, redact_request
from tasker.ai_workflow_models import AIWorkRequest
from tasker.models import AIConversation, AIFeedback, AIMessage, AIUsageLog
from tasker.proposal_models import AIProposal, AIProposalEvent
from tasker.services.ai_proposals import AIProposalConflict, apply_proposal, proposal_digest


class AIPrivacyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("privacy-owner")
        cls.other = get_user_model().objects.create_user("privacy-other")

    def conversation(self, **changes):
        return AIConversation.objects.create(**{"owner": self.owner, "title": "PRIVATE_TITLE_SENTINEL",
            "context_metadata": {"private": "PRIVATE_CONTEXT_SENTINEL"}, **changes})

    def message(self, conversation, sequence=1, **changes):
        return AIMessage.objects.create(**{"conversation": conversation, "sequence": sequence, "role": "ASSISTANT",
            "content": "PRIVATE_MESSAGE_SENTINEL", "message_metadata": {"private": "PRIVATE_RESULT_SENTINEL"},
            "input_tokens": 8, "output_tokens": 3, "total_tokens": 11, **changes})

    def request(self, conversation=None, **changes):
        context = {"actor_id": self.owner.pk, "scope": "PERSONAL", "feature": "EXPLAIN", "tasks": [],
            "proofs": {"tasks": {}, "captures": {}, "plans": {}, "task_children": {}}}
        return AIWorkRequest.objects.create(**{"owner": self.owner, "feature": "EXPLAIN", "scope": "PERSONAL",
            "status": "COMPLETED", "question": "PRIVATE_QUESTION_SENTINEL", "context_snapshot": context,
            "context_digest": proposal_digest(context), "request_digest": "a" * 64,
            "conversation": conversation, "expires_at": timezone.now() + timedelta(hours=1),
            "result_summary": {"private": "PRIVATE_SUMMARY_SENTINEL"},
            "request_metadata": {"private": "PRIVATE_METADATA_SENTINEL"}, **changes})

    def proposal(self, request, **changes):
        payload = {"schema_version": "3", "headline": "PRIVATE_PROPOSAL_SENTINEL", "summary": "Personal explanation",
            "reasoning": "Based on source evidence", "actions": [], "questions": [], "citations": [], "date_hints": []}
        return AIProposal.objects.create(**{"owner": self.owner, "request": request, "feature": request.feature,
            "scope": "PERSONAL", "payload": payload, "payload_digest": proposal_digest(payload),
            "context_digest": request.context_digest, "source_proofs": request.context_snapshot["proofs"],
            "expires_at": request.expires_at, "before_state": {"private": "PRIVATE_BEFORE_SENTINEL"},
            "after_state": {"private": "PRIVATE_AFTER_SENTINEL"}, **changes})

    def test_request_redaction_erases_saved_content_but_keeps_billing_and_audit(self):
        conversation = self.conversation()
        message = self.message(conversation)
        feedback = AIFeedback.objects.create(user=self.owner, message=message, rating=1,
            comment="PRIVATE_FEEDBACK_SENTINEL", category="Private category")
        completed_at = timezone.now()
        usage = AIUsageLog.objects.create(user=self.owner, conversation=conversation, message=message, operation="workflow_explain",
            status="SUCCEEDED", input_tokens=8, output_tokens=3, total_tokens=11, quota_charged_tokens=11,
            started_at=completed_at-timedelta(seconds=1), completed_at=completed_at, provider_response_id="provider-audit-id")
        request = self.request(conversation, usage_log=usage)
        proposal = self.proposal(request)
        before_usage = AIUsageLog.all_objects.filter(pk=usage.pk).values().get()
        result = redact_request(user=self.owner, request_id=request.pk)
        self.assertEqual(result.status, "COMPLETED")
        self.assertIsNotNone(result.content_redacted_at)
        self.assertEqual((result.question, result.context_snapshot, result.result_summary, result.request_metadata), ("", {}, {}, {}))
        proposal.refresh_from_db()
        self.assertEqual((proposal.payload, proposal.source_proofs, proposal.before_state, proposal.after_state), ({}, {}, {}, {}))
        self.assertEqual(proposal.state, "CANCELLED")
        message.refresh_from_db()
        feedback.refresh_from_db()
        conversation.refresh_from_db()
        self.assertEqual((message.content, message.message_metadata, feedback.comment, conversation.title), ("", {}, "", ""))
        self.assertTrue(conversation.is_deleted)
        self.assertEqual(AIUsageLog.all_objects.filter(pk=usage.pk).values().get(), before_usage)
        self.assertEqual(AIProposalEvent.objects.get(proposal=proposal).action, "redacted")

    def test_redaction_is_idempotent_and_audit_does_not_copy_body(self):
        request = self.request()
        proposal = self.proposal(request)
        first = redact_request(user=self.owner, request_id=request.pk)
        second = redact_request(user=self.owner, request_id=request.pk)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(AIProposalEvent.objects.filter(proposal=proposal).count(), 1)
        self.assertNotIn("PRIVATE_", str(list(AIProposalEvent.objects.values("details"))))

    def test_running_request_is_cancelled_and_marked_even_before_conversation_is_linked(self):
        request = self.request(status="RUNNING")
        result = redact_request(user=self.owner, request_id=request.pk)
        self.assertEqual(result.status, "CANCELLED")
        self.assertIsNotNone(result.content_redacted_at)
        self.assertIsNone(result.conversation_id)
        self.assertEqual(result.context_snapshot, {})

    def test_running_unlinked_conversation_is_found_through_owner_bound_usage_uuid(self):
        request = self.request(status="RUNNING")
        conversation = self.conversation()
        message = self.message(conversation, status="PENDING")
        AIUsageLog.objects.create(user=self.owner, conversation=conversation, message=message,
            operation="workflow_explain", request_metadata={"source": str(request.pk)})
        redact_request(user=self.owner, request_id=request.pk)
        message.refresh_from_db()
        conversation.refresh_from_db()
        self.assertEqual(message.content, "")
        self.assertTrue(conversation.is_deleted)

    def test_late_erasure_remains_authorized_after_owner_deactivation(self):
        request = self.request(status="RUNNING")
        redact_request(user=self.owner, request_id=request.pk)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        conversation = self.conversation()
        message = self.message(conversation)
        AIWorkRequest.objects.filter(pk=request.pk).update(conversation=conversation)
        result = erase_late_result(request_id=request.pk)
        message.refresh_from_db()
        self.assertEqual(result.status, "CANCELLED")
        self.assertEqual(message.content, "")

    def test_internal_late_erasure_cannot_start_a_new_erasure(self):
        request = self.request()
        conversation = self.conversation(owner=self.other)
        message = self.message(conversation)
        AIWorkRequest.objects.filter(pk=request.pk).update(conversation=conversation)
        with self.assertRaises(PermissionDenied):
            erase_late_result(request_id=request.pk)
        redact_request(user=self.owner, request_id=request.pk)
        erase_late_result(request_id=request.pk)
        message.refresh_from_db()
        self.assertEqual(message.content, "PRIVATE_MESSAGE_SENTINEL")

    def test_applied_result_redaction_preserves_truthful_state_and_disables_undo(self):
        request = self.request()
        proposal = self.proposal(request, state="APPLIED", applied_at=timezone.now(), apply_receipt={"effects": []})
        redact_request(user=self.owner, request_id=request.pk)
        proposal.refresh_from_db()
        self.assertEqual(proposal.state, "APPLIED")
        self.assertIsNotNone(proposal.content_redacted_at)
        self.assertEqual(proposal.apply_receipt, {})

    def test_other_users_and_inactive_owners_cannot_delete_private_content(self):
        conversation = self.conversation()
        request = self.request(conversation)
        with self.assertRaises(PermissionDenied):
            redact_request(user=self.other, request_id=request.pk)
        with self.assertRaises(PermissionDenied):
            redact_conversation(user=self.other, conversation_id=conversation.pk)
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            redact_request(user=self.owner, request_id=request.pk)
        self.assertEqual(AIWorkRequest.objects.get(pk=request.pk).question, "PRIVATE_QUESTION_SENTINEL")

    def test_owners_can_remove_content_after_ai_access_is_revoked(self):
        self.assertFalse(self.owner.has_perm("tasker.use_ai"))
        request = self.request()
        result = redact_request(user=self.owner, request_id=request.pk)
        self.assertEqual(result.question, "")

    def test_conversation_deletion_erases_soft_deleted_messages_and_all_linked_previews(self):
        conversation = self.conversation()
        message = self.message(conversation)
        message.delete(user=self.owner)
        first = self.request(conversation)
        second = self.request(conversation)
        self.proposal(first)
        self.proposal(second)
        redact_conversation(user=self.owner, conversation_id=conversation.pk)
        message.refresh_from_db()
        self.assertEqual(message.content, "")
        self.assertFalse(AIWorkRequest.objects.filter(conversation=conversation, content_redacted_at__isnull=True).exists())
        self.assertFalse(AIProposal.objects.filter(request__conversation=conversation, content_redacted_at__isnull=True).exists())

    def test_retention_is_owner_scoped_bounded_and_leaves_active_or_newer_records(self):
        cutoff = timezone.now() - timedelta(days=7)
        first, second = self.request(), self.request()
        active = self.request(status="RUNNING")
        foreign = self.request(owner=self.other)
        recent = self.request()
        AIWorkRequest.objects.filter(pk__in=[first.pk, second.pk, active.pk, foreign.pk]).update(created_at=cutoff - timedelta(days=1))
        result = redact_ai_content(user=self.owner, before=cutoff, limit=1)
        self.assertEqual(result["requests"], 1)
        self.assertTrue(result["remaining"])
        result = redact_ai_content(user=self.owner, before=cutoff, limit=1)
        self.assertEqual(result["requests"], 1)
        self.assertFalse(result["remaining"])
        self.assertEqual(AIWorkRequest.objects.filter(pk__in=[active.pk, foreign.pk, recent.pk], content_redacted_at__isnull=True).count(), 3)

    def test_retention_preserves_recent_messages_in_a_shared_conversation(self):
        cutoff = timezone.now() - timedelta(days=7)
        conversation = self.conversation()
        old_message = self.message(conversation)
        new_message = self.message(conversation, sequence=2, content="NEWER_PRIVATE_MESSAGE")
        old_request = self.request(conversation)
        new_request = self.request(conversation)
        AIWorkRequest.objects.filter(pk=old_request.pk).update(created_at=cutoff - timedelta(days=1))
        AIMessage.all_objects.filter(pk=old_message.pk).update(created_at=cutoff - timedelta(days=1))
        redact_ai_content(user=self.owner, before=cutoff)
        old_message.refresh_from_db()
        new_message.refresh_from_db()
        conversation.refresh_from_db()
        new_request.refresh_from_db()
        self.assertEqual(old_message.content, "")
        self.assertEqual(new_message.content, "NEWER_PRIVATE_MESSAGE")
        self.assertFalse(conversation.is_deleted)
        self.assertIsNone(new_request.content_redacted_at)

    def test_retention_clears_private_text_from_an_old_soft_deleted_conversation(self):
        cutoff = timezone.now() - timedelta(days=7)
        conversation = self.conversation()
        message = self.message(conversation)
        conversation.delete(user=self.owner)
        AIConversation.all_objects.filter(pk=conversation.pk).update(created_at=cutoff - timedelta(days=1))
        AIMessage.all_objects.filter(pk=message.pk).update(created_at=cutoff - timedelta(days=1))
        result = redact_ai_content(user=self.owner, before=cutoff)
        message.refresh_from_db()
        self.assertEqual(result["conversations"], 1)
        self.assertEqual(message.content, "")
        self.assertFalse(redact_ai_content(user=self.owner, before=cutoff)["remaining"])

    def test_retention_does_not_erase_active_input_in_a_reused_conversation(self):
        cutoff = timezone.now() - timedelta(days=7)
        conversation = self.conversation()
        message = self.message(conversation, role="USER")
        closed = self.request(conversation)
        active = self.request(conversation, status="RUNNING")
        AIWorkRequest.objects.filter(pk__in=[closed.pk, active.pk]).update(created_at=cutoff - timedelta(days=1))
        AIMessage.all_objects.filter(pk=message.pk).update(created_at=cutoff - timedelta(days=1))
        redact_ai_content(user=self.owner, before=cutoff)
        message.refresh_from_db()
        active.refresh_from_db()
        self.assertEqual(message.content, "PRIVATE_MESSAGE_SENTINEL")
        self.assertIsNone(active.content_redacted_at)

    def test_retention_requires_an_explicit_past_cutoff_and_bounded_batch(self):
        for before, limit in ((timezone.now() + timedelta(days=1), 10), (timezone.now().replace(tzinfo=None), 10),
                              (timezone.now(), 0), (timezone.now(), 501), (timezone.now(), True)):
            with self.assertRaises(ValidationError):
                redact_ai_content(user=self.owner, before=before, limit=limit)
