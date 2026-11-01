"""Offline asynchronous boundary, privacy, stale work and owner UI coverage."""
from datetime import timedelta
import json
from types import SimpleNamespace
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasker.ai.exceptions import AITimeoutError
from tasker.ai.privacy import redact_request
from tasker.models import AIConversation, AIMessage, AIProposal, AIUsageLog, AIWorkRequest, OutboxJob, Task, TaskChecklistItem
from tasker.services.ai_workflow import (cancel_work_request, create_work_request, get_work_request,
    handle_ai_work, retry_work_request, visible_work_requests)
from tasker.services.jobs import claim_next_job, run_claimed_job, run_worker_once


def owner(name="workflow-owner"):
    user = get_user_model().objects.create_user(name)
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    return user


def response(actions=()):
    return {"schema_version": "3", "headline": "A clear next step", "summary": "Review recorded work.",
        "reasoning": "Use the source facts.", "actions": list(actions), "questions": [], "citations": [], "date_hints": []}


class FakeGateway:
    """A deliberately local result; monetary admission has separate gateway tests."""
    def __init__(self, callback=None, payload=None, error=None):
        self.callback, self.payload, self.error, self.calls = callback, payload or response(), error, 0

    def generate_text(self, *, user, metadata, before_provider_attempt, **kwargs):
        assert not connection.in_atomic_block, "Provider I/O must not hold a DB transaction"
        before_provider_attempt()
        self.calls += 1
        conversation = AIConversation.objects.create(owner=user, agent_type="workflow-assistant", title="Private generated title")
        message = AIMessage.objects.create(conversation=conversation, sequence=1, role="ASSISTANT",
            content=json.dumps(self.payload), total_tokens=0)
        log = AIUsageLog.objects.create(user=user, conversation=conversation, message=message,
            operation="workflow-briefing", request_metadata=metadata, status="FAILED" if self.error else "SUCCEEDED")
        if self.callback:
            self.callback()
        if self.error:
            raise self.error
        return SimpleNamespace(text=json.dumps(self.payload), usage_log_id=log.pk, conversation_id=conversation.pk)


