from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
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
from tasker.ai.personal_assistant import personal_ai_availability


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
    tasks = visible_tasks_for(request.user)
    query = request.GET.get("q", "").strip()
    priority = request.GET.get("priority", "")
    status = request.GET.get("status", "")
    category = request.GET.get("category", "")
    owner = request.GET.get("owner", "")
    sort = request.GET.get("sort", "due")

    if query:
        tasks = tasks.filter(
            Q(title__icontains=query)
            | Q(description__icontains=query)
            | Q(notes__icontains=query)
        )
    if priority in Task.Priority.values:
        tasks = tasks.filter(priority=priority)
    if status in Task.Status.values:
        tasks = tasks.filter(status=status)
    if category.isdigit():
        tasks = tasks.filter(category_id=int(category))
    can_view_all = request.user.has_perm("tasker.view_all_tasks") or request.user.has_perm(
        "tasker.manage_tasker"
    )
    if owner.isdigit() and can_view_all:
        tasks = tasks.filter(owner_id=int(owner))
    if request.GET.get("archived") != "1":
        tasks = tasks.filter(archived=False)

    if sort == "priority":
        tasks = tasks.annotate(priority_rank=priority_rank_expression()).order_by(
            "priority_rank", F("due_date").asc(nulls_last=True)
        )
    else:
        tasks = tasks.order_by(*SORT_OPTIONS.get(sort, SORT_OPTIONS["due"]))

    page_obj = Paginator(tasks, 12).get_page(request.GET.get("page"))
    owners = (
        get_user_model().objects.filter(is_active=True).order_by(
            "first_name", "last_name", "username"
        )
        if can_view_all
        else get_user_model().objects.none()
    )
    return render(
        request,
        "tasker/task_list.html",
        {
            "page_obj": page_obj,
            "categories": TaskCategory.objects.filter(is_active=True),
            "owners": owners,
            "priority_choices": Task.Priority.choices,
            "status_choices": Task.Status.choices,
            "filters": {
                "q": query,
                "priority": priority,
                "status": status,
                "category": category,
                "owner": owner,
                "sort": sort,
                "archived": request.GET.get("archived", ""),
            },
            "can_view_all": can_view_all,
            "today": timezone.localdate(),
        },
    )


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
    time_entries = task.time_entries.select_related("user")[:8]
    activities = TaskActivity.objects.filter(task=task).select_related("created_by")[:30]
    return render(
        request,
        "tasker/task_detail.html",
        {
            "task": task,
            "time_entries": time_entries,
            "activities": activities,
            "comment_form": TaskCommentForm(),
            "checklist_form": ChecklistItemForm(),
            "time_form": TimeEntryForm(),
            "progress_form": TaskProgressForm(initial={"progress": task.progress}),
            "can_change": can_change_task(request.user, task),
            "can_delete": can_delete_task(request.user, task),
            "personal_ai_allowed": task.owner_id == request.user.pk,
            "ai_availability": personal_ai_availability(request.user),
        },
    )


@login_required
@permission_required("tasker.add_task", raise_exception=True)
def task_create(request):
    form = TaskForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        task = save_task(form=form, user=request.user)
        messages.success(request, "Task created successfully.")
        return redirect("ims:tasker:task_detail", slug=task.slug)
    return render(request, "tasker/task_form.html", {"form": form, "page_title": "Create task"})


@login_required
@permission_required("tasker.change_task", raise_exception=True)
def task_edit(request, slug):
    task = _task_for_user(request.user, slug)
    _require_change(request.user, task)
    form = TaskForm(request.POST or None, instance=task, user=request.user)
    if request.method == "POST" and form.is_valid():
        task = save_task(form=form, user=request.user)
        messages.success(request, "Task updated successfully.")
        return redirect("ims:tasker:task_detail", slug=task.slug)
    return render(
        request,
        "tasker/task_form.html",
        {"form": form, "task": task, "page_title": "Edit task"},
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
        update_progress(task=task, user=request.user, progress=form.cleaned_data["progress"])
        messages.success(request, "Progress updated.")
    else:
        messages.error(request, "Progress must be between 0 and 100.")
    return redirect(task.get_absolute_url())


def _task_for_user(user, slug):
    return get_object_or_404(visible_tasks_for(user), slug=slug)


def _require_change(user, task):
    if not can_change_task(user, task):
        raise PermissionDenied
