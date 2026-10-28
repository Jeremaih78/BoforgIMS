"""Preview first, accept explicitly; block times never rewrite task deadlines."""
from datetime import time, timedelta, timezone as datetime_timezone
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from tasker.models import Task, TaskActivity
from tasker.organization import require_task_access
from tasker.policies import can_change_task, visible_tasks_for
from tasker.planning_models import ScheduleBlock, validate_span
from tasker.services.capacity import (
    PlanningConflict, _actor, _event, _expect, aware_local, capacity_for_day,
    interval_intersections, interval_minutes, locked_policy, policy_for,
)
from tasker.services.effort import effort_for_task
from tasker.services.workbench import work_version

UTC = datetime_timezone.utc


class ScheduleReviewRequired(ValueError):
    def __init__(self, preview):
        self.preview = preview
        super().__init__("Review the conflicts and choose another time, resolve the work, or explicitly authorize the capacity override with a reason.")


def _current_task(user, task):
    task = visible_tasks_for(user).filter(pk=task.pk).first()
    if task is None or not can_change_task(user, task):
        raise PermissionDenied
    return task


def visible_schedule_blocks(user):
    user = _actor(user)
    return ScheduleBlock.objects.filter(task__in=visible_tasks_for(user)).select_related("task", "owner")


def _dates(starts_at, ends_at, zone):
    first = starts_at.astimezone(zone).date()
    last = (ends_at.astimezone(UTC) - timedelta(microseconds=1)).astimezone(zone).date()
    return [first + timedelta(days=index) for index in range((last-first).days+1)]


def _warning(result, code, message):
    if not any(item["code"] == code for item in result):
        result.append({"code": code, "message": message})