@override_settings(TASKER_AI_WORKFLOW_ENABLED=True, TASKER_AI_BUDGET_ENFORCED=False)
class AIWorkflowWorkerTests(TransactionTestCase):
    def setUp(self):
        self.user = owner()
        self.task = Task.objects.create(owner=self.user, title="A synthetic next step")

    def queue(self, **changes):
        return create_work_request(**{"user": self.user, "feature": "BRIEFING", "client_key": uuid.uuid4(), **changes})

    def dispatch(self, gateway=None):
        gateway = gateway or FakeGateway()
        with patch("tasker.services.ai_workflow.AIService", return_value=gateway):
            result = run_worker_once(limit=10)
        return gateway, result

    def test_queue_dispatch_publish_and_replay_do_not_mutate_work(self):
        item = self.queue()
        self.assertEqual(item.job.payload, {"request_id": str(item.pk)})
        self.assertFalse(AIUsageLog.objects.exists())
        gateway, result = self.dispatch()
        item.refresh_from_db()
        self.assertEqual(item.status, "COMPLETED")
        self.assertEqual(result[0]["outcome"], "succeeded")
        self.assertEqual(item.proposal.payload["headline"], "A clear next step")
        self.assertFalse(TaskChecklistItem.objects.exists())
        self.dispatch(gateway)
        self.assertEqual(gateway.calls, 1)

    def test_duplicate_submission_has_one_request_and_job(self):
        key = uuid.uuid4()
        first = self.queue(client_key=key)
        same = self.queue(client_key=key)
        self.assertEqual(first.pk, same.pk)
        self.assertEqual(AIWorkRequest.objects.count(), 1)
        with self.assertRaises(ValidationError):
            self.queue(client_key=key, question="Different request")

    def test_queued_cancel_never_dispatches(self):
        item = self.queue()
        cancel_work_request(user=self.user, request_id=item.pk, expected_revision=item.revision)
        gateway, _ = self.dispatch()
        self.assertEqual(gateway.calls, 0)
        self.assertFalse(AIProposal.objects.exists())

    def test_stale_task_fails_before_dispatch(self):
        item = self.queue()
        self.task.title = "Changed while queued"
        self.task.save()
        gateway, _ = self.dispatch()
        item.refresh_from_db()
        self.assertEqual(gateway.calls, 0)
        self.assertEqual(item.status, "FAILED")

    def test_expired_context_fails_without_call(self):
        item = self.queue()
        item.context_snapshot["expires_at"] = (timezone.now()-timedelta(seconds=1)).isoformat()
        item.save(update_fields=["context_snapshot"])
        gateway, _ = self.dispatch()
        self.assertEqual(gateway.calls, 0)

    def test_late_cancel_retains_usage_but_never_publishes(self):
        item = self.queue()
        def cancel():
            item.refresh_from_db()
            cancel_work_request(user=self.user, request_id=item.pk, expected_revision=item.revision)
        self.dispatch(FakeGateway(callback=cancel))
        item.refresh_from_db()
        self.assertEqual(item.status, "CANCELLED")
        self.assertIsNotNone(item.usage_log_id)
        self.assertFalse(AIProposal.objects.exists())

    def test_late_redaction_erases_linked_response_and_conversation(self):
        item = self.queue(question="Private question")
        self.dispatch(FakeGateway(callback=lambda: redact_request(user=self.user, request_id=item.pk)))
        item.refresh_from_db()
        self.assertTrue(item.content_redacted_at)
        self.assertIsNotNone(item.usage_log_id)
        self.assertFalse(AIMessage.all_objects.exclude(content="").exists())
        self.assertFalse(AIConversation.all_objects.exclude(title="").exists())
        self.assertFalse(AIProposal.objects.exists())

    def test_redaction_then_deactivation_still_erases_late_result(self):
        item = self.queue(question="Erased before deactivation")
        def erase_and_disable():
            redact_request(user=self.user, request_id=item.pk)
            get_user_model().objects.filter(pk=self.user.pk).update(is_active=False)
        self.dispatch(FakeGateway(callback=erase_and_disable))
        item.refresh_from_db()
        self.assertTrue(item.content_redacted_at)
        self.assertFalse(AIMessage.all_objects.exclude(content="").exists())
        self.assertIsNotNone(item.usage_log_id)

    def test_tampered_context_cannot_dispatch(self):
        item = self.queue()
        item.context_snapshot["question"] = "Changed without matching immutable evidence"
        item.save(update_fields=["context_snapshot"])
        gateway, _ = self.dispatch()
        self.assertEqual(gateway.calls, 0)
        self.assertFalse(AIProposal.objects.exists())

    def test_timeout_links_failed_audit_and_does_not_automatically_resend(self):
        item = self.queue()
        gateway, _ = self.dispatch(FakeGateway(error=AITimeoutError()))
        item.refresh_from_db()
        self.assertEqual(item.status, "UNKNOWN")
        self.assertIsNotNone(item.usage_log_id)
        self.dispatch(gateway)
        self.assertEqual(gateway.calls, 1)
        retried = retry_work_request(user=self.user, request_id=item.pk, client_key=uuid.uuid4())
        self.assertEqual(retried.retry_of_id, item.pk)
        self.assertEqual(retried.status, "QUEUED")

    def test_mutation_during_generation_prevents_publication_but_retains_audit(self):
        item = self.queue()
        self.dispatch(FakeGateway(callback=lambda: Task.objects.filter(pk=self.task.pk).update(title="Concurrent human edit")))
        item.refresh_from_db()
        self.assertEqual(item.status, "FAILED")
        self.assertIsNotNone(item.usage_log_id)
        self.assertFalse(AIProposal.objects.exists())

    def test_reclaimed_running_request_records_uncertainty_without_resending(self):
        item = self.queue()
        AIWorkRequest.objects.filter(pk=item.pk).update(status="RUNNING", dispatch_nonce=uuid.uuid4())
        gateway, _ = self.dispatch()
        item.refresh_from_db()
        self.assertEqual(item.status, "UNKNOWN")
        self.assertEqual(gateway.calls, 0)

    def test_lost_lease_cannot_publish_and_retains_usage(self):
        item = self.queue()
        def steal():
            OutboxJob.objects.filter(pk=item.job_id).update(lease_token=uuid.uuid4())
        self.dispatch(FakeGateway(callback=steal))
        item.refresh_from_db()
        self.assertEqual(item.status, "UNKNOWN")
        self.assertIsNotNone(item.usage_log_id)
        self.assertFalse(AIProposal.objects.exists())

    def test_invalid_model_actions_cannot_commit_anything(self):
        item = self.queue()
        self.dispatch(FakeGateway(payload=response([{"kind": "SEND_EMAIL", "to": "outsider@example.test"}])))
        item.refresh_from_db()
        self.assertEqual(item.status, "FAILED")
        self.assertFalse(AIProposal.objects.exists())

    def test_breakdown_stays_preview_until_explicit_apply(self):
        item = self.queue(feature="BREAKDOWN", task_id=self.task.pk)
        self.dispatch(FakeGateway(payload=response([{"id": "step", "kind": "CHECKLIST_ADD", "task_id": self.task.pk, "title": "Confirm outcome"}])))
        item.refresh_from_db()
        self.assertEqual(item.status, "COMPLETED")
        self.assertFalse(TaskChecklistItem.objects.exists())


