"""Asynchronous, owner-private AI work through the existing gateway and outbox."""
from datetime import timedelta
import hashlib
import json
import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from tasker.ai.exceptions import AIServiceError
from tasker.ai.service import AIService
from tasker.ai_workflow_models import AIWorkRequest
from tasker.models import AIConfiguration, AIMessage, AIUsageLog, AIFeedback
from tasker.services.jobs import (
    JobCancelled, LostJobLease, assert_current_job_lease, fence_current_job_effects, enqueue_job,
)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _actor(user, *, require_ai=True):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or (require_ai and not actor.has_perms(("tasker.use_ai", "tasker.view_task"))):
        raise PermissionDenied
    if require_ai:
        from .pilot_access import feature_access
        if not feature_access(actor,'ai'):raise PermissionDenied
    return actor


def _enabled():
    if not getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False):
        raise ValidationError("Reviewed AI assistance is not enabled for this rollout.")


def _context(user, *, feature, task_id=None, capture_id=None, team_id=None, question=""):
    from tasker.ai.workflow_context import build_workflow_context
    return build_workflow_context(user=user, feature=feature, task_id=task_id,
        capture_id=capture_id, team_id=team_id, question=question)


def workflow_availability(user):
    """Offline configuration checks only; no credential authentication/provider call."""
    from tasker.ai.budgets import budget_status
    from tasker.ai.settings import AIEnvironmentSettings
    actor = _actor(user)
    configuration = AIConfiguration.objects.filter(is_active=True).first()
    budget = budget_status(actor)
    budget["reason_label"] = {"budget_unconfigured": "An administrator must review and enable the weekly spending policy.",
        "billing_company_unconfigured": "Your employee allowance and billing company need administrator setup.",
        "charge_review_required": "An earlier AI charge needs administrator review before more spending."}.get(budget.get("reason"), "")
    enabled = bool(configuration and configuration.is_enabled)
    credential_present = bool(AIEnvironmentSettings.load().api_key)
    from tasker.models import AIPriceCard
    from tasker.ai.capabilities import preflight_configuration
    card = AIPriceCard.objects.filter(provider=configuration.provider, model=configuration.model,
        service_tier="default", is_active=True, is_verified=True, verified_at__isnull=False,
        valid_until__gt=timezone.now()).first() if configuration else None
    model_ready = bool(card and preflight_configuration(configuration,
        profile=card.capability_profile, pricing_verified=True)["ok"])
    return {"configured": enabled and credential_present and model_ready and bool(budget.get("configured")),
        "enabled": enabled, "credential_present": credential_present,
        "budget": budget, "model_ready": model_ready, "authenticated": False,
        "message": "Credentials and model availability have not been authenticated by this check."}


def _source_access(user, item):
    """Re-authorize sources without comparing versions or exposing old context."""
    if item.content_redacted_at:
        return
    from tasker.ai.workflow_context import authorize_workflow_sources
    authorize_workflow_sources(user=user, context=item.context_snapshot)


def get_work_request(*, user, request_id):
    actor = _actor(user)
    item = AIWorkRequest.objects.filter(pk=request_id, owner=actor).first()
    if item is None:
        raise PermissionDenied
    _source_access(actor, item)
    return item


def visible_work_requests(user, *, limit=30):
    actor = _actor(user)
    visible = []
    for item in AIWorkRequest.objects.filter(owner=actor).order_by("-created_at", "-pk")[:limit]:
        try:
            _source_access(actor, item)
        except (PermissionDenied, ValidationError, AIServiceError):
            continue
        visible.append(item)
    return visible


