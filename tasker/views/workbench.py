"""Bounded Work views and explicit, versioned triage actions."""
import json
from datetime import timedelta
from urllib.parse import urlsplit

from django.contrib import messages
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, F, OuterRef, Q, Subquery
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from tasker.models import OrganizationAccess, SavedWorkView, Task, TaskCategory, TaskChecklistItem, TaskContribution, WorkbenchPreference
from tasker.organization import company_read_ids, managed_teams_for
from tasker.policies import can_change_task, visible_tasks_for
from tasker.selectors import priority_rank_expression
from tasker.services.captures import visible_captures_for
from tasker.services.focus import running_entry_for
from tasker.services.goals import visible_initiatives_for
from tasker.services.workbench import (
    WorkConflict, apply_work_actions, delete_work_view, save_work_preferences, save_work_view, work_version,
)
from tasker.workbench_models import FILTER_KEYS, normalize_work_filters


PRESET_LABELS = (("all", "All work"), ("mine", "Mine"), ("waiting", "Waiting"), ("duesoon", "Due soon"), ("unscheduled", "Unscheduled"))


def _return_url(request):
    fallback = reverse("ims:tasker:task_list")
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=request.is_secure()) and urlsplit(target).path == fallback:
        return target
    return fallback


def _error(request, message, *, status=400):
    return render(request, "tasker/work/error.html", {"work_error": message, "back_url": _return_url(request)}, status=status)


def _settings(request):
    preference = WorkbenchPreference.objects.filter(owner=request.user).first()
    current = {"preset": preference.preset if preference else "all", "layout": preference.layout if preference else "list", "sort": "due"}
    saved = None
    if request.GET.get("saved"):
        raw = request.GET["saved"]
        if not raw.isdecimal() or len(raw) > 18 or int(raw) < 1:
            raise Http404
        try:
            pk = int(raw)
        except (ValueError, TypeError):
            raise Http404
        saved = get_object_or_404(SavedWorkView.objects, owner=request.user, pk=pk)
        current.update(normalize_work_filters(saved.filters))
    current.update({name: request.GET[name] for name in FILTER_KEYS if name in request.GET})
    return normalize_work_filters(current), saved


def _manage_scopes(user):
    teams = set(managed_teams_for(user).values_list("pk", flat=True))
    companies = set()
    if user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker"):
        companies = set(OrganizationAccess.objects.filter(user=user, is_active=True, company__is_active=True, can_manage=True).values_list("company_id", flat=True))
    return teams, companies


