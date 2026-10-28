"""Atomic Work triage with fresh authority and explicit stale-view rejection."""
from copy import copy
from datetime import date, datetime
import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounting.models import Company
from tasker.models import SavedWorkView, Task, TaskActivity, Team, WorkbenchPreference
from tasker.organization import require_task_access, validate_task_scope
from tasker.services.lifecycle import prepare_task_state, require_completion_acceptance
from tasker.workbench_models import WORK_LAYOUTS, WORK_PRESETS, normalize_work_filters


class WorkConflict(ValueError):
    pass


def work_version(task):
    """Opaque state proof also detects writes sharing one clock timestamp."""
    stamp = task.updated_at.isoformat() if task.updated_at else "unsaved"
    state = {field: Task._meta.get_field(field).to_python(getattr(task, field)) for field in (
        "title", "description", "definition_of_done", "notes", "owner_id", "company_id", "team_id",
        "assigned_by_id", "priority", "status", "due_date", "due_time", "progress", "archived",
        "blocked_reason", "estimated_duration", "actual_duration", "parent_task_id", "category_id",
        "colour", "carry_forward", "started_at", "completed_at", "is_deleted",
    )}
    payload = json.dumps([task.pk, stamp, state], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()


def _actor(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if current is None or not current.has_perm("tasker.view_task"):
        raise PermissionDenied
    return current


def _ids(values):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 50:
        raise ValidationError("Select between 1 and 50 tasks.")
    result = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (str, int)) or not str(value).isdecimal() or len(str(value)) > 18 or int(value) < 1:
            raise ValidationError("Choose valid task identifiers.")
        result.add(int(value))
    return sorted(result)


def _action_value(action, value):
    if action == "priority":
        if value not in Task.Priority.values:
            raise ValidationError("Choose a valid priority.")
    elif action == "due_date":
        if value in (None, ""):
            return None
        if isinstance(value, str):
            try:
                value = date.fromisoformat(value)
            except ValueError:
                raise ValidationError("Choose an ISO calendar date.") from None
        if not isinstance(value, date) or isinstance(value, datetime):
            raise ValidationError("Choose a calendar date.")
    elif action == "assign":
        try:
            if isinstance(value, bool) or not str(value).isdecimal() or len(str(value)) > 18 or int(value) < 1:
                raise ValueError
            value = get_user_model().objects.get(pk=int(value), is_active=True)
        except (ValueError, TypeError, get_user_model().DoesNotExist):
            raise ValidationError("Choose an active employee.") from None
    elif action == "block":
        if not isinstance(value, str) or not value.strip() or len(value) > 5000 or "\x00" in value:
            raise ValidationError("Record a blocker of up to 5,000 characters.")
        value = value.strip()
    elif action not in ("archive", "unarchive", "complete", "reopen"):
        raise ValidationError("Choose a supported Work action.")
    return value


@transaction.atomic
def apply_work_actions(*, user, task_ids, action, value=None, versions=None, accept_definition=False):
    """Validate the complete selection before making any audited writes."""
    user = _actor(user)
    ids = _ids(task_ids)
    value = _action_value(action, value)
    if not isinstance(versions, dict):
        raise WorkConflict("Reload Work before applying this change.")
    tasks = list(Task.all_objects.select_for_update().filter(pk__in=ids).order_by("pk"))
    if len(tasks) != len(ids):
        raise PermissionDenied
    # Acquire shared scope parents in a stable order before per-item rechecks.
    list(Company.objects.select_for_update().filter(pk__in={task.company_id for task in tasks if task.company_id}).order_by("pk"))
    list(Team.all_objects.select_for_update().filter(pk__in={task.team_id for task in tasks if task.team_id}).order_by("pk"))
    for task in tasks:
        require_task_access(user, task)
        expected = versions.get(str(task.pk), versions.get(task.pk))
        if not isinstance(expected, str) or expected != work_version(task):
            raise WorkConflict("One or more selected tasks changed. Reload and review them before applying this action.")
        if action == "complete":
            if task.archived or task.status == Task.Status.CANCELLED:
                raise ValidationError("Reopen or unarchive selected tasks before completing them.")
            require_completion_acceptance(task, accepted=accept_definition)
        if action == "block" and (task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED)):
            raise ValidationError("Reopen or unarchive selected tasks before blocking them.")
        if action == "assign":
            if not user.has_perm("tasker.assign_task"):
                raise PermissionDenied
            candidate = copy(task)
            candidate.owner = value
            validate_task_scope(user, candidate, previous=task, lock=True)
            if not candidate.company_id and candidate.owner_id != task.owner_id and task.subtasks.filter(is_deleted=False).exclude(owner_id=candidate.owner_id).exists():
                raise ValidationError("Move or unlink personal child tasks before changing their parent's owner.")
            candidate.full_clean()
    from tasker.services.focus import block_task, close_task_timers, complete_focus
    from tasker.services.tasks import sync_checklist_progress
    result = []
    for task in tasks:
        if action == "complete":
            task = complete_focus(task=task, user=user, accept_definition=accept_definition)
        elif action == "block":
            task = block_task(task=task, user=user, reason=value)
        else:
            previous_status = task.status
            if action == "priority":
                task.priority = value
            elif action == "due_date":
                task.due_date = value
                if value is None:
                    task.due_time = None
            elif action in ("archive", "unarchive"):
                task.archived = action == "archive"
            elif action == "assign":
                changed_owner = task.owner_id != value.pk
                task.owner = value
                task.assigned_by = user
                if changed_owner:
                    close_task_timers(task=task, actor=user)
            elif action == "reopen":
                task.status = Task.Status.PENDING
            task.updated_by = user
            prepare_task_state(task, previous_status=previous_status)
            task.save()
            if action == "archive" or (action == "assign" and changed_owner):
                from tasker.services.scheduling import invalidate_task_schedule
                invalidate_task_schedule(task=task,actor=user)
            if action == "archive":
                close_task_timers(task=task, actor=user)
            if action == "reopen":
                sync_checklist_progress(task=task, user=user)
                task.refresh_from_db()
        TaskActivity.objects.create(task=task, event_type=TaskActivity.EventType.UPDATED,
                                    description=f"Work action: {action.replace('_', ' ')}", changes={"work_action": action},
                                    created_by=user, updated_by=user)
        result.append(task)
    return result


@transaction.atomic
def save_work_view(*, user, name, filters, pk=None):
    user = _actor(user)
    # Serialize owner-local names without holding a stronger user key lock.
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    if not isinstance(name, str):
        raise ValidationError("Name this saved view.")
    view = SavedWorkView.objects.select_for_update().filter(owner=user, pk=pk).first() if pk else SavedWorkView(owner=user, created_by=user)
    if view is None:
        raise PermissionDenied
    view.name = name.strip()
    view.filters = normalize_work_filters(filters)
    view.updated_by = user
    view.save()
    return view


@transaction.atomic
def delete_work_view(*, user, pk):
    user = _actor(user)
    view = SavedWorkView.objects.select_for_update().filter(owner=user, pk=pk).first()
    if view is None:
        raise PermissionDenied
    view.delete(user=user)


@transaction.atomic
def save_work_preferences(*, user, preset, layout):
    user = _actor(user)
    if preset not in WORK_PRESETS or layout not in WORK_LAYOUTS:
        raise ValidationError("Choose a supported Work preset and layout.")
    get_user_model().objects.select_for_update(no_key=True).get(pk=user.pk)
    preference, _ = WorkbenchPreference.objects.get_or_create(owner=user)
    preference.preset, preference.layout = preset, layout
    preference.save()
    return preference
