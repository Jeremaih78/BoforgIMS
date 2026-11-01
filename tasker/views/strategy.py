"""A scoped intervention board and explicit, versioned scenario review."""
from copy import deepcopy
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tasker.models import Task, Team
from tasker.organization import managed_teams_for
from tasker.services import scenarios, strategy
from tasker.strategy_forms import (ScenarioChanges, ScenarioCreateForm, ScenarioRevisionForm,
    StrategyFilterForm, WorkDeclarationForm)


def _enabled():
    if not getattr(settings, "TASKER_STRATEGY_ENABLED", False):
        raise Http404


def _error(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


def _timestamp(value):
    if not value or not isinstance(value, str):
        return value
    return parse_datetime(value)


def _present_board(board, user):
    """Presentation-only fields; never infer declarations from activity."""
    board = deepcopy(board)
    goals = {goal["id"]: goal for goal in board["goals"]}
    task_index = {}
    def task_fields(task):
        task["goal"] = goals.get(task.get("goal_id"))
        task["deadline_date"] = parse_date(task["due_date"]) if task.get("due_date") else None
        task["last_work_update_date"] = _timestamp(task.get("last_work_update"))
        task_index[task["id"]] = task
    for lane in board["lanes"]:
        lane["declaration"]["last_confirmed_date"] = _timestamp(lane["declaration"].get("last_confirmed"))
        lane["arrival"]["effective_date"] = _timestamp(lane["arrival"].get("effective_at"))
        lane["end_day"]["submitted_date"] = _timestamp(lane["end_day"].get("submitted_at"))
        for task in lane["tasks"]:
            task_fields(task)
        for action in lane.get('business_actions',[]):
            action['due_date']=parse_date(action['due_date']) if action.get('due_date') else None
        if lane["is_self"]:
            lane["declaration_form"] = WorkDeclarationForm(user=user,
                team=Team.objects.filter(pk=lane["team_id"]).first(), prefix="declare_"+str(lane["team_id"]),
                initial={"team": lane["team_id"], "current_task": (lane["declaration"].get("current") or {}).get("id"),
                    "next_task": (lane["declaration"].get("next") or {}).get("id"),
                    "expected_revision": lane["declaration"]["revision"]})
    for task in board["unallocated_tasks"]:
        task_fields(task)
    for link in board["dependencies"]:
        task = task_index.get(link["task_id"], {})
        link["source_title"] = task.get("title", "Task #"+str(link["task_id"]))
        link["source_url"] = task.get("url", "")
    board["generated_date"] = _timestamp(board.get("generated_at"))
    return board


def _board_context(request, form, *, declaration_error="", declaration_form=None, declaration_team=None):
    data = form.cleaned_data if form.is_valid() else {}
    board = strategy.build_strategy_board(user=request.user,
        team_id=data["team"].pk if data.get("team") else None,
        company_id=data["company"].pk if data.get("company") else None,
        day=data.get("day"), view=data.get("view") or "execution", page=data.get("page") or 1)
    board = _present_board(board, request.user)
    if declaration_form is not None:
        for lane in board["lanes"]:
            if lane["is_self"] and lane["team_id"] == declaration_team:
                lane["declaration_form"] = declaration_form
    params = {key: value for key, value in request.GET.items() if key not in ("view", "page")}
    tabs = [{"name": label, "key": key, "url": "?"+urlencode({**params, "view": key})}
        for key, label in (("execution", "Execution"), ("dependencies", "Dependencies"), ("capacity", "Capacity"))]
    pagination = board["pagination"]
    current = {**params, "view": board["view"]}
    team_ai_id = None
    if data.get("team") and getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False) and request.user.has_perm("tasker.use_ai"):
        from tasker.services.reporting import report_teams_for
        if report_teams_for(request.user).filter(pk=data["team"].pk).exists():
            team_ai_id = data["team"].pk
    return {"board": board, "filter_form": form, "view_tabs": tabs,
        "previous_url": "?"+urlencode({**current, "page": max(1, pagination["page"]-1)}),
        "next_url": "?"+urlencode({**current, "page": pagination["page"]+1}),
        "declaration_error": declaration_error,
        "team_ai_id": team_ai_id,
        "can_scenario": request.user.has_perms(("tasker.change_task", "tasker.assign_task")) and managed_teams_for(request.user).exists()}


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def strategy_board(request):
    _enabled()
    form = StrategyFilterForm(request.GET, user=request.user)
    valid = form.is_valid()
    return render(request, "tasker/strategy/board.html", _board_context(request, form), status=200 if valid else 400)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def strategy_declare(request):
    _enabled()
    try:
        team_id = int(request.POST.get("team_id", ""))
    except (ValueError, TypeError):
        raise PermissionDenied from None
    team = strategy.strategy_teams_for(request.user).filter(pk=team_id).first()
    if team is None:
        raise PermissionDenied
    form = WorkDeclarationForm(request.POST, user=request.user, team=team, prefix="declare_"+str(team.pk))
    status = 400
    if form.is_valid():
        data = form.cleaned_data
        try:
            strategy.declare_work(user=request.user, team_id=data["team"].pk,
                current_task_id=data["current_task"].pk if data["current_task"] else None,
                next_task_id=data["next_task"].pk if data["next_task"] else None,
                expected_revision=data["expected_revision"])
        except (ValueError, ValidationError) as exc:
            form.add_error(None, _error(exc))
            status = 409
        else:
            messages.success(request, "Your current and next work declaration is confirmed.")
            return redirect(reverse("ims:tasker:strategy_board")+"?"+urlencode({"team": team.pk}))
    filter_form = StrategyFilterForm({"team": team.pk}, user=request.user)
    return render(request, "tasker/strategy/board.html", _board_context(request, filter_form,
        declaration_error="Your declaration was not saved. Review the current work and your retained choices.",
        declaration_form=form, declaration_team=team.pk), status=status)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def scenario_list(request):
    _enabled()
    rows = scenarios.visible_scenarios_for(request.user).select_related("team", "team__company")
    page = Paginator(rows, 20).get_page(request.GET.get("page"))
    readable = []
    for candidate in page.object_list:
        try:
            readable.append(scenarios.get_scenario(user=request.user, scenario_id=candidate.pk))
        except PermissionDenied:
            continue
    page.object_list = readable
    return render(request, "tasker/strategy/scenarios.html", {"page_obj": page})