def _sources(user, tasks):
    ids = [task.pk for task in tasks]
    captures = {row["promoted_task_id"]: row for row in visible_captures_for(user).filter(promoted_task_id__in=ids).values("pk", "promoted_task_id", "visibility")}
    contributions = {item.task_id: item for item in TaskContribution.objects.filter(task_id__in=ids, initiative__in=visible_initiatives_for(user)).select_related("initiative__goal")} if getattr(settings, "TASKER_DIRECTION_ENABLED", False) else {}
    for task in tasks:
        capture = captures.get(task.pk)
        task.work_source_url = reverse("ims:tasker:capture_detail", kwargs={"pk": capture["pk"]}) if capture else ""
        task.work_contribution = contributions.get(task.pk)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def work_list(request):
    try:
        filters, saved = _settings(request)
    except ValidationError as error:
        return _error(request, " ".join(error.messages))
    tasks = visible_tasks_for(request.user)
    preset, layout = filters.get("preset", "all"), filters.get("layout", "list")
    today = timezone.localdate()
    active = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
    if preset == "mine":
        tasks = tasks.filter(owner=request.user)
    elif preset == "waiting":
        tasks = tasks.filter(status=Task.Status.BLOCKED)
    elif preset == "duesoon":
        tasks = tasks.filter(status__in=active, due_date__lte=today + timedelta(days=7))
    elif preset == "unscheduled":
        tasks = tasks.filter(status__in=active, due_date__isnull=True)
    query = filters.get("q", "")
    if query:
        tasks = tasks.filter(Q(title__icontains=query) | Q(description__icontains=query) | Q(notes__icontains=query) | Q(definition_of_done__icontains=query))
    for field in ("priority", "status"):
        if filters.get(field):
            tasks = tasks.filter(**{field: filters[field]})
    for field in ("category", "owner"):
        if filters.get(field):
            tasks = tasks.filter(**{field + "_id": int(filters[field])})
    if filters.get("archived") != "1":
        tasks = tasks.filter(archived=False)
    next_step = TaskChecklistItem.objects.filter(task_id=OuterRef("pk"), is_completed=False).order_by("position", "pk").values("title")[:1]
    tasks = tasks.annotate(work_next_step=Subquery(next_step),
                           work_step_total=Count("checklist_items", filter=Q(checklist_items__is_deleted=False)),
                           work_step_done=Count("checklist_items", filter=Q(checklist_items__is_deleted=False, checklist_items__is_completed=True)))
    sort = filters.get("sort", "due")
    sorts = {"due": (F("due_date").asc(nulls_last=True), F("due_time").asc(nulls_last=True), "title", "pk"),
             "newest": ("-created_at", "-pk"), "oldest": ("created_at", "pk"), "updated": ("-updated_at", "-pk"), "title": ("title", "pk")}
    if sort == "priority":
        tasks = tasks.annotate(work_priority=priority_rank_expression()).order_by("work_priority", F("due_date").asc(nulls_last=True), "pk")
    else:
        tasks = tasks.order_by(*sorts[sort])
    page = Paginator(tasks, 24).get_page(request.GET.get("page"))
    shown = list(page.object_list)
    page.object_list = shown
    teams, companies = _manage_scopes(request.user)
    from tasker.services.dependencies import readiness_for_tasks
    readiness = readiness_for_tasks(tasks=shown,user=request.user)
    change = request.user.has_perm("tasker.change_task")
    for task in shown:
        task.work_editable = change and (request.user.is_superuser or task.owner_id == request.user.pk or task.team_id in teams or task.company_id in companies)
        task.work_startable = task.work_editable and not task.archived and task.status in (Task.Status.PENDING, Task.Status.IN_PROGRESS)
        task.work_readiness = readiness[task.pk]
        task.work_startable = task.work_startable and task.work_readiness.ready
        task.work_version = work_version(task)
        task.work_versions_json = json.dumps({str(task.pk): task.work_version})
    _sources(request.user, shown)
    columns = [{"value": value, "label": label, "tasks": [task for task in shown if task.status == value]} for value, label in Task.Status.choices]
    owner_ids = visible_tasks_for(request.user).values("owner_id")
    owners = get_user_model().objects.filter(pk__in=owner_ids, is_active=True).order_by("first_name", "last_name", "username")[:100]
    assignees = get_user_model().objects.filter(is_active=True).filter(
        Q(pk=request.user.pk)
        | Q(tasker_team_memberships__team_id__in=teams, tasker_team_memberships__is_active=True, tasker_team_memberships__is_deleted=False)
        | Q(tasker_team_memberships__team__company_id__in=companies, tasker_team_memberships__team__is_deleted=False, tasker_team_memberships__is_active=True, tasker_team_memberships__is_deleted=False)
        | Q(tasker_organization_access__company_id__in=companies, tasker_organization_access__is_active=True, tasker_organization_access__is_deleted=False)
    ).distinct().order_by("first_name", "last_name", "username")[:100]
    running = running_entry_for(user=request.user)
    running_visible = bool(running and visible_tasks_for(request.user).filter(pk=running.task_id).exists() and can_change_task(request.user, running.task))
    return render(request, "tasker/task_list.html", {
        "page_obj": page, "columns": columns, "filters": filters, "layout": layout, "preset": preset,
        "presets": PRESET_LABELS, "saved_views": SavedWorkView.objects.filter(owner=request.user)[:30], "selected_saved": saved,
        "categories": TaskCategory.objects.filter(is_active=True), "owners": owners, "assignees": assignees,
        "priority_choices": Task.Priority.choices, "status_choices": Task.Status.choices, "can_view_all": request.user.is_superuser or bool(teams or companies or company_read_ids(request.user).exists()),
        "versions_json": json.dumps({str(task.pk): task.work_version for task in shown}), "filters_json": json.dumps(filters),
        "running": running if running_visible else None, "has_running": bool(running), "today": today,
    })


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def task_preview(request, slug):
    task = get_object_or_404(visible_tasks_for(request.user), slug=slug)
    _sources(request.user, [task])
    steps = task.checklist_items.order_by("position", "pk")
    editable = can_change_task(request.user, task)
    goal = task.work_contribution.initiative.goal if task.work_contribution else None
    return JsonResponse({"title": task.title, "description": task.description[:6000], "definition_of_done": task.definition_of_done,
        "priority": task.get_priority_display(), "status": task.get_status_display(), "progress": task.progress,
        "accepted": task.status == Task.Status.COMPLETED, "owner": task.owner.get_full_name() or task.owner.username,
        "due": task.due_date.isoformat() if task.due_date else None, "blocked_reason": task.blocked_reason,
        "url": task.get_absolute_url(), "edit_url": reverse("ims:tasker:task_edit", kwargs={"slug": task.slug}) if editable else None,
        "start_url": reverse("ims:tasker:focus_start", kwargs={"slug": task.slug}) if editable and not task.archived and task.status in (Task.Status.PENDING, Task.Status.IN_PROGRESS) else None,
        "source_url": task.work_source_url or None, "goal": goal.title if goal else None, "goal_url": goal.get_absolute_url() if goal else None,
        "checklist": list(steps.values("title", "is_completed")[:12]), "checklist_total": steps.count(), "version": work_version(task)})