def preview_schedule(*, user, task, starts_at, ends_at, block=None):
    user = _actor(user)
    task = _current_task(user, task)
    validate_span(starts_at, ends_at, maximum_days=1)
    if block is not None:
        block = visible_schedule_blocks(user).filter(pk=block.pk, task=task).first()
        if block is None:
            raise PermissionDenied
    policy = policy_for(task.owner)
    zone_name = policy.timezone_name if policy else "Africa/Harare"
    zone = ZoneInfo(zone_name)
    warnings, hard = [], []
    if block is not None and block.owner_id != task.owner_id:
        _warning(hard, "OWNER_CHANGED", "The task owner changed. Create a new block for the current owner.")
    if block is not None and block.state == ScheduleBlock.State.CANCELLED:
        _warning(hard, "BLOCK_CANCELLED", "This block was cancelled. Edit it into a new draft before accepting work.")
    if not task.owner.is_active:
        _warning(hard, "OWNER_UNAVAILABLE", "The task owner is inactive. Delegate the task to an active authorized employee before scheduling.")
    if task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
        _warning(hard, "TASK_UNAVAILABLE", "Reopen or unarchive the task before scheduling its work.")
    if task.status == Task.Status.BLOCKED:
        _warning(hard, "TASK_BLOCKED", "Resolve or explicitly mark the task ready before accepting a work block.")
    from tasker.services.dependencies import readiness_for
    ready = readiness_for(task=task, user=user)
    if not ready.ready:
        _warning(hard, "DEPENDENCY_WAITING", ready.reason)
    if starts_at < timezone.now():
        _warning(hard, "PAST_BLOCK", "Past dates are for review. Choose a current or future work block.")
    capacity = []
    for day in _dates(starts_at, ends_at, zone):
        current = capacity_for_day(user=task.owner, day=day, actor=user, exclude_block=block)
        capacity.append(current)
        if not current["known"]:
            _warning(warnings, "CAPACITY_UNKNOWN", "Capacity is unknown. Configure hours and an explicit interruption buffer, or record an authorized planning override.")
            continue
        boundary = [(aware_local(day, time.min, zone), aware_local(day + timedelta(days=1), time.min, zone))]
        proposed = interval_intersections([(starts_at, ends_at)], boundary)
        minutes = interval_minutes(proposed)
        inside = interval_minutes(interval_intersections(proposed, current["work_intervals"]))
        free = interval_minutes(interval_intersections(proposed, current["free_intervals"]))
        if inside < minutes:
            _warning(warnings, "OUTSIDE_HOURS", "Part of this block is outside the configured working hours.")
        if free < inside:
            _warning(warnings, "AVAILABILITY_CONFLICT", "This block overlaps leave or a fixed commitment. Private exclusion notes are not shared.")
        if interval_minutes(interval_intersections(proposed, current["booked_intervals"])):
            _warning(warnings, "BLOCK_CONFLICT", "This block overlaps an already accepted work block.")
        if current["committed_minutes"] + minutes > current["usable_minutes"]:
            _warning(warnings, "CAPACITY_OVERLOAD", "Accepted work plus this block exceeds usable capacity after exclusions and interruption buffer.")
    effort = effort_for_task(task=task)
    if effort["remaining_minutes"] is None:
        _warning(warnings, "EFFORT_UNKNOWN", "Remaining effort is unknown or needs re-estimation. Set it explicitly or record an authorized planning override.")
    else:
        reserved = ScheduleBlock.objects.filter(task=task, owner=task.owner, state=ScheduleBlock.State.ACCEPTED, ends_at__gt=timezone.now())
        if block is not None: reserved = reserved.exclude(pk=block.pk)
        reserved_minutes = sum(interval_minutes([(item.starts_at, item.ends_at)]) for item in reserved.only("starts_at", "ends_at"))
        if reserved_minutes + interval_minutes([(starts_at, ends_at)]) > effort["remaining_minutes"]:
            _warning(warnings, "EFFORT_OVERBOOKED", "Planned blocks exceed the current remaining-effort estimate. Review the split or re-estimate the work.")
    if task.due_date:
        try:
            deadline = aware_local(task.due_date, task.due_time or time(23,59,59), zone)
            if ends_at.astimezone(UTC) > deadline.astimezone(UTC):
                _warning(warnings, "AFTER_DEADLINE", "This work block ends after the task deadline. Moving a block does not change that deadline.")
        except ValidationError:
            _warning(warnings, "DEADLINE_TIME_UNKNOWN", "The deadline's local time is ambiguous; review it explicitly.")
    snapshot = {"task": work_version(task), "owner": task.owner_id, "start": starts_at.astimezone(UTC).isoformat(), "end": ends_at.astimezone(UTC).isoformat(),
        "block": str(block.revision) if block else None, "capacity": [item["version"] for item in capacity], "effort": effort["version"],
        "dependency": ready.version, "warnings": [item["code"] for item in warnings], "hard": [item["code"] for item in hard]}
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return {"task": task, "block": block, "starts_at": starts_at, "ends_at": ends_at, "timezone_name": zone_name,
        "warnings": warnings, "hard_errors": hard, "can_accept": not hard, "version": version,
        "block_version": str(block.revision) if block else "", "task_version": work_version(task),
        "dependency_version": ready.version, "capacity": capacity, "effort": effort,
        "duration_minutes": interval_minutes([(starts_at, ends_at)])}


@transaction.atomic
def save_schedule_draft(*, user, task, starts_at, ends_at, block=None, expected_version=None):
    user = _actor(user)
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    validate_span(starts_at, ends_at, maximum_days=1)
    locked_policy(task.owner)
    previous_owner = None
    if block is not None:
        block = ScheduleBlock.objects.select_for_update().filter(pk=block.pk, task=task).first()
        if block is None:
            raise PermissionDenied
        _expect(block, expected_version)
        previous_owner = block.owner_id
        if block.owner_id != task.owner_id:
            raise PlanningConflict("The task owner changed. Create a new block for its current owner.")
    else:
        block = ScheduleBlock(task=task, owner=task.owner, created_by=user)
    block.starts_at, block.ends_at, block.state = starts_at, ends_at, ScheduleBlock.State.DRAFT
    block.accepted_at, block.override_reason, block.acceptance_snapshot = None, "", {}
    block.revision, block.updated_by = uuid.uuid4(), user
    block.save()
    _event(owner=task.owner, actor=user, action="schedule.draft_saved", record=block, details={"task_id": task.pk, "revision": str(block.revision), "previous_owner": previous_owner})
    return block