@override_settings(TASKER_AI_WORKFLOW_ENABLED=True, TASKER_AI_BUDGET_ENFORCED=False)
class AIWorkflowUITests(TestCase):
    def setUp(self):
        self.user = owner()
        self.other = owner("other-workflow-owner")
        self.task = Task.objects.create(owner=self.user, title="Review UI task")
        self.client.force_login(self.user)

    def queue(self, **changes):
        return create_work_request(**{"user": self.user, "feature": "BRIEFING", "client_key": uuid.uuid4(), **changes})

    def url(self, name, **kwargs):
        return reverse("ims:tasker:"+name, kwargs=kwargs)

    def test_home_degraded_budget_and_no_generation_on_get(self):
        result = self.client.get(self.url("ai_work_home"))
        self.assertContains(result, "Live AI is not ready")
        self.assertContains(result, "Queue a suggestion")
        self.assertFalse(AIWorkRequest.objects.exists())
        self.assertEqual(result["Cache-Control"].find("no-store") >= 0, True)

    def test_assistant_entry_redirects_to_new_review_flow(self):
        self.assertRedirects(self.client.get(self.url("assistant_home")), self.url("ai_work_home"))

    def test_post_queues_without_provider_and_repeats_safely(self):
        data = {"feature": "BRIEFING", "client_key": str(uuid.uuid4())}
        first = self.client.post(self.url("ai_work_home"), data)
        self.assertEqual(first.status_code, 302)
        self.assertEqual(self.client.post(self.url("ai_work_home"), data)["Location"], first["Location"])
        self.assertEqual(AIWorkRequest.objects.count(), 1)
        self.assertFalse(AIUsageLog.objects.exists())

    def test_meeting_history_accepts_deliberate_text(self):
        item = self.queue(feature="MEETING", question="Deliberately supplied synthetic meeting")
        self.assertEqual(get_work_request(user=self.user, request_id=item.pk).pk, item.pk)
        self.assertIn(item, visible_work_requests(self.user))

    def test_owner_only_request_status_history_and_actions(self):
        item = self.queue(question="OWNER_ONLY_SENTINEL")
        self.client.force_login(self.other)
        for name in ("ai_work_detail", "ai_work_status"):
            self.assertEqual(self.client.get(self.url(name, pk=item.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("ai_work_action", pk=item.pk, action="redact")).status_code, 403)
        self.assertNotContains(self.client.get(self.url("ai_work_home")), "OWNER_ONLY_SENTINEL")

    def test_status_returns_only_safe_metadata(self):
        item = self.queue(question="PRIVATE_STATUS_SENTINEL")
        result = self.client.get(self.url("ai_work_status", pk=item.pk))
        self.assertEqual(set(result.json()), {"status", "label", "revision", "pending"})
        self.assertNotContains(result, "PRIVATE_STATUS_SENTINEL")
        self.assertFalse(AIUsageLog.objects.exists())

    def test_revoked_source_access_hides_history(self):
        item = self.queue()
        self.task.owner = self.other
        self.task.save()
        self.assertEqual(self.client.get(self.url("ai_work_detail", pk=item.pk)).status_code, 403)
        self.assertNotIn(item, visible_work_requests(self.user))

    def test_csrf_is_required_for_state_changes(self):
        from django.test import Client
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url("ai_work_home"), {"feature": "BRIEFING"}).status_code, 403)

    def test_budget_privacy_rejects_ambiguous_cutoff(self):
        self.assertEqual(self.client.get(self.url("ai_budget")).status_code, 200)
        self.assertEqual(self.client.post(self.url("ai_budget"), {"before": "tomorrow"}).status_code, 400)

    def completed(self):
        from tasker.services.ai_proposals import create_proposal
        item = self.queue(feature="BREAKDOWN", task_id=self.task.pk)
        item.status = "COMPLETED"
        item.save(update_fields=["status"])
        create_proposal(user=self.user, request_id=item.pk, feature=item.feature,
            context=item.context_snapshot, payload=response([{"id": "step", "kind": "CHECKLIST_ADD",
                "task_id": self.task.pk, "title": "Confirm the intended outcome"}]))
        return item

    def test_preview_is_escaped_unselected_and_apply_undo_are_explicit(self):
        item = self.completed()
        detail = self.client.get(self.url("ai_work_detail", pk=item.pk))
        self.assertContains(detail, "Confirm the intended outcome")
        self.assertNotRegex(detail.content.decode(), r'<input[^>]+name="actions"[^>]+checked')
        revision = item.proposal.revision
        applied = self.client.post(self.url("ai_work_action", pk=item.pk, action="apply"),
            {"expected_revision": revision, "actions": ["step"]})
        self.assertEqual(applied.status_code, 302)
        self.assertEqual(TaskChecklistItem.objects.count(), 1)
        item.proposal.refresh_from_db()
        undone = self.client.post(self.url("ai_work_action", pk=item.pk, action="undo"),
            {"expected_revision": item.proposal.revision})
        self.assertEqual(undone.status_code, 302)
        self.assertFalse(TaskChecklistItem.objects.exists())

    def test_stale_apply_keeps_selection_and_returns_conflict(self):
        item = self.completed()
        self.task.title = "Changed by the owner"
        self.task.save()
        result = self.client.post(self.url("ai_work_action", pk=item.pk, action="apply"),
            {"expected_revision": item.proposal.revision, "actions": ["step"]})
        self.assertEqual(result.status_code, 409)
        self.assertContains(result, "checked", status_code=409)
        self.assertFalse(TaskChecklistItem.objects.exists())

    @override_settings(TASKER_AI_WORKFLOW_ENABLED=False)
    def test_rollout_disabled_still_allows_owner_cancel_and_redact(self):
        with override_settings(TASKER_AI_WORKFLOW_ENABLED=True):
            item = self.queue(question="Removed content")
        result = self.client.post(self.url("ai_work_action", pk=item.pk, action="cancel"), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(self.client.post(self.url("ai_work_action", pk=item.pk, action="redact")).status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.question, "")

    def test_stale_cancel_returns_reviewable_message(self):
        item = self.queue()
        result = self.client.post(self.url("ai_work_action", pk=item.pk, action="cancel"), {"expected_revision": uuid.uuid4()})
        self.assertEqual(result.status_code, 302)
        item.refresh_from_db()
        self.assertEqual(item.status, "QUEUED")

    @override_settings(TASKER_AI_WORKFLOW_ENABLED=False)
    def test_rollout_flag_hides_new_endpoints(self):
        self.assertEqual(self.client.get(self.url("ai_work_home")).status_code, 404)