def _ids(request, slug=None):
    if slug:
        task = get_object_or_404(visible_tasks_for(request.user), slug=slug)
        return [task.pk]
    raw = request.POST.getlist("task_ids")
    if not 1 <= len(raw) <= 50 or any(not value.isdecimal() or len(value) > 18 or int(value) < 1 for value in raw):
        raise ValidationError("Select between 1 and 50 valid tasks.")
    return sorted(set(map(int, raw)))


def _apply(request, *, slug=None, action=None):
    try:
        ids = _ids(request, slug)
        action = action or request.POST.get("action", "")
        accepted = request.POST.get("accept_definition") == "on"
        if action == "complete" and not accepted:
            tasks = list(visible_tasks_for(request.user).filter(pk__in=ids).order_by("pk"))
            if len(tasks) != len(ids) or any(not can_change_task(request.user, task) for task in tasks):
                raise PermissionDenied
            if any(task.archived or task.status == Task.Status.CANCELLED for task in tasks):
                raise ValidationError("Reopen or unarchive the selection before accepting it.")
            return render(request, "tasker/work/confirm_complete.html", {"tasks": tasks, "versions_json": json.dumps({str(task.pk): work_version(task) for task in tasks}), "back_url": _return_url(request)})
        versions = json.loads(request.POST.get("versions", "{}"))
        apply_work_actions(user=request.user, task_ids=ids, action=action, value=request.POST.get("value"), versions=versions, accept_definition=accepted)
    except WorkConflict as error:
        return _error(request, str(error), status=409)
    except (ValidationError, ValueError, TypeError) as error:
        return _error(request, " ".join(error.messages) if isinstance(error, ValidationError) else "Check the action and selected values. No tasks were changed.")
    messages.success(request, "Work updated. All selected changes were saved.")
    return redirect(_return_url(request))


@login_required
@permission_required(("tasker.view_task", "tasker.change_task"), raise_exception=True)
@never_cache
@require_POST
def task_work_action(request, slug, action):
    return _apply(request, slug=slug, action=action)


@login_required
@permission_required(("tasker.view_task", "tasker.change_task"), raise_exception=True)
@never_cache
@require_POST
def task_bulk_action(request):
    return _apply(request)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def work_view_save(request):
    try:
        raw = request.POST.get("pk", "")
        if raw and (not raw.isdecimal() or len(raw) > 18 or int(raw) < 1):
            raise ValidationError("Choose a valid saved view.")
        save_work_view(user=request.user, name=request.POST.get("name", ""), filters=json.loads(request.POST.get("filters", "{}")), pk=int(raw) if raw else None)
    except (ValidationError, ValueError, TypeError) as error:
        return _error(request, " ".join(error.messages) if isinstance(error, ValidationError) else "Check the saved view settings.")
    messages.success(request, "Work view saved for your account.")
    return redirect(_return_url(request))


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def work_view_delete(request, pk):
    if pk < 1 or len(str(pk)) > 18:
        raise Http404
    delete_work_view(user=request.user, pk=pk)
    messages.success(request, "Saved view removed.")
    return redirect("ims:tasker:task_list")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def work_preferences_save(request):
    try:
        save_work_preferences(user=request.user, preset=request.POST.get("preset", "all"), layout=request.POST.get("layout", "list"))
    except ValidationError as error:
        return _error(request, " ".join(error.messages))
    messages.success(request, "Default Work view updated.")
    return redirect(_return_url(request))
