from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Sum
from django.utils import timezone

from tasker.models import (
    Task,
    TaskActivity,
    TaskChecklistItem,
    TaskComment,
    TaskTimeEntry,
)
from tasker.services.focus import close_task_timers


@transaction.atomic
def save_task(*, form, user):
    task = form.save(commit=False)
    is_new = task.pk is None
    previous_status = None
    if not is_new:
        previous_status = (
            Task.all_objects.select_for_update().values_list("status", flat=True).get(pk=task.pk)
        )

    if is_new:
        task.assigned_by = user
        task.created_by = user
    task.updated_by = user
    if task.status == Task.Status.IN_PROGRESS and not task.started_at:
        task.started_at = timezone.now()
    if task.status == Task.Status.COMPLETED:
        task.progress = 100
        if previous_status != Task.Status.COMPLETED or not task.completed_at:
            task.completed_at = timezone.now()
    elif previous_status == Task.Status.COMPLETED:
        task.completed_at = None
    task.save()
    form.save_m2m()
    if task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
        close_task_timers(task=task, actor=user)
    return task


@transaction.atomic
def soft_delete_task(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    close_task_timers(task=task, actor=user)
    task.delete(user=user)


@transaction.atomic
def add_comment(*, task, user, body):
    comment = TaskComment.objects.create(
        task=task,
        body=body.strip(),
        created_by=user,
        updated_by=user,
    )
    _activity(task, user, TaskActivity.EventType.COMMENT, "Comment added")
    return comment


@transaction.atomic
def add_checklist_item(*, task, user, title):
    last_position = TaskChecklistItem.objects.filter(task=task).aggregate(last=Max("position"))["last"]
    item = TaskChecklistItem.objects.create(
        task=task,
        title=title.strip(),
        position=(last_position or 0) + 1,
        created_by=user,
        updated_by=user,
    )
    _activity(task, user, TaskActivity.EventType.CHECKLIST, f"Checklist item added: {item.title}")
    return item


@transaction.atomic
def toggle_checklist_item(*, item, user):
    item.is_completed = not item.is_completed
    item.completed_at = timezone.now() if item.is_completed else None
    item.completed_by = user if item.is_completed else None
    item.updated_by = user
    item.save(
        update_fields=["is_completed", "completed_at", "completed_by", "updated_by", "updated_at"]
    )
    action = "completed" if item.is_completed else "reopened"
    progress = sync_checklist_progress(task=item.task, user=user)
    _activity(
        item.task,
        user,
        TaskActivity.EventType.CHECKLIST,
        f"Checklist item {action}: {item.title} ({progress}% complete)",
    )
    return item


def sync_checklist_progress(*, task, user):
    items = TaskChecklistItem.objects.filter(task=task)
    total = items.count()
    if not total:
        return task.progress
    completed = items.filter(is_completed=True).count()
    progress = round((completed / total) * 100)
    if task.progress != progress:
        task.progress = progress
        task.updated_by = user
        task._skip_tasker_activity = True
        task.save(update_fields=["progress", "updated_by", "updated_at"])
    return progress


@transaction.atomic
def log_time(*, task, user, started_at, duration_minutes, notes=""):
    duration = timedelta(minutes=duration_minutes)
    entry = TaskTimeEntry.objects.create(
        task=task,
        user=user,
        started_at=started_at,
        ended_at=started_at + duration,
        duration=duration,
        notes=notes.strip(),
        created_by=user,
        updated_by=user,
    )
    total_duration = TaskTimeEntry.objects.filter(task=task).aggregate(total=Sum("duration"))["total"]
    task.actual_duration = total_duration
    task.updated_by = user
    task._skip_tasker_activity = True
    task.save(update_fields=["actual_duration", "updated_by", "updated_at"])
    _activity(
        task,
        user,
        TaskActivity.EventType.TIME,
        f"Logged {duration_minutes} minute{'s' if duration_minutes != 1 else ''}",
        changes={"actual_duration": {"to": total_duration.total_seconds()}},
    )
    return entry


@transaction.atomic
def update_progress(*, task, user, progress):
    task = Task.objects.select_for_update().get(pk=task.pk)
    task.progress = progress
    task.updated_by = user
    if progress == 100 and task.status != Task.Status.COMPLETED:
        task.status = Task.Status.COMPLETED
        task.completed_at = timezone.now()
    elif progress < 100 and task.status == Task.Status.COMPLETED:
        task.status = Task.Status.IN_PROGRESS
        task.completed_at = None
    task.save(update_fields=["progress", "status", "completed_at", "updated_by", "updated_at"])
    if task.status == Task.Status.COMPLETED:
        close_task_timers(task=task, actor=user)
    return task


def _activity(task, user, event_type, description, changes=None):
    return TaskActivity.objects.create(
        task=task,
        event_type=event_type,
        description=description,
        changes=changes or {},
        created_by=user,
        updated_by=user,
    )
