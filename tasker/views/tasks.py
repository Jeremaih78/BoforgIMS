from django.contrib import messages
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Prefetch, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST, require_http_methods

from tasker.forms import (
    ChecklistItemForm,
    TaskCommentForm,
    TaskForm,
    TaskProgressForm,
    TimeEntryForm,
)
from tasker.models import Task, TaskActivity, TaskCategory, TaskChecklistItem, TaskComment
from tasker.policies import can_change_task, can_delete_task, visible_tasks_for
from tasker.selectors import priority_rank_expression
from tasker.services.tasks import (
    add_checklist_item,
    add_comment,
    log_time,
    save_task,
    soft_delete_task,
    toggle_checklist_item,
    update_progress,
)
from tasker.services.notes import NotesConflictError
from tasker.services.workbench import work_version
from tasker.ai.personal_assistant import personal_ai_availability
from tasker.organization import company_read_ids, managed_teams_for, visible_companies_for, visible_teams_for


SORT_OPTIONS = {
    "due": (F("due_date").asc(nulls_last=True), F("due_time").asc(nulls_last=True), "title"),
    "newest": ("-created_at",),
    "oldest": ("created_at",),
    "title": ("title",),
    "updated": ("-updated_at",),
}


@login_required
@permission_required("tasker.view_task", raise_exception=True)
def task_list(request):
    from tasker.views.workbench import work_list
    return work_list(request)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
def task_detail(request, slug):
    task = get_object_or_404(
        visible_tasks_for(request.user).prefetch_related(
            Prefetch(
                "checklist_items",
                queryset=TaskChecklistItem.objects.select_related("completed_by"),
            ),
            Prefetch("comments", queryset=TaskComment.objects.select_related("created_by")),
        ),
        slug=slug,
    )
    from tasker.services.captures import visible_captures_for
    source_capture = visible_captures_for(request.user).filter(promoted_task=task).first()
    time_entries = task.time_entries.select_related("user")[:8]
    activities = TaskActivity.objects.filter(task=task).select_related("created_by")[:30]
    from tasker.models import TaskContribution
    from tasker.services.goals import visible_initiatives_for
    primary_contribution = TaskContribution.objects.filter(
        task=task, initiative__in=visible_initiatives_for(request.user),
    ).select_related("initiative__goal").first()
    from tasker.services.dependencies import readiness_for
    from tasker.services.effort import effort_for_task
    coordination_requests = []
    if getattr(settings, "TASKER_COORDINATION_ENABLED", False):
        from tasker.services.coordination import visible_requests_for
        coordination_requests = visible_requests_for(request.user).filter(task=task).select_related("resolver").order_by("-raised_at", "-pk")[:10]
    return render(
        request,
        "tasker/task_detail.html",
        {
            "task": task,
            "readiness":readiness_for(task=task,user=request.user),
            "remaining_effort":effort_for_task(task=task),
            "coordination_requests": coordination_requests,
            "primary_contribution": primary_contribution,
            "source_capture": source_capture,
            "time_entries": time_entries,
            "activities": activities,
            "comment_form": TaskCommentForm(),
            "checklist_form": ChecklistItemForm(),
            "time_form": TimeEntryForm(),
            "progress_form": TaskProgressForm(initial={"progress": task.progress, "work_version": work_version(task)}),
            "can_change": can_change_task(request.user, task),
            "can_delete": can_delete_task(request.user, task),
            "personal_ai_allowed": task.owner_id == request.user.pk,
            "ai_availability": personal_ai_availability(request.user),
        },
    )


