from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from tasker.policies import can_change_task
from tasker.models import TaskChecklistItem
from tasker.services.focus import (
    ActiveTimerConflict,
    block_task,
    complete_focus,
    pause_focus,
    running_entry_for,
    save_focus_notes,
    start_focus,
    stop_focus,
)
from tasker.services.tasks import toggle_checklist_item
from tasker.views.tasks import _task_for_user


def _mutable_task(request, slug):
    task = _task_for_user(request.user, slug)
    if not can_change_task(request.user, task):
        raise PermissionDenied
    return task


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_GET
def focus_mode(request, slug):
    task = _mutable_task(request, slug)
    if task.status in (task.Status.COMPLETED, task.Status.CANCELLED) or task.archived:
        messages.info(request, "This task is not available for Focus Mode.")
        return redirect(task.get_absolute_url())
    running = running_entry_for(user=request.user)
    if running and running.task_id != task.pk:
        messages.info(request, f'Your active timer is on “{running.task.title}”.')
        return redirect("ims:tasker:focus_mode", slug=running.task.slug)
    return render(
        request,
        "tasker/focus_mode.html",
        {
            "task": task,
            "running_entry": running if running and running.task_id == task.pk else None,
            "elapsed_seconds": int(task.actual_duration.total_seconds()) if task.actual_duration else 0,
        },
    )


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_start(request, slug):
    task = _mutable_task(request, slug)
    try:
        start_focus(task=task, user=request.user)
    except ActiveTimerConflict as exc:
        messages.info(request, f'Your active timer is on “{exc.entry.task.title}”.')
        return redirect("ims:tasker:focus_mode", slug=exc.entry.task.slug)
    except IntegrityError:
        running = running_entry_for(user=request.user)
        messages.info(request, "A focus timer was already started by another request.")
        return redirect(
            "ims:tasker:focus_mode", slug=running.task.slug if running else task.slug
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(task.get_absolute_url())
    return redirect("ims:tasker:focus_mode", slug=task.slug)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_pause(request, slug):
    pause_focus(task=_mutable_task(request, slug), user=request.user)
    return redirect("ims:tasker:focus_mode", slug=slug)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_resume(request, slug):
    task = _mutable_task(request, slug)
    try:
        start_focus(task=task, user=request.user)
    except ActiveTimerConflict as exc:
        messages.error(request, f'Another timer is active on “{exc.entry.task.title}”.')
        return redirect("ims:tasker:focus_mode", slug=exc.entry.task.slug)
    except IntegrityError:
        running = running_entry_for(user=request.user)
        messages.info(request, "A focus timer was already resumed by another request.")
        return redirect(
            "ims:tasker:focus_mode", slug=running.task.slug if running else task.slug
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect(task.get_absolute_url())
    return redirect("ims:tasker:focus_mode", slug=slug)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_stop(request, slug):
    task = _mutable_task(request, slug)
    stop_focus(task=task, user=request.user)
    messages.success(request, "Focus session stopped and time saved.")
    return redirect(task.get_absolute_url())


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_block(request, slug):
    task = _mutable_task(request, slug)
    try:
        block_task(task=task, user=request.user, reason=request.POST.get("reason", ""))
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ims:tasker:focus_mode", slug=slug)
    messages.warning(request, "Task marked as blocked. The reason has been recorded.")
    return redirect("ims:tasker:index")


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_complete(request, slug):
    complete_focus(task=_mutable_task(request, slug), user=request.user)
    messages.success(request, "Task completed. Excellent work.")
    return redirect("ims:tasker:index")


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_notes(request, slug):
    task = _mutable_task(request, slug)
    save_focus_notes(task=task, user=request.user, notes=request.POST.get("notes", ""))
    return JsonResponse({"saved": True})


@login_required
@permission_required("tasker.change_taskchecklistitem", raise_exception=True)
@require_POST
def focus_checklist_toggle(request, slug, item_pk):
    task = _mutable_task(request, slug)
    item = get_object_or_404(TaskChecklistItem.objects.select_related("task"), pk=item_pk, task=task)
    toggle_checklist_item(item=item, user=request.user)
    return redirect("ims:tasker:focus_mode", slug=slug)
