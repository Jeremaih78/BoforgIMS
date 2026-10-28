"""Transactional lifecycle operations for the distraction-free task timer."""

from __future__ import annotations

import hashlib
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from tasker.models import Task, TaskActivity, TaskTimeEntry
from tasker.services.lifecycle import prepare_task_state, save_without_task_activity
from tasker.organization import require_task_access


class ActiveTimerConflict(Exception):
    """Raised when a user attempts to run more than one timer."""

    def __init__(self, entry):
        self.entry = entry
        super().__init__("Another focus timer is already running.")


def running_entry_for(*, user):
    return (
        TaskTimeEntry.objects.filter(user=user, ended_at__isnull=True)
        .select_related("task")
        .first()
    )


@transaction.atomic
def start_focus(*, task, user, interval_minutes=None, resume_interval=False):
    minutes = _interval_minutes(interval_minutes)
    # One actor lock serializes starts on different tasks without introducing
    # another timer. NO KEY UPDATE stays compatible with audit/FK key shares.
    user = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if user is None:
        raise PermissionDenied
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    if task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED) or task.archived:
        raise ValueError("Completed, cancelled, or archived tasks cannot be started.")
    from tasker.services.dependencies import assert_task_ready
    assert_task_ready(task=task, user=user)
    running = (
        TaskTimeEntry.objects.select_for_update(of=("self",))
        .filter(user=user, ended_at__isnull=True)
        .select_related("task")
        .first()
    )
    if running:
        if running.task_id == task.pk:
            if minutes is not None:
                running.ai_metadata = _interval_metadata(minutes, running=running)
                running.updated_by = user
                running.save(update_fields=["ai_metadata", "updated_by", "updated_at"])
                _activity(task, user, "Focus interval target set")
            return running
        raise ActiveTimerConflict(running)
    can_resume_interval = resume_interval and task.status == Task.Status.IN_PROGRESS
    now = timezone.now()
    task.status = Task.Status.IN_PROGRESS
    task.started_at = task.started_at or now
    task.blocked_reason = ""
    task.updated_by = user
    task.save(update_fields=["status", "started_at", "blocked_reason", "updated_by", "updated_at"])
    metadata = _interval_metadata(minutes) if minutes is not None else {}
    if can_resume_interval and minutes is None:
        previous = TaskTimeEntry.objects.filter(task=task, user=user, entry_type=TaskTimeEntry.EntryType.FOCUS).order_by("-started_at", "-pk").first()
        if previous and _valid_interval(previous.ai_metadata) and not previous.ai_metadata.get("focus_interval_closed"):
            metadata = dict(previous.ai_metadata)
    return TaskTimeEntry.objects.create(
        task=task,
        user=user,
        started_at=now,
        entry_type=TaskTimeEntry.EntryType.FOCUS,
        ai_metadata=metadata,
        created_by=user,
        updated_by=user,
    )


@transaction.atomic
def pause_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    entry = _close_running_entry(task=task, user=user, end_interval=False)
    if entry:
        _activity(task, user, "Focus timer paused")
    return entry


@transaction.atomic
def stop_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    entry = _close_running_entry(task=task, user=user)
    if entry is None:
        previous = TaskTimeEntry.objects.select_for_update().filter(task=task, user=user, entry_type=TaskTimeEntry.EntryType.FOCUS).order_by("-started_at", "-pk").first()
        if previous and _valid_interval(previous.ai_metadata) and not previous.ai_metadata.get("focus_interval_closed"):
            previous.ai_metadata = {**previous.ai_metadata, "focus_interval_closed": True}
            previous.updated_by = user
            previous.save(update_fields=["ai_metadata", "updated_by", "updated_at"])
    if entry:
        _activity(task, user, "Focus session stopped")
    return entry


@transaction.atomic
def stop_own_focus(*, user):
    """Stop only the actor's time, even if task scope has since been revoked."""
    user = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if user is None or not user.has_perm("tasker.view_task"):
        raise PermissionDenied
    task_id = TaskTimeEntry.objects.filter(user=user, ended_at__isnull=True).values_list("task_id", flat=True).first()
    if not task_id:
        return False
    task = Task.all_objects.select_for_update().get(pk=task_id)
    return bool(close_task_timers(task=task, actor=user, user=user))


@transaction.atomic
def block_task(*, task, user, reason):
    reason = reason.strip()
    if not reason:
        raise ValueError("A blocked reason is required.")
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    if task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
        raise ValueError("Reopen or unarchive this task before marking it blocked.")
    close_task_timers(task=task, actor=user)
    task.status = Task.Status.BLOCKED
    task.blocked_reason = reason
    task.updated_by = user
    prepare_task_state(task)
    task.save(update_fields=["status", "completed_at", "blocked_reason", "updated_by", "updated_at"])
    return task


