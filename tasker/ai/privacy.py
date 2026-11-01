"""Owner-controlled local content deletion, separate from immutable cost history.

This does not claim to delete provider-held data or change provider retention.
An in-flight request is cancelled and marked before the worker may publish its
late result. The worker also clears any messages linked after this transaction.
"""
from datetime import datetime
import uuid

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tasker.models import AIConversation, AIFeedback, AIMessage, AIUsageLog
from tasker.proposal_models import AIProposal, AIProposalEvent


def _owner(user):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if actor is None:
        raise PermissionDenied
    # Removing one's saved content must remain possible after AI access is revoked.
    return actor


def _redact_proposals(actor, request_ids, now):
    proposals = list(AIProposal.all_objects.select_for_update().filter(owner=actor, request_id__in=request_ids).order_by("pk"))
    for proposal in proposals:
        if proposal.content_redacted_at is not None:
            continue
        revision = uuid.uuid4()
        state = AIProposal.State.CANCELLED if proposal.state == AIProposal.State.PENDING else proposal.state
        # This is the sole content-erasure exception to immutable suggestion bodies.
        AIProposal.all_objects.filter(pk=proposal.pk).update(
            payload={}, source_proofs={}, before_state={}, after_state={}, apply_receipt={},
            selected_action_ids=[], ai_metadata={}, ai_processed_at=None,
            content_redacted_at=now, state=state, revision=revision, updated_at=now, updated_by=actor,
        )
        AIProposalEvent.objects.create(proposal=proposal, actor=actor, action="redacted", revision=revision,
                                       details={"reason_code": "owner_content_redaction"})
    return len(proposals)


def _redact_messages(actor, conversation_ids, now, *, before=None):
    if not conversation_ids:
        return 0
    conversations = list(AIConversation.all_objects.select_for_update().filter(owner=actor, pk__in=conversation_ids).order_by("pk"))
    ids = [item.pk for item in conversations]
    if before is None:
        AIConversation.all_objects.filter(pk__in=ids).update(
            title="", context_metadata={}, provider_conversation_id="", status=AIConversation.Status.ARCHIVED,
            ai_metadata={}, ai_processed_at=None, is_deleted=True, deleted_at=now, deleted_by=actor,
            updated_at=now, updated_by=actor,
        )
    messages = AIMessage.all_objects.filter(conversation_id__in=ids)
    if before is not None:
        messages = messages.filter(created_at__lt=before).exclude(status=AIMessage.Status.PENDING)
    message_ids = list(messages.values_list("pk", flat=True))
    # QuerySet.update erases already-soft-deleted content too. Token counts remain
    # as audit metadata; the separate usage and monetary ledger is never deleted.
    AIMessage.all_objects.filter(pk__in=message_ids).update(
        content="", message_metadata={}, provider_response_id="", error_code="", ai_metadata={}, ai_processed_at=None,
        is_deleted=True, deleted_at=now, deleted_by=actor, updated_at=now, updated_by=actor,
    )
    AIFeedback.all_objects.filter(message_id__in=message_ids, user=actor).update(
        category="", comment="", ai_metadata={}, ai_processed_at=None,
        is_deleted=True, deleted_at=now, deleted_by=actor, updated_at=now, updated_by=actor,
    )
    return len(message_ids)


def _redact_requests(actor, rows, now):
    ids = [row.pk for row in rows]
    conversations = {row.conversation_id for row in rows if row.conversation_id}
    # Gateway preparation already writes this content-free, requester-bound UUID
    # before dispatch. Find its messages even before the workflow's result link.
    if ids:
        conversations.update(AIUsageLog.all_objects.filter(user_id=actor.pk,
            request_metadata__source__in=[str(pk) for pk in ids], conversation__owner_id=actor.pk)
            .values_list("conversation_id", flat=True))
    for request in rows:
        if request.content_redacted_at is not None:
            continue
        status = "CANCELLED" if request.status in ("QUEUED", "RUNNING") else request.status
        type(request).all_objects.filter(pk=request.pk).update(
            question="", context_snapshot={}, result_summary={}, request_metadata={},
            ai_metadata={}, ai_processed_at=None, status=status, revision=uuid.uuid4(),
            content_redacted_at=now, updated_at=now, updated_by=actor,
        )
    _redact_proposals(actor, ids, now)
    return conversations


def _erase_request(actor, request):
    Request = type(request)
    now = timezone.now()
    conversations = _redact_requests(actor, [request], now)
    # A workflow uses a fresh conversation per request. If older callers reused a
    # conversation, erase all linked request previews so none retain that content.
    if conversations:
        related = list(Request.all_objects.select_for_update().filter(owner=actor, conversation_id__in=conversations).exclude(pk=request.pk).order_by("pk"))
        _redact_requests(actor, related, now)
        _redact_messages(actor, conversations, now)
    request.refresh_from_db()
    return request


