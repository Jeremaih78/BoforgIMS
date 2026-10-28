"""Basic management workspace; all reads and writes share domain scope."""
from django.contrib import messages
from django.conf import settings
from functools import wraps
from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Count, Q
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from accounting.models import Company
from tasker.models import (
    BaselineObservation, BaselineStudy, Goal, Initiative, KeyResult,
    OrganizationAccess, OrganizationEvent, OutcomeMeasurement, Task,
    TaskContribution, Team, TeamMembership,
)
from tasker.organization import (
    can_manage_company, can_manage_team, visible_companies_for, visible_teams_for,
)
from tasker.policies import can_change_task, visible_tasks_for
from tasker.services.baseline import (
    accept_observation, baseline_csv, baseline_summary, retract_observation,
    save_observation, save_study, visible_observations_for, visible_studies_for,
)
from tasker.services.business_goals import create_business_goal_drafts
from tasker.services.goals import (
    accept_outcome, can_manage_outcome, key_result_progress, link_task_contribution, record_outcome,
    retract_outcome, save_goal, save_initiative, save_key_result,
    visible_goals_for, visible_initiatives_for, visible_key_results_for, visible_outcomes_for,
)
from tasker.stage2_forms import (
    BaselineObservationForm, BaselineStudyForm, ContributionForm, GoalForm,
    InitiativeForm, KeyResultForm, MembershipForm, OrganizationAccessForm,
    OutcomeForm, TeamForm, manageable_companies,
)


def workspace_view(view):
    @wraps(view)
    def gated(request, *args, **kwargs):
        if not getattr(settings, "TASKER_DIRECTION_ENABLED", False):
            raise Http404
        return view(request, *args, **kwargs)
    return login_required(permission_required("tasker.view_task", raise_exception=True)(gated))


def _page(queryset, request):
    return Paginator(queryset, 20).get_page(request.GET.get("page"))


def _form_error(form, error):
    if hasattr(error, "message_dict"):
        for field, errors in error.message_dict.items():
            for message in errors:
                form.add_error(field if field in form.fields else None, message)
    else:
        form.add_error(None, error)


def _edit(request, *, form_class, saver, title, destination, instance=None, initial=None):
    form = form_class(request.POST if request.method == "POST" else None, user=request.user, instance=instance, initial=initial)
    if request.method == "POST" and form.is_valid():
        try:
            saver(form=form, user=request.user)
        except ValidationError as exc:
            _form_error(form, exc)
        else:
            messages.success(request, "Changes saved.")
            return redirect(destination)
    return render(request, "tasker/direction/form.html", {"form": form, "page_title": title, "cancel_url": destination})


@transaction.atomic
def _save_organization(*, form, user):
    obj = form.save(commit=False)
    team = obj if isinstance(obj, Team) else (obj.team if isinstance(obj, TeamMembership) else None)
    company = team.company if team else obj.company
    Company.objects.select_for_update().get(pk=company.pk)
    user = get_user_model().objects.get(pk=user.pk)
    capability = f"tasker.{'change' if obj.pk else 'add'}_{obj._meta.model_name}"
    if not user.is_active or not user.has_perm(capability):
        raise PermissionDenied
    if isinstance(obj, OrganizationAccess):
        if not user.is_superuser:
            raise PermissionDenied
    elif not can_manage_company(user, company):
        raise PermissionDenied
    if obj.pk:
        current = type(obj).all_objects.select_for_update().get(pk=obj.pk)
        current_company = current.company if hasattr(current, "company") else current.team.company
        if current.is_deleted or (not user.is_superuser and not can_manage_company(user, current_company)):
            raise PermissionDenied
        if isinstance(obj, Team) and obj.company_id != current.company_id:
            raise ValidationError("Create a separate team to change company scope.")
    obj.full_clean()
    if not obj.pk:
        obj.created_by = user
    obj.updated_by = user
    obj.save()
    OrganizationEvent.objects.create(
        company=company, team=team, actor=user, action="configuration.saved",
        object_type=obj._meta.model_name, object_id=str(obj.pk),
        details={name: form.cleaned_data[name] for name in ("role", "is_active", "can_manage", "can_view_all", "can_receive_reports") if name in form.cleaned_data},
    )
    return obj


@workspace_view
@permission_required("tasker.view_team", raise_exception=True)
def team_list(request):
    return render(request, "tasker/direction/teams.html", {
        "teams": _page(visible_teams_for(request.user).select_related("company"), request),
        "can_configure": manageable_companies(request.user).exists(),
        "grants": OrganizationAccess.objects.select_related("company", "user").all()[:100] if request.user.is_superuser else (),
    })