def create_work_request(*, user, feature, client_key, task_id=None, capture_id=None,
        team_id=None, question="", retry_of=None):
    _enabled()
    from tasker.ai.workflow_schema import normalize_feature
    from tasker.ai.budgets import enrolled_company
    actor = _actor(user)
    feature = normalize_feature(feature)
    try:
        client_key = uuid.UUID(str(client_key))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValidationError("A valid request retry key is required.") from exc
    if type(question) is not str or len(question) > (16000 if feature == "MEETING" else 2000):
        raise ValidationError("Keep supplied content within the displayed limit.")
    arguments = {"feature": feature, "task_id": task_id, "capture_id": capture_id,
        "team_id": team_id, "question": question}
    request_digest = digest({**arguments, "retry_of": str(retry_of) if retry_of else None})
    # The retry identity is checked before rebuilding a potentially changed source.
    existing = AIWorkRequest.objects.filter(owner=actor, client_key=client_key).first()
    if existing:
        if existing.request_digest != request_digest:
            raise ValidationError("This retry key belongs to a different request.")
        _source_access(actor, existing)
        return existing
    context = _context(actor, **arguments)
    company = enrolled_company(actor)
    from tasker.models import Task, Team
    source_company = None
    if task_id:
        source_company = Task.objects.filter(pk=task_id).values_list("company_id", flat=True).first()
    elif team_id:
        source_company = Team.objects.filter(pk=team_id).values_list("company_id", flat=True).first()
    if company and source_company and company.pk != source_company:
        raise ValidationError("The selected work and configured billing company differ.")
    with transaction.atomic():
        # Short actor lock serializes duplicate HTTP submissions, not network work.
        get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
        existing = AIWorkRequest.objects.filter(owner=actor, client_key=client_key).first()
        if existing:
            if existing.request_digest != request_digest:
                raise ValidationError("This retry key belongs to a different request.")
            return existing
        if retry_of:
            old = AIWorkRequest.objects.filter(pk=retry_of, owner=actor).first()
            if old is None or old.status not in ("FAILED", "UNKNOWN", "CANCELLED"):
                raise ValidationError("Only a closed unsuccessful request can be retried.")
        item = AIWorkRequest.objects.create(owner=actor, company=company,
            feature=feature, scope=context["scope"], task_id=task_id,
            capture_id=capture_id, team_id=team_id, question=question,
            context_snapshot=context, context_digest=digest(context),
            request_digest=request_digest, client_key=client_key,
            expires_at=timezone.now()+timedelta(hours=2), retry_of_id=retry_of,
            created_by=actor, updated_by=actor)
        job = enqueue_job(kind="ai.generate", dedupe_key="ai-request:"+str(item.pk),
            payload={"request_id": str(item.pk)}, owner=actor)
        item.job = job
        item.save(update_fields=["job", "updated_at"])
    return item


@transaction.atomic
def cancel_work_request(*, user, request_id, expected_revision):
    actor = _actor(user, require_ai=False)
    item = AIWorkRequest.objects.select_for_update().filter(pk=request_id, owner=actor).first()
    if item is None:
        raise PermissionDenied
    if item.status == "CANCELLED":
        return item
    if str(item.revision) != str(expected_revision):
        raise ValidationError("This request changed. Review its current state.")
    if item.status not in ("QUEUED", "RUNNING"):
        raise ValidationError("Only waiting or running requests can be cancelled.")
    item.status, item.revision = "CANCELLED", uuid.uuid4()
    item.completed_at, item.updated_by = timezone.now(), actor
    item.save(update_fields=["status", "revision", "completed_at", "updated_by", "updated_at"])
    return item


def retry_work_request(*, user, request_id, client_key):
    item = get_work_request(user=user, request_id=request_id)
    return create_work_request(user=user, feature=item.feature, client_key=client_key,
        task_id=item.task_id, capture_id=item.capture_id, team_id=item.team_id,
        question=item.question, retry_of=item.pk)


def _fresh_context(actor, item):
    source = item.context_snapshot.get("source", {})
    if (digest(item.context_snapshot) != item.context_digest
            or item.context_snapshot.get("actor_id") != actor.pk
            or source != {"task_id": item.task_id, "capture_id": item.capture_id, "team_id": item.team_id}):
        raise ValidationError("The stored request evidence changed. Generate a fresh request.")
    expires = parse_datetime(item.context_snapshot.get("expires_at", ""))
    if expires is None or timezone.is_naive(expires) or expires <= timezone.now():
        raise ValidationError("The work snapshot expired. Generate a fresh request.")
    fresh = _context(actor, feature=item.feature, task_id=item.task_id,
        capture_id=item.capture_id, team_id=item.team_id, question=item.question)
    if fresh.get("proofs") != item.context_snapshot.get("proofs") or fresh["scope"] != item.scope:
        raise ValidationError("Source work changed. Generate a fresh reviewed request.")
    from tasker.ai.budgets import enrolled_company
    current_company = enrolled_company(actor)
    if (current_company.pk if current_company else None) != item.company_id:
        raise ValidationError("Billing enrollment changed. Review the request again.")
    return fresh


