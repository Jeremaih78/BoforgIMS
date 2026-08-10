from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_GET, require_POST

from tasker.ai.exceptions import AIServiceError
from tasker.ai.personal_assistant import (
    PersonalProductivityAssistant,
    apply_suggested_steps,
    personal_ai_availability,
)
from tasker.models import AIConversation, AIMessage, Task


DAY_FEATURES = {
    "plan-day": ("plan_day", "Plan My Day"),
    "next-task": ("next_task", "Suggested Next Task"),
    "prioritize": ("prioritize", "Task Prioritization"),
    "summarize-day": ("summarize_day", "Day Summary"),
}


@login_required
@permission_required("tasker.use_ai", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
def assistant_home(request):
    conversations = AIConversation.objects.filter(
        owner=request.user, agent_type=PersonalProductivityAssistant.agent_type
    )[:8]
    return render(request, "tasker/assistant_home.html", {
        "ai_availability": personal_ai_availability(request.user),
        "conversations": conversations,
    })


@login_required
@permission_required("tasker.use_ai", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
def assistant_day_action(request, action):
    if action not in DAY_FEATURES:
        raise Http404
    feature, title = DAY_FEATURES[action]
    if feature == "next_task" and not Task.objects.filter(
        owner=request.user,
        archived=False,
        status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED),
    ).exists():
        messages.info(request, "You have no active personal tasks to recommend.")
        return redirect("ims:tasker:index")
    return _run(request, feature=feature, title=title)


@login_required
@permission_required("tasker.use_ai", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
def assistant_task_action(request, slug, action):
    if action not in {"break-down", "explain"}:
        raise Http404
    task = get_object_or_404(Task.objects.select_related("category"), owner=request.user, slug=slug)
    if action == "break-down" and (
        task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED)
    ):
        messages.warning(request, "Only active tasks can be broken into new steps.")
        return redirect("ims:tasker:task_detail", slug=task.slug)
    feature = "break_task" if action == "break-down" else "explain_task"
    title = "Suggested Task Steps" if feature == "break_task" else "Task Explanation"
    return _run(request, feature=feature, title=title, task=task)


def _run(request, *, feature, title, task=None):
    try:
        payload, message = PersonalProductivityAssistant().run(
            user=request.user, feature=feature, task=task
        )
    except AIServiceError as exc:
        messages.error(request, exc.user_message)
        if task:
            return redirect("ims:tasker:task_detail", slug=task.slug)
        return redirect("ims:tasker:index")
    return render(request, "tasker/assistant_result.html", {
        "feature": feature,
        "page_title": title,
        "result": payload,
        "ai_message": message,
        "task": task,
    })


@login_required
@permission_required("tasker.use_ai", raise_exception=True)
@permission_required("tasker.add_taskchecklistitem", raise_exception=True)
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def assistant_apply_steps(request, slug, message_pk):
    task = get_object_or_404(Task, owner=request.user, slug=slug)
    try:
        indexes = [int(value) for value in request.POST.getlist("steps")]
    except ValueError as exc:
        raise PermissionDenied from exc
    try:
        count = apply_suggested_steps(
            user=request.user, task=task, message_id=message_pk, selected_indexes=indexes
        )
    except (AIServiceError, AIMessage.DoesNotExist, PermissionError):
        raise PermissionDenied
    if count:
        messages.success(request, f"Added {count} suggested step{'s' if count != 1 else ''} to the checklist.")
    else:
        messages.info(request, "No new checklist steps were added.")
    return redirect("ims:tasker:task_detail", slug=task.slug)
