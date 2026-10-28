"""Application service for personal-only AI productivity recommendations."""

import hashlib
import json

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

from tasker.models import AIConfiguration, AIMessage, AIUsageLog, Task, TaskChecklistItem
from tasker.policies import can_change_task
from tasker.services.tasks import add_checklist_item

from .exceptions import AIPermissionError, AIResponseValidationError
from .personal_context import (
    ACTIVE_STATUSES, EXECUTABLE_STATUSES, PROMPT_ENVELOPE_CHARACTERS,
    build_personal_context, build_personal_task_context,
)
from .service import AIService
from .settings import AIEnvironmentSettings


FEATURES = {
    "plan_day": "personal-plan-day", "next_task": "personal-next-task",
    "summarize_day": "personal-summarize-day", "prioritize": "personal-prioritize-tasks",
    "break_task": "personal-break-task", "explain_task": "personal-explain-task",
}
READY_FEATURES = {"plan_day", "next_task", "prioritize"}
MAX_RECOMMENDED_MINUTES = 24 * 60


class NoReadyTasksError(AIResponseValidationError):
    code = "ai_no_ready_tasks"
    user_message = "No ready task can be recommended. Resolve a blocker or review your plan."


class AIStaleProposalError(AIResponseValidationError):
    code = "ai_proposal_stale"
    user_message = "This suggestion is out of date. Review the task and generate a new suggestion."