@transaction.atomic
def redact_request(*, user, request_id):
    """Erase a request and its linked result; retain measured/unknown billing."""
    actor = _owner(user)
    Request = apps.get_model("tasker", "AIWorkRequest")
    request = Request.all_objects.select_for_update().filter(pk=request_id, owner=actor).first()
    if request is None:
        raise PermissionDenied
    return _erase_request(actor, request)


@transaction.atomic
def erase_late_result(*, request_id):
    """Worker-only continuation of a previously authorized content erasure.

    Account deactivation cannot revoke the owner's earlier deletion instruction.
    No new erasure is authorized here: an already-redacted request is mandatory,
    and every conversation/message lookup remains bound to that request's owner.
    """
    Request = apps.get_model("tasker", "AIWorkRequest")
    owner_id = Request.all_objects.filter(pk=request_id).values_list("owner_id", flat=True).first()
    actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=owner_id).first()
    if actor is None:
        raise PermissionDenied
    request = Request.all_objects.select_for_update().filter(pk=request_id, owner_id=actor.pk,
        content_redacted_at__isnull=False).first()
    if request is None:
        raise PermissionDenied
    return _erase_request(actor, request)


@transaction.atomic
def redact_conversation(*, user, conversation_id):
    actor = _owner(user)
    # Read ownership first; lock Request -> Proposal -> Conversation, matching the
    # worker's final publication transaction and preventing a lock-order cycle.
    conversation = AIConversation.all_objects.filter(pk=conversation_id, owner=actor).first()
    if conversation is None:
        raise PermissionDenied
    Request = apps.get_model("tasker", "AIWorkRequest")
    rows = list(Request.all_objects.select_for_update().filter(owner=actor, conversation_id=conversation.pk).order_by("pk"))
    now = timezone.now()
    _redact_requests(actor, rows, now)
    _redact_messages(actor, {conversation.pk}, now)
    conversation.refresh_from_db()
    return conversation


@transaction.atomic
def redact_ai_content(*, user, before, limit=100):
    """Explicit bounded retention for the owner's completed, older local content.

    The caller chooses an aware cutoff; there is no hidden company-wide default.
    Active requests are not age-pruned. Use redact_request to cancel one directly.
    Repeat until remaining=False to finish a larger local history.
    """
    actor = _owner(user)
    if not isinstance(before, datetime) or timezone.is_naive(before) or before > timezone.now():
        raise ValidationError("Choose a past timezone-aware retention cutoff.")
    if type(limit) is not int or not 1 <= limit <= 500:
        raise ValidationError("Choose a retention batch between 1 and 500 records.")
    Request = apps.get_model("tasker", "AIWorkRequest")
    eligible = Request.all_objects.filter(owner=actor, created_at__lt=before, content_redacted_at__isnull=True).exclude(status__in=("QUEUED", "RUNNING"))
    rows = list(eligible.select_for_update().order_by("created_at", "pk")[:limit])
    now = timezone.now()
    conversations = _redact_requests(actor, rows, now)
    # Never age-prune a shared conversation containing a newer/active request.
    retained = set(Request.all_objects.filter(owner=actor, conversation_id__in=conversations, content_redacted_at__isnull=True).values_list("conversation_id", flat=True))
    active = set(Request.all_objects.filter(owner=actor, conversation_id__in=retained,
        status__in=("QUEUED", "RUNNING")).values_list("conversation_id", flat=True))
    messages = _redact_messages(actor, retained - active, now, before=before)
    conversations -= retained
    private_messages = AIMessage.all_objects.filter(~Q(content="") | ~Q(message_metadata={}) | ~Q(provider_response_id="") | ~Q(ai_metadata={}))
    legacy = AIConversation.all_objects.filter(owner=actor, created_at__lt=before).filter(
        Q(is_deleted=False) | ~Q(title="") | ~Q(context_metadata={}) | ~Q(provider_conversation_id="")
        | ~Q(ai_metadata={}) | Q(pk__in=private_messages.values("conversation_id")))
    legacy = legacy.filter(Q(last_message_at__lt=before) | Q(last_message_at__isnull=True)).exclude(
        pk__in=Request.all_objects.filter(owner=actor, conversation__isnull=False, content_redacted_at__isnull=True).values("conversation_id"))
    legacy = legacy.exclude(pk__in=AIMessage.all_objects.filter(Q(created_at__gte=before) | Q(status=AIMessage.Status.PENDING)).values("conversation_id"))
    conversations.update(legacy.order_by("created_at", "pk").values_list("pk", flat=True)[:max(0, limit - len(rows))])
    messages += _redact_messages(actor, conversations, now)
    return {"requests": len(rows), "conversations": len(conversations), "messages": messages,
            "remaining": eligible.exists() or legacy.exclude(pk__in=conversations).exists()}