@transaction.atomic
def accept_schedule(*, user, block, expected_version, preview_version, override_reason=""):
    user = _actor(user)
    # Lock the task before the serialization policy, matching task mutation hooks.
    task_id = ScheduleBlock.objects.filter(pk=block.pk).values_list("task_id", flat=True).first()
    if not task_id:
        raise PermissionDenied
    task = Task.objects.select_for_update().get(pk=task_id)
    require_task_access(user, task, capability="tasker.view_task")
    locked_policy(task.owner)
    block = ScheduleBlock.objects.select_for_update().get(pk=block.pk)
    _expect(block, expected_version)
    if block.state != ScheduleBlock.State.DRAFT or block.owner_id != task.owner_id:
        raise PlanningConflict("This block is no longer the reviewed draft. Reload the plan.")
    preview = preview_schedule(user=user, task=task, starts_at=block.starts_at, ends_at=block.ends_at, block=block)
    if preview["version"] != str(preview_version or ""):
        raise PlanningConflict("The task, capacity or dependencies changed after preview. Review a fresh preview before accepting.")
    if preview["hard_errors"]:
        raise ScheduleReviewRequired(preview)
    if not isinstance(override_reason, str) or len(override_reason) > 1000 or "\x00" in override_reason:
        raise ValidationError("Use an override reason of up to 1,000 characters.")
    if preview["warnings"] and not override_reason.strip():
        raise ScheduleReviewRequired(preview)
    from tasker.services.dependencies import assert_task_ready
    assert_task_ready(task=task, user=user, expected_version=preview["dependency_version"])
    block.state, block.accepted_at = ScheduleBlock.State.ACCEPTED, timezone.now()
    block.override_reason = override_reason.strip()
    block.acceptance_snapshot = {"preview_version": preview["version"], "dependency_version": preview["dependency_version"],
        "warnings": preview["warnings"], "capacity": [{key:item[key] for key in ("day", "known", "work_minutes", "excluded_minutes", "buffer_minutes", "usable_minutes", "committed_minutes")} for item in preview["capacity"]],
        "remaining_minutes": preview["effort"]["remaining_minutes"], "actor_id": user.pk}
    # JSON snapshots have explicit ISO dates, without labels or private notes.
    for item in block.acceptance_snapshot["capacity"]: item["day"] = str(item["day"])
    block.revision, block.updated_by = uuid.uuid4(), user
    block.save()
    _event(owner=task.owner, actor=user, action="schedule.accepted", record=block, details={"task_id": task.pk, "revision": str(block.revision), "override_reason": block.override_reason, "snapshot": block.acceptance_snapshot})
    TaskActivity.objects.create(task=task, event_type=TaskActivity.EventType.UPDATED, description="Work block accepted" + (" with explicit planning override" if block.override_reason else ""),
        changes={"schedule_block_id": block.pk, "starts_at": block.starts_at.isoformat(), "ends_at": block.ends_at.isoformat(), "override_reason": block.override_reason}, created_by=user, updated_by=user)
    return block


@transaction.atomic
def cancel_schedule(*, user, block, expected_version):
    user = _actor(user)
    task_id = ScheduleBlock.objects.filter(pk=block.pk).values_list("task_id", flat=True).first()
    if not task_id: raise PermissionDenied
    task = Task.objects.select_for_update().get(pk=task_id)
    require_task_access(user, task, capability="tasker.view_task")
    locked_policy(task.owner)
    block = ScheduleBlock.objects.select_for_update().get(pk=block.pk)
    _expect(block, expected_version)
    block.state, block.revision, block.updated_by = ScheduleBlock.State.CANCELLED, uuid.uuid4(), user
    block.save()
    _event(owner=block.owner, actor=user, action="schedule.cancelled", record=block, details={"task_id": task.pk})
    return block


def invalidate_task_schedule(*, task, actor, reason="Task ownership, scope or lifecycle changed"):
    """Caller holds Task lock in an atomic mutation; cancelled blocks never revive."""
    owner_ids = sorted(set(ScheduleBlock.objects.filter(task=task).exclude(state=ScheduleBlock.State.CANCELLED).values_list("owner_id", flat=True)))
    from django.contrib.auth import get_user_model
    for owner in get_user_model().objects.filter(pk__in=owner_ids).order_by("pk"):
        locked_policy(owner)
    blocks = list(ScheduleBlock.objects.select_for_update().filter(task=task).exclude(state=ScheduleBlock.State.CANCELLED).order_by("pk"))
    for block in blocks:
        block.state, block.revision, block.updated_by = ScheduleBlock.State.CANCELLED, uuid.uuid4(), actor
        block.save()
        _event(owner=block.owner, actor=actor, action="schedule.invalidated", record=block, details={"task_id": task.pk, "reason": reason})
    return len(blocks)