def _create_context(request, form, changes, team):
    # This is scoped source data for setting explicit form fields only.
    source = []
    if team:
        tasks = changes.forms[0].fields["task"].queryset if changes.forms else changes.empty_form.fields["task"].queryset
        source = [{"id": task.pk, "owner": task.owner_id, "priority": task.priority,
            "due_date": task.due_date.isoformat() if task.due_date else "",
            "due_time": task.due_time.isoformat(timespec="minutes") if task.due_time else ""}
            for task in tasks]
        from tasker.services.coordination import scoped_tasks_for
        from tasker.organization import assignable_users_for
        task_total = scoped_tasks_for(request.user).filter(team=team, archived=False, status__in=("PENDING", "IN_PROGRESS", "BLOCKED")).count()
        owner_total = assignable_users_for(request.user, team=team).count()
    else:
        task_total = owner_total = 0
    return {"form": form, "changes": changes, "team": team, "task_initials": source,
        "omitted_task_choices": max(0, task_total-len(source)), "omitted_owner_choices": max(0, owner_total-200)}


@login_required
@permission_required(("tasker.view_task", "tasker.change_task", "tasker.assign_task"), raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def scenario_create(request):
    _enabled()
    if not managed_teams_for(request.user).exists():
        raise PermissionDenied
    supplied = request.POST if request.method == "POST" else request.GET
    team = managed_teams_for(request.user).filter(pk=supplied.get("team")).first() if str(supplied.get("team", "")).isdecimal() else None
    form = ScenarioCreateForm(request.POST if request.method == "POST" else None, user=request.user,
        initial={"team": team.pk if team else None})
    changes = ScenarioChanges(request.POST if request.method == "POST" else None, prefix="changes",
        form_kwargs={"user": request.user, "team": team})
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid() and changes.is_valid():
            edits = [{"task_id": row.cleaned_data["task"].pk, "owner_id": row.cleaned_data["owner"].pk,
                "priority": row.cleaned_data["priority"],
                "due_date": row.cleaned_data["due_date"].isoformat() if row.cleaned_data["due_date"] else None,
                "due_time": row.cleaned_data["due_time"].isoformat(timespec="minutes") if row.cleaned_data["due_time"] else None}
                for row in changes if row.cleaned_data and not row.cleaned_data.get("DELETE")]
            try:
                item = scenarios.create_scenario(user=request.user, team_id=form.cleaned_data["team"].pk,
                    title=form.cleaned_data["title"], changes=edits, client_key=form.cleaned_data["client_key"])
            except (ValueError, ValidationError) as exc:
                form.add_error(None, _error(exc))
                status = 409
            else:
                return redirect("ims:tasker:scenario_detail", pk=item.pk)
    return render(request, "tasker/strategy/scenario_form.html", _create_context(request, form, changes, team), status=status)


def _scenario_context(item, *, error=""):
    return {"scenario": item, "preview": item.preview, "error": error,
        "expired": item.expires_at <= timezone.now()}


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def scenario_detail(request, pk):
    _enabled()
    return render(request, "tasker/strategy/scenario_detail.html", _scenario_context(
        scenarios.get_scenario(user=request.user, scenario_id=pk)))


def _scenario_action(request, pk, action):
    _enabled()
    item = scenarios.get_scenario(user=request.user, scenario_id=pk)
    form = ScenarioRevisionForm(request.POST)
    if not form.is_valid():
        return render(request, "tasker/strategy/scenario_detail.html", _scenario_context(item,
            error="Reload the scenario and review its current version before acting."), status=400)
    try:
        method = scenarios.commit_scenario if action == "commit" else scenarios.discard_scenario
        method(user=request.user, scenario_id=pk, **form.cleaned_data)
    except (ValueError, ValidationError) as exc:
        return render(request, "tasker/strategy/scenario_detail.html", _scenario_context(
            scenarios.get_scenario(user=request.user, scenario_id=pk), error=_error(exc)), status=409)
    messages.success(request, "Scenario committed. Targeted work updates were recorded for eligible affected employees." if action == "commit" else "Draft discarded. Live work was not changed.")
    return redirect("ims:tasker:scenario_detail", pk=pk)


def _discard_action(request, pk):
    """Closing one's draft remains possible after rollout/management revocation."""
    form = ScenarioRevisionForm(request.POST)
    if not form.is_valid():
        messages.warning(request, "Reload your draft and review its current version before discarding it.")
        return redirect("ims:tasker:index")
    try:
        scenarios.discard_scenario(user=request.user, scenario_id=pk, **form.cleaned_data)
    except (ValueError, ValidationError) as exc:
        if getattr(settings, "TASKER_STRATEGY_ENABLED", False):
            try:
                item = scenarios.get_scenario(user=request.user, scenario_id=pk)
            except PermissionDenied:
                pass
            else:
                return render(request, "tasker/strategy/scenario_detail.html", _scenario_context(item, error=_error(exc)), status=409)
        messages.warning(request, "This draft could not be discarded in its current state. No work changes were made.")
        return redirect("ims:tasker:index")
    messages.success(request, "Draft discarded. Live work was not changed.")
    if getattr(settings, "TASKER_STRATEGY_ENABLED", False):
        try:
            scenarios.get_scenario(user=request.user, scenario_id=pk)
        except PermissionDenied:
            pass
        else:
            return redirect("ims:tasker:scenario_detail", pk=pk)
    return redirect("ims:tasker:index")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def scenario_commit(request, pk):
    return _scenario_action(request, pk, "commit")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def scenario_discard(request, pk):
    return _discard_action(request, pk)