@workspace_view
@permission_required("tasker.view_team", raise_exception=True)
def team_detail(request, pk):
    team = get_object_or_404(visible_teams_for(request.user), pk=pk)
    tasks = visible_tasks_for(request.user).filter(team=team)
    members = TeamMembership.objects.filter(team=team).select_related("user").order_by("user__username") if request.user.has_perm("tasker.view_teammembership") else TeamMembership.objects.none()
    work_totals = {row["owner_id"]: row for row in tasks.values("owner_id").annotate(
        total=Count("pk"), completed=Count("pk", filter=Q(status=Task.Status.COMPLETED)),
        blocked=Count("pk", filter=Q(status=Task.Status.BLOCKED)),
    )}
    members = list(members)
    for member in members:
        member.work_summary = work_totals.get(member.user_id, {"total": 0, "completed": 0, "blocked": 0})
    return render(request, "tasker/direction/team_detail.html", {
        "team": team, "members": members,
        "tasks": _page(tasks.order_by("owner__username", "title"), request),
        "can_configure": can_manage_company(request.user, team.company),
        "can_delegate": can_manage_team(request.user, team),
    })


@workspace_view
def team_edit(request, pk=None):
    if not request.user.has_perm(f"tasker.{'change' if pk else 'add'}_team") or not manageable_companies(request.user).exists():
        raise PermissionDenied
    instance = get_object_or_404(visible_teams_for(request.user), pk=pk) if pk else None
    if instance and not can_manage_company(request.user, instance.company):
        raise PermissionDenied
    return _edit(request, form_class=TeamForm, saver=_save_organization, title="Edit team" if pk else "Create team", destination="ims:tasker:team_list", instance=instance)


@workspace_view
def membership_edit(request, pk=None):
    if not request.user.has_perm(f"tasker.{'change' if pk else 'add'}_teammembership") or not manageable_companies(request.user).exists():
        raise PermissionDenied
    instance = get_object_or_404(TeamMembership.objects.filter(team__company__in=manageable_companies(request.user)), pk=pk) if pk else None
    initial = {"team": request.GET.get("team")} if not pk else None
    return _edit(request, form_class=MembershipForm, saver=_save_organization, title="Edit membership" if pk else "Add team member", destination="ims:tasker:team_list", instance=instance, initial=initial)


@workspace_view
def organization_access_edit(request, pk=None):
    if not request.user.is_superuser:
        raise PermissionDenied
    instance = get_object_or_404(OrganizationAccess.objects.all(), pk=pk) if pk else None
    return _edit(request, form_class=OrganizationAccessForm, saver=_save_organization, title="Company access and report rights", destination="ims:tasker:team_list", instance=instance)


@workspace_view
def goal_list(request):
    return render(request, "tasker/direction/goals.html", {
        "goals": _page(visible_goals_for(request.user).select_related("company", "team", "owner"), request),
        "manageable_companies": manageable_companies(request.user),
    })


@workspace_view
@require_POST
def business_goal_drafts(request):
    company_id = request.POST.get("company", "")
    if not company_id.isdigit():
        raise Http404
    company = get_object_or_404(manageable_companies(request.user), pk=company_id)
    create_business_goal_drafts(user=request.user, company=company)
    messages.success(request, "The three agreed company directions are available as drafts. Confirm missing measures, owners and dates before activation.")
    return redirect("ims:tasker:goal_list")


@workspace_view
def goal_detail(request, pk):
    goal = get_object_or_404(visible_goals_for(request.user), pk=pk)
    results = visible_key_results_for(request.user).filter(goal=goal)
    initiatives = list(visible_initiatives_for(request.user).filter(goal=goal))
    from tasker.services.captures import visible_captures_for
    sources = {capture.promoted_initiative_id: capture for capture in visible_captures_for(request.user).filter(promoted_initiative__in=initiatives)}
    for initiative in initiatives:
        initiative.visible_source_capture = sources.get(initiative.pk)
    contributions = TaskContribution.objects.filter(initiative__in=initiatives, task__in=visible_tasks_for(request.user)).select_related("task", "initiative")[:50]
    outcomes = visible_outcomes_for(request.user).filter(key_result__goal=goal).select_related("employee", "key_result")
    outcomes_page = _page(outcomes, request)
    for outcome in outcomes_page:
        outcome.can_manage_evidence = can_manage_outcome(request.user, outcome)
    return render(request, "tasker/direction/goal_detail.html", {
        "goal": goal, "results": [(kr, key_result_progress(kr)) for kr in results],
        "initiatives": initiatives, "contributions": contributions,
        "outcomes": outcomes_page,
        "can_manage": can_manage_company(request.user, goal.company) or (goal.team_id and can_manage_team(request.user, goal.team)),
    })