def _object(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        raise AIResponseValidationError()


def _text(value, maximum=4000):
    if type(value) is not str or not value.strip() or len(value) > maximum:
        raise AIResponseValidationError()


def _integer(value, maximum=None):
    if type(value) is not int or value <= 0 or (maximum is not None and value > maximum):
        raise AIResponseValidationError()


def _array(value, *, minimum=0, maximum=75):
    if type(value) is not list or not minimum <= len(value) <= maximum:
        raise AIResponseValidationError()


def _strings(values, maximum=50):
    _array(values, maximum=maximum)
    for value in values:
        _text(value)


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response fields are not permitted.")
        result[key] = value
    return result


def _task_revision(task):
    """Fingerprint all relevant task/checklist state, including queryset edits."""
    fields = ("pk", "owner_id", "title", "description", "priority", "status", "archived",
              "is_deleted", "updated_at", "estimated_duration", "due_date", "due_time", "progress",
              "blocked_reason", "parent_task_id")
    digest = hashlib.sha256(json.dumps({field: getattr(task, field) for field in fields},
        sort_keys=True, default=str, ensure_ascii=False).encode("utf-8"))
    # Do not hold the whole checklist in memory or put hidden/deleted item text
    # into a provider prompt. The fingerprint alone is stored on the proposal.
    items = TaskChecklistItem.all_objects.filter(task=task).order_by("pk").values(
        "pk", "title", "position", "is_completed", "is_deleted", "updated_at")
    for item in items.iterator(chunk_size=200):
        digest.update(json.dumps(item, sort_keys=True, default=str, ensure_ascii=False).encode("utf-8"))
    return digest.hexdigest()


class PersonalProductivityAssistant:
    agent_type = "personal-productivity-assistant"

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or AIService()

    def run(self, *, user, feature, task=None):
        if feature not in FEATURES:
            raise AIResponseValidationError()
        if not user.is_authenticated or not user.is_active or not user.has_perms(("tasker.use_ai", "tasker.view_task")):
            raise AIPermissionError()
        task_context = None
        task_revision = None
        if feature in {"break_task", "explain_task"}:
            if task is None:
                raise AIResponseValidationError()
            # A short snapshot transaction; no provider work runs under its lock.
            with transaction.atomic():
                try:
                    task = Task.objects.select_for_update().get(pk=task.pk, owner=user)
                except Task.DoesNotExist as exc:
                    raise AIPermissionError() from exc
                if feature == "break_task" and (task.archived or task.status not in ACTIVE_STATUSES):
                    raise AIStaleProposalError()
                task_context = build_personal_task_context(user=user, task=task)
                task_revision = _task_revision(task)
        elif task is not None:
            raise AIResponseValidationError()
        serialized_task = json.dumps(task_context, ensure_ascii=False, separators=(",", ":")) if task_context else ""
        available_characters = (AIEnvironmentSettings.load().max_prompt_characters
                                - PROMPT_ENVELOPE_CHARACTERS - len(serialized_task))
        personal_context = build_personal_context(user=user, max_characters=available_characters)
        if feature in READY_FEATURES and not any(
                item["status"] in EXECUTABLE_STATUSES for item in personal_context["current_workload"]):
            raise NoReadyTasksError()
        variables = {"employee_context": json.dumps(personal_context, ensure_ascii=False, separators=(",", ":"))}
        if task_context:
            variables["task_context"] = serialized_task
        result = self.ai_service.generate_text(
            user=user, operation=feature.replace("_", "-"), prompt_key=FEATURES[feature], variables=variables,
            agent_type=self.agent_type,
            metadata={"feature": feature, "agent": self.agent_type, "schema_version": "2"},
        )
        try:
            if not isinstance(result.text, str) or len(result.text) > 64000:
                raise AIResponseValidationError()
            payload = json.loads(result.text, object_pairs_hook=_unique_json_object)
            self._validate(payload=payload, feature=feature, context=personal_context, task=task)
            self._canonicalize_live(payload, feature=feature, user=user, task=task, task_revision=task_revision)
        except (ValueError, TypeError, KeyError, RecursionError, AIResponseValidationError) as exc:
            self._mark_invalid(result.message_id, result.usage_log_id, user)
            if isinstance(exc, AIResponseValidationError):
                raise
            raise AIResponseValidationError() from exc
        message = AIMessage.objects.get(pk=result.message_id, conversation__owner=user)
        message.message_metadata = {
            **message.message_metadata, "feature": feature, "task_id": task.pk if task else None,
            "task_revision": task_revision, "structured_result": payload,
        }
        message.updated_by = user
        message.save(update_fields=["message_metadata", "updated_by", "updated_at"])
        return payload, message

    @staticmethod
    def _validate(*, payload, feature, context, task):
        fields = {
            "plan_day": ("headline", "focus", "recommendations", "reasoning"),
            "next_task": ("task_id", "title", "next_action", "estimated_focus_minutes", "why", "reasoning"),
            "prioritize": ("headline", "ranked_tasks", "reasoning"),
            "summarize_day": ("headline", "accomplishments", "unfinished", "insights", "tomorrow_focus", "reasoning"),
            "break_task": ("task_id", "task_title", "approach", "steps", "reasoning"),
            "explain_task": ("task_id", "title", "plain_language_goal", "why_it_matters",
                             "definition_of_done", "first_action", "risks", "reasoning"),
        }
        if feature not in fields:
            raise AIResponseValidationError()
        _object(payload, fields[feature])
        _text(payload["reasoning"])
        workload = {item["id"]: item for item in context["current_workload"]}
        ready_ids = {task_id for task_id, item in workload.items() if item["status"] in EXECUTABLE_STATUSES}
        active_ids = {task_id for task_id, item in workload.items() if item["status"] in ACTIVE_STATUSES}
        if feature in {"plan_day", "prioritize"}:
            _text(payload["headline"], 255)
            if feature == "plan_day":
                _text(payload["focus"])
                items, rank_field, maximum = payload["recommendations"], "order", 4
                item_fields = ("task_id", "title", "order", "suggested_start", "estimated_minutes", "why")
            else:
                items, rank_field, maximum = payload["ranked_tasks"], "rank", 10
                item_fields = ("rank", "task_id", "title", "why", "next_action")
            _array(items, maximum=maximum)
            seen = set()
            for position, item in enumerate(items, 1):
                _object(item, item_fields)
                _integer(item["task_id"])
                _integer(item[rank_field], maximum)
                if item["task_id"] not in ready_ids or item["task_id"] in seen or item[rank_field] != position:
                    raise AIResponseValidationError()
                seen.add(item["task_id"])
                _text(item["title"], 255)
                _text(item["why"])
                if feature == "plan_day":
                    _text(item["suggested_start"], 100)
                    _integer(item["estimated_minutes"], MAX_RECOMMENDED_MINUTES)
                else:
                    _text(item["next_action"])
            if feature == "plan_day" and sum(item["estimated_minutes"] for item in items) > MAX_RECOMMENDED_MINUTES:
                raise AIResponseValidationError()
        elif feature == "next_task":
            _integer(payload["task_id"])
            if payload["task_id"] not in ready_ids:
                raise AIResponseValidationError()
            _text(payload["title"], 255)
            _text(payload["next_action"])
            _text(payload["why"])
            _integer(payload["estimated_focus_minutes"], MAX_RECOMMENDED_MINUTES)
        elif feature == "summarize_day":
            for key in ("headline", "tomorrow_focus"):
                _text(payload[key])
            _strings(payload["accomplishments"])
            _strings(payload["insights"])
            _array(payload["unfinished"])
            seen = set()
            for item in payload["unfinished"]:
                _object(item, ("task_id", "title", "why_unfinished", "next_action"))
                _integer(item["task_id"])
                if item["task_id"] not in active_ids or item["task_id"] in seen:
                    raise AIResponseValidationError()
                seen.add(item["task_id"])
                for key in ("title", "why_unfinished", "next_action"):
                    _text(item[key], 255 if key == "title" else 4000)
            if not context.get("completed_today") and payload["accomplishments"]:
                raise AIResponseValidationError()
        else:
            _integer(payload["task_id"])
            if task is None or payload["task_id"] != task.pk or task.is_deleted:
                raise AIResponseValidationError()
            if feature == "break_task":
                if task.archived or task.status not in ACTIVE_STATUSES:
                    raise AIResponseValidationError()
                _text(payload["task_title"], 255)
                _text(payload["approach"])
                _array(payload["steps"], minimum=3, maximum=12)
                titles = set()
                for position, item in enumerate(payload["steps"], 1):
                    _object(item, ("order", "title", "why", "estimated_minutes"))
                    _integer(item["order"], 12)
                    _integer(item["estimated_minutes"], MAX_RECOMMENDED_MINUTES)
                    _text(item["title"], 255)
                    _text(item["why"])
                    title = item["title"].strip().casefold()
                    if item["order"] != position or title in titles:
                        raise AIResponseValidationError()
                    titles.add(title)
            else:
                for key in ("title", "plain_language_goal", "why_it_matters", "definition_of_done", "first_action"):
                    _text(payload[key], 255 if key == "title" else 4000)
                _strings(payload["risks"])

    @staticmethod
    def _canonicalize_live(payload, *, feature, user, task, task_revision):
        if feature == "plan_day":
            items = payload["recommendations"]
        elif feature == "prioritize":
            items = payload["ranked_tasks"]
        elif feature == "summarize_day":
            items = payload["unfinished"]
        else:
            items = [payload]
        ids = {item["task_id"] for item in items}
        live = {item.pk: item for item in Task.objects.filter(owner=user, pk__in=ids)}
        for item in items:
            current = live.get(item["task_id"])
            if current is None or (feature in READY_FEATURES and (
                    current.archived or current.status not in EXECUTABLE_STATUSES)):
                raise AIStaleProposalError()
            if feature == "summarize_day" and (current.archived or current.status not in ACTIVE_STATUSES):
                raise AIStaleProposalError()
            if feature == "break_task" and _task_revision(current) != task_revision:
                raise AIStaleProposalError()
            item["task_title" if feature == "break_task" else "title"] = current.title

    @staticmethod
    @transaction.atomic
    def _mark_invalid(message_id, usage_log_id, user):
        AIMessage.objects.filter(pk=message_id).update(
            status=AIMessage.Status.FAILED, error_code=AIResponseValidationError.code,
            updated_by=user, updated_at=timezone.now(),
        )
        AIUsageLog.objects.filter(pk=usage_log_id).update(
            status=AIUsageLog.Status.FAILED, error_type="AIResponseValidationError",
            error_code=AIResponseValidationError.code, error_message=AIResponseValidationError.user_message,
            updated_by=user, updated_at=timezone.now(),
        )


def personal_ai_availability(user):
    if not user.is_authenticated or not user.has_perm("tasker.use_ai"):
        return {"available": False, "reason": "AI permission required."}
    configuration = AIConfiguration.objects.filter(is_active=True).first()
    if not configuration or not configuration.is_enabled:
        return {"available": False, "reason": "Boforg AI is currently disabled."}
    if not AIEnvironmentSettings.load().api_key:
        return {"available": False, "reason": "Boforg AI is not configured yet."}
    return {"available": True, "reason": ""}


@transaction.atomic
def apply_suggested_steps(*, user, task, message_id, selected_indexes):
    try:
        live_user = get_user_model().objects.get(pk=user.pk, is_active=True)
    except get_user_model().DoesNotExist as exc:
        raise PermissionError from exc
    if not live_user.has_perms(("tasker.use_ai", "tasker.view_task", "tasker.change_task", "tasker.add_taskchecklistitem")):
        raise PermissionError
    try:
        current = Task.all_objects.select_for_update().get(pk=task.pk)
    except Task.DoesNotExist as exc:
        raise AIStaleProposalError() from exc
    if current.owner_id != live_user.pk or not can_change_task(live_user, current):
        raise PermissionError
    message = AIMessage.objects.select_for_update().get(
        pk=message_id, conversation__owner=live_user, conversation__is_deleted=False,
        role=AIMessage.Role.ASSISTANT, status=AIMessage.Status.COMPLETED,
    )
    metadata = message.message_metadata
    if type(metadata) is not dict or metadata.get("feature") != "break_task" or metadata.get("task_id") != current.pk:
        raise AIResponseValidationError()
    if metadata.get("applied_at"):
        return 0
    if current.is_deleted or current.archived or current.status not in ACTIVE_STATUSES:
        raise AIStaleProposalError()
    if not metadata.get("task_revision") or metadata["task_revision"] != _task_revision(current):
        raise AIStaleProposalError()
    payload = metadata.get("structured_result")
    PersonalProductivityAssistant._validate(payload=payload, feature="break_task", context={"current_workload": []}, task=current)
    steps = payload["steps"]
    if type(selected_indexes) not in (list, tuple) or any(
            type(index) is not int or not 0 <= index < len(steps) for index in selected_indexes):
        raise AIResponseValidationError()
    chosen = sorted(set(selected_indexes))
    if not chosen:
        return 0
    existing = {title.strip().casefold() for title in current.checklist_items.values_list("title", flat=True)}
    added = 0
    for index in chosen:
        title = steps[index]["title"].strip()
        if title.casefold() not in existing:
            add_checklist_item(task=current, user=live_user, title=title)
            existing.add(title.casefold())
            added += 1
    metadata["applied_at"] = timezone.now().isoformat()
    metadata["applied_step_indexes"] = chosen
    message.message_metadata = metadata
    message.updated_by = live_user
    message.save(update_fields=["message_metadata", "updated_by", "updated_at"])
    return added
