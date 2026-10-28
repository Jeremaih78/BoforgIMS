"""Stage 3 destinations for scoped management navigation and UI preferences."""
from django.contrib.auth.decorators import login_required, permission_required
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Q
from django.shortcuts import render
from django.http import Http404
from django.views.decorators.http import require_GET

from tasker.models import Task
from tasker.navigation import has_strategy_access
from tasker.organization import managed_teams_for, visible_companies_for, can_manage_company
from tasker.policies import visible_tasks_for
from tasker.services.baseline import visible_studies_for


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
def ui_preferences(request):
    return render(request, "tasker/preferences.html")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
def strategy_home(request):
    if not getattr(settings, "TASKER_DIRECTION_ENABLED", False):
        raise Http404
    if not has_strategy_access(request.user):
        raise PermissionDenied
    teams = list(managed_teams_for(request.user).select_related("company").order_by("company__name", "name")[:24])
    tasks = visible_tasks_for(request.user).filter(team_id__in=[team.pk for team in teams], archived=False)
    work = {row["team_id"]: row for row in tasks.values("team_id").annotate(
        active=Count("pk", filter=Q(status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED))),
        blocked=Count("pk", filter=Q(status=Task.Status.BLOCKED)),
        completed=Count("pk", filter=Q(status=Task.Status.COMPLETED)),
    )}
    for team in teams:
        team.work_summary = work.get(team.pk, {"active": 0, "blocked": 0, "completed": 0})
    companies = [company for company in visible_companies_for(request.user) if can_manage_company(request.user, company)]
    return render(request, "tasker/strategy_home.html", {
        "teams": teams, "managed_companies": companies,
        "baseline_studies": visible_studies_for(request.user).filter(Q(team_id__in=[t.pk for t in teams]) | Q(company__in=companies)).order_by("-created_at")[:6],
    })
