import uuid

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST
from django.views.decorators.cache import never_cache

from tasker.policies import can_change_task
from tasker.models import Task, TaskChecklistItem
from tasker.services.notes import normalize_notes, notes_version
from tasker.services.focus import (
    ActiveTimerConflict,
    block_task,
    complete_focus,
    completion_definition_version,
    pause_focus,
    running_entry_for,
    save_focus_notes,
    start_focus,
    stop_focus,
    stop_own_focus,
    focus_interval_context,
)
from tasker.services.tasks import toggle_checklist_item
from tasker.views.tasks import _task_for_user
from tasker.organization import require_task_access
from tasker.policies import visible_tasks_for


def _timer_conflict(request, entry):
    if visible_tasks_for(request.user).filter(pk=entry.task_id).exists() and can_change_task(request.user, entry.task):
        messages.info(request, f'Your active timer is on “{entry.task.title}”.')
        return redirect("ims:tasker:focus_mode", slug=entry.task.slug)
    messages.info(request, "Another focus timer is active. Stop your own timer from Work before starting another task.")
    return redirect("ims:tasker:task_list")


def _mutable_task(request, slug):
    if not request.user.has_perm("tasker.view_task"):
        raise PermissionDenied
    task = _task_for_user(request.user, slug)
    if not can_change_task(request.user, task):
        raise PermissionDenied
    return task


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_GET
@never_cache
def focus_mode(request, slug):
    task = _mutable_task(request, slug)
    if task.status in (task.Status.COMPLETED, task.Status.CANCELLED) or task.archived:
        messages.info(request, "This task is not available for Focus Mode.")
        return redirect(task.get_absolute_url())
    running = running_entry_for(user=request.user)
    if running and running.task_id != task.pk:
        return _timer_conflict(request, running)
    from tasker.models import TaskContribution
    from tasker.services.goals import visible_initiatives_for
    from tasker.services.captures import visible_captures_for
    checklist = task.checklist_items.order_by("position", "pk")
    return render(
        request,
        "tasker/focus_mode.html",
        {
            "task": task,
            "notes_version": notes_version(task.notes),
            "definition_version": completion_definition_version(task),
            "running_entry": running if running and running.task_id == task.pk else None,
            "elapsed_seconds": int(task.actual_duration.total_seconds()) if task.actual_duration else 0,
            "checklist_items": list(checklist[:50]),
            "checklist_total": checklist.count(),
            "next_step": checklist.filter(is_completed=False).first(),
            "focus_interval": focus_interval_context(task=task, user=request.user, running=running),
            "parking_client_key": str(uuid.uuid4()),
            "source_capture": visible_captures_for(request.user).filter(promoted_task=task).first(),
            "primary_contribution": TaskContribution.objects.filter(task=task, initiative__in=visible_initiatives_for(request.user)).select_related("initiative__goal").first(),
        },
    )


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_start(request, slug):
    task = _mutable_task(request, slug)
    try:
        start_focus(task=task, user=request.user, interval_minutes=request.POST.get("interval_minutes"))
    except ActiveTimerConflict as exc:
        return _timer_conflict(request, exc.entry)
    except IntegrityError:
        running = running_entry_for(user=request.user)
        messages.info(request, "A focus timer was already started by another request.")
        return _timer_conflict(request, running) if running else redirect("ims:tasker:focus_mode", slug=task.slug)
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
        start_focus(task=task, user=request.user, resume_interval=True)
    except ActiveTimerConflict as exc:
        return _timer_conflict(request, exc.entry)
    except IntegrityError:
        running = running_entry_for(user=request.user)
        messages.info(request, "A focus timer was already resumed by another request.")
        return _timer_conflict(request, running) if running else redirect("ims:tasker:focus_mode", slug=task.slug)
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
    return redirect("ims:tasker:focus_wrap_up", slug=task.slug)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_complete(request, slug):
    task = _mutable_task(request, slug)
    try:
        complete_focus(task=task, user=request.user, accept_definition=request.POST.get("accept_definition") == "on",
                       definition_version=request.POST.get("definition_version", "") if task.definition_of_done.strip() else None)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("ims:tasker:focus_mode", slug=task.slug) if task.status not in (Task.Status.COMPLETED, Task.Status.CANCELLED) and not task.archived else redirect(task.get_absolute_url())
    messages.success(request, "Task completed. Excellent work.")
    return redirect("ims:tasker:focus_wrap_up", slug=task.slug)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def focus_stop_active(request):
    stop_own_focus(user=request.user)
    messages.success(request, "Your active focus timer is stopped. Recorded time was retained.")
    return redirect("ims:tasker:task_list")


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def focus_park(request, slug):
    content = request.POST.get("content", "")
    if not isinstance(content, str) or not content.strip() or len(content) > 10000:
        return JsonResponse({"saved": False, "message": "Write an idea of up to 10,000 characters. Your draft has not been saved."}, status=400)
    from tasker.services.captures import CaptureReplayError, create_capture
    with transaction.atomic():
        actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=request.user.pk, is_active=True).first()
        if actor is None:
            raise PermissionDenied
        task = get_object_or_404(Task.objects.select_for_update(), slug=slug)
        require_task_access(actor, task, capability="tasker.view_task")
        if task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
            return JsonResponse({"saved": False, "error": "focus_unavailable", "message": "This focus task is no longer active. Your idea is still in this page."}, status=409)
        try:
            capture = create_capture(user=actor, client_key=request.POST.get("client_key", ""), kind="IDEA",
                                     content=content, title=content.strip().splitlines()[0][:255])
        except CaptureReplayError:
            return JsonResponse({"saved": False, "message": "That submission was already used. Keep this draft and check your Inbox before starting another capture."}, status=409)
        except ValidationError:
            return JsonResponse({"saved": False, "message": "The idea was not saved. Keep the draft and retry from this page."}, status=400)
    return JsonResponse({"saved": True, "capture_url": capture.get_absolute_url(), "new_client_key": str(uuid.uuid4())})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def focus_wrap_up(request, slug):
    task = _task_for_user(request.user, slug)
    from tasker.services.operator import build_operator
    from tasker.services.operator_presentation import present_operator
    return render(request, "tasker/focus_wrap_up.html", {"task": task,
        "operator": present_operator(build_operator(user=request.user)),
        "task_can_change": can_change_task(request.user, task)})


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def focus_notes(request, slug):
    task = _mutable_task(request, slug)
    base_version = request.POST.get("base_version", "")
    if len(base_version) != 64 or any(char not in "0123456789abcdef" for char in base_version):
        return JsonResponse({"saved": False, "error": "notes_version_required"}, status=428)
    if "notes" not in request.POST:
        return JsonResponse({"saved": False, "error": "notes_required"}, status=400)
    submitted = normalize_notes(request.POST["notes"])
    if len(submitted) > 10000:
        return JsonResponse({"saved": False, "error": "notes_too_long"}, status=400)
    with transaction.atomic():
        task = get_object_or_404(Task.objects.select_for_update(), pk=task.pk)
        require_task_access(request.user, task, capability="tasker.view_task")
        current = normalize_notes(task.notes)
        current_version = notes_version(current)
        # Identical-content retry is safe even if the first response was lost.
        if submitted != current and base_version != current_version:
            return JsonResponse({
                "saved": False, "error": "notes_conflict", "notes": current,
                "version": current_version,
            }, status=409)
        if submitted != current:
            task = save_focus_notes(task=task, user=request.user, notes=submitted)
        return JsonResponse({"saved": True, "version": notes_version(task.notes)})


@login_required
@permission_required("tasker.change_taskchecklistitem", raise_exception=True)
@require_POST
def focus_checklist_toggle(request, slug, item_pk):
    task = _mutable_task(request, slug)
    item = get_object_or_404(TaskChecklistItem.objects.select_related("task"), pk=item_pk, task=task)
    toggle_checklist_item(item=item, user=request.user)
    return redirect("ims:tasker:focus_mode", slug=slug)