@workspace_view
def goal_edit(request, pk=None):
    instance = get_object_or_404(visible_goals_for(request.user), pk=pk) if pk else None
    return _edit(request, form_class=GoalForm, saver=save_goal, title="Edit goal" if pk else "Create goal", destination="ims:tasker:goal_list", instance=instance)


@workspace_view
def key_result_edit(request, pk=None):
    instance = get_object_or_404(visible_key_results_for(request.user), pk=pk) if pk else None
    return _edit(request, form_class=KeyResultForm, saver=save_key_result, title="Measurable result", destination="ims:tasker:goal_list", instance=instance, initial={"goal": request.GET.get("goal")})


@workspace_view
def initiative_edit(request, pk=None):
    instance = get_object_or_404(visible_initiatives_for(request.user), pk=pk) if pk else None
    return _edit(request, form_class=InitiativeForm, saver=save_initiative, title="Initiative", destination="ims:tasker:goal_list", instance=instance, initial={"goal": request.GET.get("goal")})


@workspace_view
def contribution_edit(request, slug):
    task = get_object_or_404(visible_tasks_for(request.user), slug=slug)
    if not can_change_task(request.user, task):
        raise PermissionDenied
    form = ContributionForm(request.POST if request.method == "POST" else None, user=request.user)
    if request.method == "POST" and form.is_valid():
        try:
            link_task_contribution(task=task, initiative=form.cleaned_data["initiative"], user=request.user)
        except ValidationError as exc:
            _form_error(form, exc)
        else:
            messages.success(request, "Primary goal contribution saved.")
            return redirect(task.get_absolute_url())
    return render(request, "tasker/direction/form.html", {"form": form, "page_title": f"Link goal: {task.title}", "task": task})


@workspace_view
def outcome_create(request):
    def saver(*, form, user):
        return record_outcome(user=user, **form.cleaned_data)
    return _edit(request, form_class=OutcomeForm, saver=saver, title="Record outcome evidence", destination="ims:tasker:goal_list")


@workspace_view
@require_POST
def outcome_action(request, pk, action):
    outcome = get_object_or_404(visible_outcomes_for(request.user), pk=pk)
    try:
        if action == "accept":
            accept_outcome(outcome=outcome, user=request.user)
        elif action == "retract":
            retract_outcome(outcome=outcome, user=request.user, reason=request.POST.get("reason", ""))
        else:
            raise PermissionDenied
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Outcome evidence updated.")
    return redirect("ims:tasker:goal_detail", pk=outcome.key_result.goal_id)


@workspace_view
def baseline_list(request):
    return render(request, "tasker/direction/baselines.html", {"studies": _page(visible_studies_for(request.user), request)})


@workspace_view
def baseline_detail(request, pk):
    study = get_object_or_404(visible_studies_for(request.user), pk=pk)
    return render(request, "tasker/direction/baseline_detail.html", {
        "study": study, "summary": baseline_summary(study, user=request.user),
        "observations": _page(visible_observations_for(request.user).filter(study=study).select_related("employee"), request),
        "can_manage": can_manage_company(request.user, study.company) or (study.team_id and can_manage_team(request.user, study.team)),
    })


@workspace_view
def baseline_edit(request, pk=None):
    instance = get_object_or_404(visible_studies_for(request.user), pk=pk) if pk else None
    return _edit(request, form_class=BaselineStudyForm, saver=save_study, title="Baseline measurement plan", destination="ims:tasker:baseline_list", instance=instance)


@workspace_view
def observation_edit(request, pk=None):
    instance = get_object_or_404(visible_observations_for(request.user), pk=pk) if pk else None
    return _edit(request, form_class=BaselineObservationForm, saver=save_observation, title="Baseline observation", destination="ims:tasker:baseline_list", instance=instance, initial={"study": request.GET.get("study")})


@workspace_view
@require_POST
def observation_action(request, pk, action):
    observation = get_object_or_404(visible_observations_for(request.user), pk=pk)
    try:
        if action == "accept":
            accept_observation(observation=observation, user=request.user)
        elif action == "retract":
            retract_observation(observation=observation, user=request.user, reason=request.POST.get("reason", ""))
        else:
            raise PermissionDenied
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Baseline observation updated.")
    return redirect("ims:tasker:baseline_detail", pk=observation.study_id)


@workspace_view
def baseline_export(request, pk):
    study = get_object_or_404(visible_studies_for(request.user), pk=pk)
    response = HttpResponse(baseline_csv(request.user, study), content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="tasker-baseline-{study.pk}.csv"'
    return response