@transaction.atomic
def complete_focus(*, task, user, accept_definition=False, definition_version=None):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    if task.archived or task.status == Task.Status.CANCELLED:
        raise ValueError("Reopen or unarchive this task before completing it.")
    if definition_version is not None and definition_version != completion_definition_version(task):
        raise ValueError("The definition of done changed. Review the current definition before completing this task.")
    from tasker.services.lifecycle import require_completion_acceptance
    require_completion_acceptance(task, accepted=accept_definition)
    close_task_timers(task=task, actor=user)
    previous_status = task.status
    task.status = Task.Status.COMPLETED
    task.progress = 100
    prepare_task_state(task, previous_status=previous_status)
    task.updated_by = user
    task.save(
        update_fields=[
            "status",
            "progress",
            "completed_at",
            "blocked_reason",
            "updated_by",
            "updated_at",
        ]
    )
    from tasker.services.scheduling import invalidate_task_schedule
    invalidate_task_schedule(task=task,actor=user,reason="Task completed")
    return task


@transaction.atomic
def save_focus_notes(*, task, user, notes):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.view_task")
    task.notes = notes[:10000]
    task.updated_by = user
    task.save(update_fields=["notes", "updated_by", "updated_at"])
    return task


def _close_running_entry(*, task, user, end_interval=True):
    entries = close_task_timers(task=task, actor=user, user=user, end_interval=end_interval)
    return entries[0] if entries else None


@transaction.atomic
def close_task_timers(*, task, actor, user=None, end_interval=True):
    """Close active timers safely and synchronize the task's authoritative duration."""
    entries = list(
        TaskTimeEntry.objects.select_for_update().filter(
            task=task,
            ended_at__isnull=True,
            **({"user": user} if user is not None else {}),
        )
    )
    if not entries:
        return []
    current_time = timezone.now()
    for entry in entries:
        entry.ended_at = max(current_time, entry.started_at)
        entry.duration = entry.ended_at - entry.started_at
        entry.updated_by = actor
        entry.updated_at = current_time
        if end_interval and _valid_interval(entry.ai_metadata):
            entry.ai_metadata = {**entry.ai_metadata, "focus_interval_closed": True}
    TaskTimeEntry.objects.bulk_update(
        entries, ["ended_at", "duration", "updated_by", "updated_at", "ai_metadata"]
    )
    total = TaskTimeEntry.objects.filter(task=task, duration__isnull=False).aggregate(
        total=Sum("duration")
    )["total"]
    task.actual_duration = total
    task.updated_by = actor
    save_without_task_activity(task, update_fields=["actual_duration", "updated_by", "updated_at"])
    return entries


def _activity(task, user, description):
    TaskActivity.objects.create(
        task=task,
        event_type=TaskActivity.EventType.TIME,
        description=description,
        created_by=user,
        updated_by=user,
    )


def completion_definition_version(task):
    """Bind a Focus acceptance to the exact definition shown on that page."""
    return hashlib.sha256(task.definition_of_done.encode("utf-8")).hexdigest()


def _interval_minutes(value):
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        raise ValueError("Choose an interval between 1 and 240 minutes.")
    try:
        minutes = int(value)
        if str(minutes) != str(value) or not 1 <= minutes <= 240:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError("Choose an interval between 1 and 240 minutes.") from None
    return minutes


def _interval_metadata(minutes, *, running=None):
    metadata = dict(running.ai_metadata) if running and isinstance(running.ai_metadata, dict) else {}
    metadata.update({"focus_interval_id": str(uuid.uuid4()), "focus_interval_minutes": minutes,
                     "focus_interval_offset_seconds": max(0, int((timezone.now() - running.started_at).total_seconds())) if running else 0,
                     "focus_interval_closed": False})
    return metadata


def _valid_interval(metadata):
    if not isinstance(metadata, dict) or type(metadata.get("focus_interval_minutes")) is not int:
        return False
    try:
        uuid.UUID(metadata.get("focus_interval_id", ""))
        offset = metadata.get("focus_interval_offset_seconds", 0)
        return 1 <= metadata["focus_interval_minutes"] <= 240 and type(offset) is int and offset >= 0
    except (TypeError, ValueError, AttributeError):
        return False


def focus_interval_context(*, task, user, running=None):
    """A target derived from existing focus entries, never another timer."""
    if running and (running.task_id != task.pk or running.user_id != user.pk):
        running = None
    entry = running or TaskTimeEntry.objects.filter(task=task, user=user, entry_type=TaskTimeEntry.EntryType.FOCUS).order_by("-started_at", "-pk").first()
    if not entry or not _valid_interval(entry.ai_metadata) or entry.ai_metadata.get("focus_interval_closed"):
        return {"minutes": None, "base_seconds": 0, "started_at": None, "running": False}
    metadata = entry.ai_metadata
    total = TaskTimeEntry.objects.filter(task=task, user=user, entry_type=TaskTimeEntry.EntryType.FOCUS,
        ai_metadata__focus_interval_id=metadata["focus_interval_id"], ended_at__isnull=False).aggregate(total=Sum("duration"))["total"]
    saved = int(total.total_seconds()) if total else 0
    return {"minutes": metadata["focus_interval_minutes"],
            "base_seconds": saved - metadata.get("focus_interval_offset_seconds", 0),
            "started_at": running.started_at if running else None, "running": running is not None}