@login_required
@permission_required("tasker.add_task", raise_exception=True)
def task_create(request):
    instance = Task()
    if request.method == "GET":
        if str(request.GET.get("team", "")).isdigit():
            team = get_object_or_404(visible_teams_for(request.user), pk=request.GET["team"])
            instance.team = team
            instance.company = team.company
        elif str(request.GET.get("company", "")).isdigit():
            instance.company = get_object_or_404(visible_companies_for(request.user), pk=request.GET["company"])
    form = TaskForm(request.POST or None, instance=instance, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            task = save_task(form=form, user=request.user)
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Task created successfully.")
            return redirect("ims:tasker:task_detail", slug=task.slug)
    return render(request, "tasker/task_form.html", {"form": form, "page_title": "Create task"})


@login_required
@permission_required("tasker.change_task", raise_exception=True)
def task_edit(request, slug):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    form = TaskForm(request.POST or None, instance=task, user=request.user)
    notes_conflict = None
    if request.method == "POST" and form.is_valid():
        try:
            task = save_task(form=form, user=request.user)
        except NotesConflictError as exc:
            data = form.data.copy()
            data["notes_version"] = exc.version
            form.data = data
            form.add_error("notes", "These notes changed in another tab. Review the latest saved text below, merge your edits, and save again.")
            notes_conflict = exc.notes
        except ValidationError as exc:
            form.add_error(None, "; ".join(exc.messages))
        else:
            messages.success(request, "Task updated successfully.")
            return redirect("ims:tasker:task_detail", slug=task.slug)
    return render(
        request,
        "tasker/task_form.html",
        {"form": form, "task": task, "page_title": "Edit task", "notes_conflict": notes_conflict},
    )


@login_required
@permission_required("tasker.delete_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
def task_delete(request, slug):
    task = _task_for_user(request.user, slug)
    if not can_delete_task(request.user, task):
        raise PermissionDenied
    if request.method == "POST":
        soft_delete_task(task=task, user=request.user)
        messages.success(request, "Task moved to deleted items.")
        return redirect("ims:tasker:task_list")
    return render(request, "tasker/task_confirm_delete.html", {"task": task})


@login_required
@permission_required("tasker.add_taskcomment", raise_exception=True)
@require_POST
def comment_add(request, slug):
    task = _task_for_user(request.user, slug)
    form = TaskCommentForm(request.POST)
    if form.is_valid():
        add_comment(task=task, user=request.user, body=form.cleaned_data["body"])
        messages.success(request, "Comment added.")
    else:
        messages.error(request, "Enter a comment before posting.")
    return redirect(f"{task.get_absolute_url()}#comments")


@login_required
@permission_required("tasker.add_taskchecklistitem", raise_exception=True)
@require_POST
def checklist_add(request, slug):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    form = ChecklistItemForm(request.POST)
    if form.is_valid():
        add_checklist_item(task=task, user=request.user, title=form.cleaned_data["title"])
        messages.success(request, "Checklist item added.")
    else:
        messages.error(request, "Enter a checklist item.")
    return redirect(f"{task.get_absolute_url()}#checklist")


@login_required
@permission_required("tasker.change_taskchecklistitem", raise_exception=True)
@require_POST
def checklist_toggle(request, slug, item_pk):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    item = get_object_or_404(
        TaskChecklistItem.objects.select_related("task"), pk=item_pk, task=task
    )
    toggle_checklist_item(item=item, user=request.user)
    return redirect(f"{task.get_absolute_url()}#checklist")


@login_required
@permission_required("tasker.add_tasktimeentry", raise_exception=True)
@require_POST
def time_add(request, slug):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    form = TimeEntryForm(request.POST)
    if form.is_valid():
        log_time(
            task=task,
            user=request.user,
            started_at=form.cleaned_data["started_at"],
            duration_minutes=form.cleaned_data["duration_minutes"],
            notes=form.cleaned_data["notes"],
        )
        messages.success(request, "Time entry added.")
    else:
        messages.error(request, "Check the time entry and try again.")
    return redirect(f"{task.get_absolute_url()}#time-entries")


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@require_POST
def progress_update(request, slug):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    form = TaskProgressForm(request.POST)
    if form.is_valid():
        try:
            update_progress(task=task, user=request.user, progress=form.cleaned_data["progress"],
                            accept_definition=form.cleaned_data["accept_definition"], expected_version=form.cleaned_data.get("work_version"))
        except ValueError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Progress updated.")
    else:
        messages.error(request, "Progress must be between 0 and 100.")
    return redirect(task.get_absolute_url())


def _task_for_user(user, slug):
    return get_object_or_404(visible_tasks_for(user), slug=slug)


def _require_change(user, task):
    if not can_change_task(user, task):
        raise PermissionDenied
