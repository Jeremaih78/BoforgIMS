"""Transactional lifecycle operations for the distraction-free task timer."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from tasker.models import Task, TaskActivity, TaskTimeEntry


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
def start_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    running = (
        TaskTimeEntry.objects.select_for_update()
        .filter(user=user, ended_at__isnull=True)
        .select_related("task")
        .first()
    )
    if running:
        if running.task_id == task.pk:
            return running
        raise ActiveTimerConflict(running)
    if task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED) or task.archived:
        raise ValueError("Completed, cancelled, or archived tasks cannot be started.")

    now = timezone.now()
    task.status = Task.Status.IN_PROGRESS
    task.started_at = task.started_at or now
    task.blocked_reason = ""
    task.updated_by = user
    task.save(update_fields=["status", "started_at", "blocked_reason", "updated_by", "updated_at"])
    return TaskTimeEntry.objects.create(
        task=task,
        user=user,
        started_at=now,
        entry_type=TaskTimeEntry.EntryType.FOCUS,
        created_by=user,
        updated_by=user,
    )


@transaction.atomic
def pause_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    entry = _close_running_entry(task=task, user=user)
    if entry:
        _activity(task, user, "Focus timer paused")
    return entry


@transaction.atomic
def stop_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    entry = _close_running_entry(task=task, user=user)
    if entry:
        _activity(task, user, "Focus session stopped")
    return entry


@transaction.atomic
def block_task(*, task, user, reason):
    reason = reason.strip()
    if not reason:
        raise ValueError("A blocked reason is required.")
    task = Task.objects.select_for_update().get(pk=task.pk)
    close_task_timers(task=task, actor=user)
    task.status = Task.Status.BLOCKED
    task.blocked_reason = reason
    task.updated_by = user
    task.save(update_fields=["status", "blocked_reason", "updated_by", "updated_at"])
    return task


@transaction.atomic
def complete_focus(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    close_task_timers(task=task, actor=user)
    task.status = Task.Status.COMPLETED
    task.progress = 100
    task.completed_at = timezone.now()
    task.blocked_reason = ""
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
    return task


@transaction.atomic
def save_focus_notes(*, task, user, notes):
    task = Task.objects.select_for_update().get(pk=task.pk)
    task.notes = notes[:10000]
    task.updated_by = user
    task.save(update_fields=["notes", "updated_by", "updated_at"])
    return task


def _close_running_entry(*, task, user):
    entries = close_task_timers(task=task, actor=user, user=user)
    return entries[0] if entries else None


@transaction.atomic
def close_task_timers(*, task, actor, user=None):
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
    TaskTimeEntry.objects.bulk_update(
        entries, ["ended_at", "duration", "updated_by", "updated_at"]
    )
    total = TaskTimeEntry.objects.filter(task=task, duration__isnull=False).aggregate(
        total=Sum("duration")
    )["total"]
    task.actual_duration = total
    task.updated_by = user
    task._skip_tasker_activity = True
    task.save(update_fields=["actual_duration", "updated_by", "updated_at"])
    return entries


def _activity(task, user, description):
    TaskActivity.objects.create(
        task=task,
        event_type=TaskActivity.EventType.TIME,
        description=description,
        created_by=user,
        updated_by=user,
    )