def _request_error(item_id, code, *, unknown=False):
    code = code if type(code) is str and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code) else "ai_request_failed"
    with transaction.atomic():
        item = AIWorkRequest.objects.select_for_update().get(pk=item_id)
        if item.status == "RUNNING":
            item.status = "UNKNOWN" if unknown else "FAILED"
            item.error_code, item.revision = code, uuid.uuid4()
            item.completed_at = timezone.now()
            item.save(update_fields=["status", "error_code", "revision", "completed_at", "updated_at"])


def _link_usage(request_id, *, result=None):
    """Link settled or failed gateway audits even after cancellation or erasure."""
    with transaction.atomic():
        current = AIWorkRequest.objects.select_for_update().get(pk=request_id)
        usage = AIUsageLog.objects.filter(user_id=current.owner_id,
            request_metadata__source=str(current.pk)).order_by("-started_at", "-pk").first()
        if result is not None:
            current.usage_log_id, current.conversation_id = result.usage_log_id, result.conversation_id
        elif usage:
            current.usage_log_id, current.conversation_id = usage.pk, usage.conversation_id
        current.save(update_fields=["usage_log", "conversation", "updated_at"])
        redacted, owner = current.content_redacted_at is not None, current.owner
    if redacted:
        from tasker.ai.privacy import erase_late_result
        erase_late_result(request_id=request_id)
    return current


class AIDispatchStopped(AIServiceError):
    code = "ai_dispatch_cancelled"
    user_message = "The request stopped before another AI call was sent."


def _before_dispatch(request_id, nonce):
    try:
        assert_current_job_lease()
    except LostJobLease as exc:
        raise AIDispatchStopped() from exc
    item = AIWorkRequest.objects.get(pk=request_id)
    if item.status != "RUNNING" or item.dispatch_nonce != nonce or item.content_redacted_at:
        raise AIDispatchStopped()
    if not getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False):
        raise AIDispatchStopped()
    _fresh_context(_actor(item.owner), item)


