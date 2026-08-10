"""Application service for personal-only AI productivity recommendations."""

import json

from django.db import transaction
from django.utils import timezone

from tasker.models import AIConfiguration, AIMessage, AIUsageLog, Task, TaskChecklistItem
from tasker.services.tasks import add_checklist_item

from .exceptions import AIResponseValidationError
from .personal_context import build_personal_context, build_personal_task_context
from .service import AIService
from .settings import AIEnvironmentSettings


FEATURES = {
    "plan_day": "personal-plan-day",
    "next_task": "personal-next-task",
    "summarize_day": "personal-summarize-day",
    "prioritize": "personal-prioritize-tasks",
    "break_task": "personal-break-task",
    "explain_task": "personal-explain-task",
}


class PersonalProductivityAssistant:
    agent_type = "personal-productivity-assistant"

    def __init__(self, ai_service=None):
        self.ai_service = ai_service or AIService()

    def run(self, *, user, feature, task=None):
        if feature not in FEATURES:
            raise AIResponseValidationError()
        personal_context = build_personal_context(user=user)
        variables = {
            "employee_context": json.dumps(personal_context, ensure_ascii=False, separators=(",", ":")),
        }
        if task is not None:
            variables["task_context"] = json.dumps(
                build_personal_task_context(user=user, task=task),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        result = self.ai_service.generate_text(
            user=user,
            operation=feature.replace("_", "-"),
            prompt_key=FEATURES[feature],
            variables=variables,
            agent_type=self.agent_type,
            metadata={"feature": feature, "agent": self.agent_type, "schema_version": "1"},
        )
        try:
            payload = json.loads(result.text)
            self._validate(payload=payload, feature=feature, context=personal_context, task=task)
        except (ValueError, TypeError, KeyError, AIResponseValidationError) as exc:
            self._mark_invalid(result.message_id, result.usage_log_id, user)
            raise AIResponseValidationError() from exc
        message = AIMessage.objects.get(pk=result.message_id)
        message.message_metadata = {
            **message.message_metadata,
            "feature": feature,
            "task_id": task.pk if task else None,
            "structured_result": payload,
        }
        message.updated_by = user
        message.save(update_fields=["message_metadata", "updated_by", "updated_at"])
        return payload, message

    @staticmethod
    def _validate(*, payload, feature, context, task):
        if not isinstance(payload, dict) or not payload.get("reasoning"):
            raise AIResponseValidationError()
        allowed_ids = {item["id"] for item in context["current_workload"]}
        if feature == "plan_day":
            items = payload["recommendations"]
            PersonalProductivityAssistant._validate_recommendations(items, allowed_ids)
        elif feature == "next_task":
            if payload["task_id"] not in allowed_ids or not payload["why"]:
                raise AIResponseValidationError()
        elif feature == "prioritize":
            PersonalProductivityAssistant._validate_recommendations(payload["ranked_tasks"], allowed_ids)
        elif feature == "break_task":
            if payload["task_id"] != task.pk or not payload["steps"]:
                raise AIResponseValidationError()
            if any(not item.get("title") or not item.get("why") for item in payload["steps"]):
                raise AIResponseValidationError()
        elif feature == "explain_task" and payload["task_id"] != task.pk:
            raise AIResponseValidationError()

    @staticmethod
    def _validate_recommendations(items, allowed_ids):
        if not isinstance(items, list):
            raise AIResponseValidationError()
        if any(item.get("task_id") not in allowed_ids or not item.get("why") for item in items):
            raise AIResponseValidationError()

    @staticmethod
    @transaction.atomic
    def _mark_invalid(message_id, usage_log_id, user):
        AIMessage.objects.filter(pk=message_id).update(
            status=AIMessage.Status.FAILED,
            error_code=AIResponseValidationError.code,
            updated_by=user,
            updated_at=timezone.now(),
        )
        AIUsageLog.objects.filter(pk=usage_log_id).update(
            status=AIUsageLog.Status.FAILED,
            error_type="AIResponseValidationError",
            error_code=AIResponseValidationError.code,
            error_message=AIResponseValidationError.user_message,
            updated_by=user,
            updated_at=timezone.now(),
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
    if task.owner_id != user.pk:
        raise PermissionError
    message = (
        AIMessage.objects.select_for_update()
        .select_related("conversation")
        .get(pk=message_id, conversation__owner=user, status=AIMessage.Status.COMPLETED)
    )
    metadata = message.message_metadata
    if metadata.get("feature") != "break_task" or metadata.get("task_id") != task.pk:
        raise AIResponseValidationError()
    if metadata.get("applied_at"):
        return 0
    steps = metadata.get("structured_result", {}).get("steps", [])
    chosen = sorted({index for index in selected_indexes if 0 <= index < len(steps)})[:12]
    existing = {title.casefold() for title in task.checklist_items.values_list("title", flat=True)}
    added = 0
    for index in chosen:
        title = str(steps[index].get("title", "")).strip()[:255]
        if title and title.casefold() not in existing:
            add_checklist_item(task=task, user=user, title=title)
            existing.add(title.casefold())
            added += 1
    metadata["applied_at"] = timezone.now().isoformat()
    metadata["applied_step_indexes"] = chosen
    message.message_metadata = metadata
    message.updated_by = user
    message.save(update_fields=["message_metadata", "updated_by", "updated_at"])
    return added
