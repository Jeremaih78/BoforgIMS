"""Explicit remaining effort, anchored to recorded time; exhaustion is unknown."""
import hashlib
import json
import math
import uuid

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from tasker.models import Task, TaskActivity, TaskTimeEntry
from tasker.organization import require_task_access
from tasker.planning_models import TaskEffortEstimate
from tasker.services.capacity import PlanningConflict, _actor, _event, locked_policy
from tasker.services.workbench import work_version


def _record(task):
    try:
        record = task.remaining_effort_estimate
        return record if not record.is_deleted else None
    except TaskEffortEstimate.DoesNotExist:
        return None


def logged_time(*, task, now=None):
    now = now or timezone.now()
    running = list(TaskTimeEntry.objects.filter(task=task, ended_at__isnull=True).values_list("pk", "started_at"))
    closed = max(0, int(task.actual_duration.total_seconds())) if task.actual_duration else 0
    total = closed + sum(max(0, int((now - started).total_seconds())) for _, started in running)
    return total, running


def effort_for_task(*, task, now=None, logged=None):
    estimate = _record(task)
    total, running = logged if logged is not None else logged_time(task=task, now=now)
    remaining, state, needs = None, "UNKNOWN", False
    if task.status == Task.Status.COMPLETED:
        remaining, state = 0, "COMPLETED"
    elif estimate and estimate.remaining_minutes is not None:
        elapsed = max(0, total - estimate.logged_seconds_at_estimate)
        if elapsed >= estimate.remaining_minutes * 60 and (elapsed > 0 or estimate.remaining_minutes > 0):
            state, needs = "REESTIMATE", True
        else:
            remaining = max(0, estimate.remaining_minutes - math.ceil(elapsed / 60))
            state = "KNOWN" if remaining else "REVIEW"
    elif estimate:
        state = "UNKNOWN"
    elif task.estimated_duration is not None and total == 0:
        remaining = max(0, math.ceil(task.estimated_duration.total_seconds() / 60))
        state = "INITIAL" if remaining else "REVIEW"
    elif task.estimated_duration is not None and total > 0:
        # A total estimate is not a current human judgement of remaining work.
        state, needs = "REESTIMATE", True
    snapshot = {"task": work_version(task), "estimate": [str(estimate.revision), estimate.remaining_minutes, estimate.logged_seconds_at_estimate] if estimate else None,
                "running": [(pk, start.isoformat()) for pk, start in running], "state": state}
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    labels = {"UNKNOWN": "Remaining effort unknown", "REESTIMATE": "Re-estimate remaining work", "INITIAL": "Initial estimate; no recorded time yet",
              "KNOWN": "Remaining estimate", "REVIEW": "Outcome review remains", "COMPLETED": "Completed and accepted"}
    return {"remaining_minutes": remaining, "state": state, "needs_reestimate": needs, "version": version,
            "label": labels[state], "estimate": estimate, "logged_seconds": total}


@transaction.atomic
def set_remaining_effort(*, user, task, remaining_minutes, reason="", expected_version=None):
    user = _actor(user)
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    locked_policy(task.owner)
    current = effort_for_task(task=task)
    if current["version"] != str(expected_version or ""):
        raise PlanningConflict("This task or its effort estimate changed. Reload before estimating remaining work.")
    if remaining_minutes is not None and (type(remaining_minutes) is not int or not 0 <= remaining_minutes <= 525600):
        raise ValidationError("Enter remaining minutes from zero to 525,600, or leave it unknown.")
    if not isinstance(reason, str) or len(reason) > 1000 or "\x00" in reason:
        raise ValidationError("Use an estimation note of up to 1,000 characters.")
    if current["needs_reestimate"] and not reason.strip():
        raise ValidationError("Briefly record why the remaining estimate changed after recorded work.")
    record, _ = TaskEffortEstimate.all_objects.get_or_create(task=task, defaults={"created_by": user})
    record = TaskEffortEstimate.all_objects.select_for_update().get(pk=record.pk)
    if record.is_deleted:
        raise ValidationError("Restore the estimate record before editing it.")
    record.remaining_minutes = remaining_minutes
    record.logged_seconds_at_estimate = current["logged_seconds"]
    record.reason, record.revision, record.updated_by = reason.strip(), uuid.uuid4(), user
    record.save()
    _event(owner=task.owner, actor=user, action="effort.estimated", record=record, details={"task_id": task.pk, "remaining_minutes": remaining_minutes, "revision": str(record.revision)})
    TaskActivity.objects.create(task=task, event_type=TaskActivity.EventType.UPDATED, description="Remaining effort explicitly estimated",
        changes={"remaining_minutes": remaining_minutes, "reason": reason.strip()}, created_by=user, updated_by=user)
    return record
