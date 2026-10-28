from __future__ import annotations

from datetime import timedelta
from copy import copy

from django.db import transaction
from django.core.exceptions import PermissionDenied, ValidationError
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
from tasker.services.lifecycle import prepare_task_state, require_completion_acceptance, requires_timer_cleanup, save_without_task_activity
from tasker.policies import can_change_task
from tasker.services.notes import NotesConflictError, normalize_notes, notes_version
from tasker.organization import require_task_access, validate_task_scope
from tasker.services.workbench import WorkConflict, work_version


@transaction.atomic
def save_task(*, form, user):
    task = form.save(commit=False)
    is_new = task.pk is None
    previous_status = None
    current = None
    if not is_new:
        current = Task.all_objects.select_for_update().get(pk=task.pk)
        if current.is_deleted or not can_change_task(user, current):
            raise PermissionDenied
        expected = form.cleaned_data.get("work_version")
        if (expected and expected != work_version(current)) or (current.definition_of_done.strip() and not expected):
            raise ValidationError("This task changed or its review proof is missing. Reload the task, compare your draft, and review its current definition of done before saving.")
        if not getattr(form, "definition_submitted", False):
            task.definition_of_done = current.definition_of_done
        previous_status = current.status
        task.notes = normalize_notes(task.notes)
        if task.notes != normalize_notes(current.notes) and form.cleaned_data.get("notes_version") != notes_version(current.notes):
            raise NotesConflictError(current.notes)

    if is_new:
        if not user.has_perm("tasker.add_task"):
            raise PermissionDenied
        task.assigned_by = user
        task.created_by = user
    validate_task_scope(user, task, previous=current, lock=True)
    if current is not None:
        require_task_access(user, current)
    if task.status == Task.Status.COMPLETED and (current is None or current.status != Task.Status.COMPLETED or current.definition_of_done != task.definition_of_done):
        review = copy(task)
        review.status = Task.Status.PENDING
        if not review.definition_of_done.strip() and current is not None:
            review.definition_of_done = current.definition_of_done
        try:
            require_completion_acceptance(review, accepted=form.cleaned_data.get("accept_definition", False))
        except ValueError as error:
            raise ValidationError(str(error)) from error
    reassigned = current is not None and current.owner_id != task.owner_id
    if reassigned:
        task.assigned_by = user
    task.updated_by = user
    prepare_task_state(task, previous_status=previous_status)
    task.save()
    form.save_m2m()
    if current is not None and (reassigned or current.team_id != task.team_id or current.company_id != task.company_id or task.archived or task.status in (Task.Status.COMPLETED,Task.Status.CANCELLED)):
        from tasker.services.scheduling import invalidate_task_schedule
        invalidate_task_schedule(task=task,actor=user)
    if reassigned or requires_timer_cleanup(task):
        close_task_timers(task=task, actor=user)
    if current is not None and (reassigned or current.team_id != task.team_id or current.company_id != task.company_id):
        from tasker.models import OrganizationEvent
        OrganizationEvent.objects.create(
            company=task.company, team=task.team, actor=user,
            action="task.scope_changed", object_type="Task", object_id=str(task.pk),
            details={"owner_from": current.owner_id, "owner_to": task.owner_id,
                     "company_from": current.company_id, "company_to": task.company_id,
                     "team_from": current.team_id, "team_to": task.team_id},
        )
    return task


@transaction.atomic
def soft_delete_task(*, task, user):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task)
    close_task_timers(task=task, actor=user)
    from tasker.services.scheduling import invalidate_task_schedule
    invalidate_task_schedule(task=task,actor=user,reason="Task deleted")
    task.delete(user=user)


@transaction.atomic
def add_comment(*, task, user, body):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, change=False, capability="tasker.add_taskcomment")
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
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.add_taskchecklistitem")
    last_position = TaskChecklistItem.objects.filter(task=task).aggregate(last=Max("position"))["last"]
    item = TaskChecklistItem.objects.create(
        task=task,
        title=title.strip(),
        position=(last_position or 0) + 1,
        created_by=user,
        updated_by=user,
    )
    _activity(task, user, TaskActivity.EventType.CHECKLIST, f"Checklist item added: {item.title}")
    sync_checklist_progress(task=task, user=user)
    return item


@transaction.atomic
def toggle_checklist_item(*, item, user):
    task = Task.objects.select_for_update().get(pk=item.task_id)
    require_task_access(user, task, capability="tasker.change_taskchecklistitem")
    item = TaskChecklistItem.objects.select_for_update().get(pk=item.pk, task=task)
    item.task = task
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


@transaction.atomic
def sync_checklist_progress(*, task, user):
    """Checklist progress never accepts/reopens an outcome on the user's behalf."""
    task = Task.objects.select_for_update().get(pk=task.pk)
    items = TaskChecklistItem.objects.filter(task=task)
    total = items.count()
    completed = items.filter(is_completed=True).count()
    progress = 100 if task.status == Task.Status.COMPLETED else (round((completed / total) * 100) if total else 0)
    if task.progress != progress:
        task.progress = progress
        task.updated_by = user
        save_without_task_activity(task, update_fields=["progress", "updated_by", "updated_at"])
    return progress


@transaction.atomic
def log_time(*, task, user, started_at, duration_minutes, notes=""):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task, capability="tasker.add_tasktimeentry")
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
    save_without_task_activity(task, update_fields=["actual_duration", "updated_by", "updated_at"])
    _activity(
        task,
        user,
        TaskActivity.EventType.TIME,
        f"Logged {duration_minutes} minute{'s' if duration_minutes != 1 else ''}",
        changes={"actual_duration": {"to": total_duration.total_seconds()}},
    )
    return entry


@transaction.atomic
def update_progress(*, task, user, progress, accept_definition=False, expected_version=None):
    task = Task.objects.select_for_update().get(pk=task.pk)
    require_task_access(user, task)
    if (expected_version and expected_version != work_version(task)) or (task.definition_of_done.strip() and not expected_version):
        raise WorkConflict("This task changed or its review proof is missing. Reload and review the current definition of done before updating progress.")
    if task.archived or task.status == Task.Status.CANCELLED:
        raise ValueError("Reopen or unarchive this task before updating its progress.")
    previous_status = task.status
    if progress == 100:
        require_completion_acceptance(task, accepted=accept_definition)
    task.progress = progress
    task.updated_by = user
    if progress == 100 and task.status != Task.Status.COMPLETED:
        task.status = Task.Status.COMPLETED
        task.completed_at = timezone.now()
    elif progress < 100 and task.status == Task.Status.COMPLETED:
        task.status = Task.Status.IN_PROGRESS
        task.completed_at = None
    prepare_task_state(task, previous_status=previous_status)
    task.save(update_fields=["progress", "status", "started_at", "completed_at", "blocked_reason", "updated_by", "updated_at"])
    if requires_timer_cleanup(task):
        close_task_timers(task=task, actor=user)
        if task.status in (Task.Status.COMPLETED,Task.Status.CANCELLED) or task.archived:
            from tasker.services.scheduling import invalidate_task_schedule
            invalidate_task_schedule(task=task,actor=user)
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