def handle_ai_work(*, payload, job=None, service=None):
    """Run outside the outbox transaction; network calls hold no domain locks.

    A second lease seeing RUNNING records uncertainty instead of resending.
    Cost settlement remains authoritative even if publication loses its lease.
    """
    from tasker.ai.workflow_schema import validate_workflow_payload
    from tasker.ai.workflow_prompts import PROMPT_KEYS
    from tasker.services.ai_proposals import create_proposal
    try:
        request_id = uuid.UUID(str(payload["request_id"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValidationError("Invalid AI request reference.") from exc
    with transaction.atomic():
        assert_current_job_lease()
        item = AIWorkRequest.objects.select_for_update().filter(pk=request_id).first()
        if item is None or item.status in ("CANCELLED", "COMPLETED", "FAILED", "UNKNOWN"):
            return JobCancelled("ai_request_closed")
        if not getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False):
            item.status, item.error_code, item.revision = "CANCELLED", "workflow_disabled", uuid.uuid4()
            item.completed_at = timezone.now()
            item.save(update_fields=["status", "error_code", "revision", "completed_at", "updated_at"])
            return JobCancelled("workflow_disabled")
        if item.status == "RUNNING":
            item.status, item.error_code, item.revision = "UNKNOWN", "interrupted_dispatch", uuid.uuid4()
            item.completed_at = timezone.now()
            item.save(update_fields=["status", "error_code", "revision", "completed_at", "updated_at"])
            return {"uncertain": 1}
        if item.expires_at <= timezone.now() or item.content_redacted_at:
            item.status, item.error_code = "FAILED", "request_expired"
            item.completed_at = timezone.now()
            item.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
            return JobCancelled("ai_request_expired")
        item.status, item.dispatch_nonce = "RUNNING", uuid.uuid4()
        item.started_at, item.revision = timezone.now(), uuid.uuid4()
        item.save(update_fields=["status", "dispatch_nonce", "started_at", "revision", "updated_at"])
        fence_current_job_effects()
    try:
        actor = _actor(item.owner)
        _fresh_context(actor, item)
        assert_current_job_lease()
        result = (service or AIService()).generate_text(user=actor,
            operation="workflow-"+item.feature.lower(), prompt_key=PROMPT_KEYS[item.feature],
            variables={"workflow_context": json.dumps(item.context_snapshot, ensure_ascii=False, separators=(",", ":"))},
            agent_type="workflow-assistant", metadata={"feature": item.feature, "source": str(item.pk), "schema_version": "3"},
            before_provider_attempt=lambda: _before_dispatch(item.pk, item.dispatch_nonce))
    except (AIServiceError, PermissionDenied, ValidationError) as exc:
        _link_usage(item.pk)
        _request_error(item.pk, getattr(exc, "code", "source_changed"), unknown=getattr(exc, "code", "") in ("ai_timeout", "ai_stream_interrupted"))
        return {"unavailable": 1}
    except BaseException:
        _link_usage(item.pk)
        _request_error(item.pk, "interrupted_dispatch", unknown=True)
        raise
    # First link durable usage, including cancellations/redactions, before trying
    # to publish. The AI gateway has already settled/held monetary usage.
    current = _link_usage(item.pk, result=result)
    if current.status != "RUNNING":
        return JobCancelled("ai_request_closed")
    try:
        with transaction.atomic():
            assert_current_job_lease()
            current = AIWorkRequest.objects.select_for_update().get(pk=item.pk)
            if current.status != "RUNNING" or current.dispatch_nonce != item.dispatch_nonce:
                return JobCancelled("ai_request_closed")
            if current.expires_at <= timezone.now():
                raise ValidationError("This request expired before its response arrived.")
            _fresh_context(_actor(current.owner), current)
            if type(result.text) is not str or len(result.text) > 64000:
                raise ValidationError("The proposal response is too large.")
            def unique(pairs):
                value = {}
                for key, child in pairs:
                    if key in value:
                        raise ValueError("Duplicate proposal key")
                    value[key] = child
                return value
            response = json.loads(result.text, object_pairs_hook=unique)
            validate_workflow_payload(response, current.context_snapshot, feature=current.feature)
            current.status, current.revision, current.completed_at = "COMPLETED", uuid.uuid4(), timezone.now()
            current.result_summary = {"headline": response["headline"], "summary": response["summary"]}
            current.save(update_fields=["status", "revision", "completed_at", "result_summary", "updated_at"])
            proposal = create_proposal(user=actor, request_id=current.pk, feature=current.feature,
                payload=response, context=current.context_snapshot)
            fence_current_job_effects()
        return {"proposals": 1, "actions": len(response["actions"])}
    except LostJobLease:
        _request_error(item.pk, "interrupted_dispatch", unknown=True)
        raise
    except (ValidationError, PermissionDenied, AIServiceError, ValueError, TypeError, KeyError):
        _request_error(item.pk, "invalid_or_stale_proposal")
        return {"invalid": 1}


@transaction.atomic
def record_feedback(*, user, request_id, rating, comment=""):
    # Match erasure's actor -> request -> message order so a late feedback POST
    # cannot recreate private content after an owner removed it.
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    AIWorkRequest.objects.select_for_update().filter(pk=request_id, owner=user).first()
    item = get_work_request(user=user, request_id=request_id)
    if item.status != "COMPLETED" or not item.usage_log_id or item.content_redacted_at:
        raise ValidationError("Feedback is available for retained completed suggestions.")
    if type(rating) is not int or rating not in (-1, 1) or type(comment) is not str or len(comment) > 500:
        raise ValidationError("Choose helpful or unhelpful and keep feedback brief.")
    message_id = AIUsageLog.objects.filter(pk=item.usage_log_id, user=user).values_list("message_id", flat=True).first()
    if not message_id or not AIMessage.objects.filter(pk=message_id, conversation__owner=user).exists():
        raise ValidationError("The suggestion message is unavailable.")
    return AIFeedback.objects.update_or_create(user=user, message_id=message_id,
        defaults={"rating": rating, "comment": comment, "updated_by": user, "created_by": user})[0]
