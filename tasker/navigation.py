"""Navigation availability follows the same live scope as its destination."""
from django.conf import settings
from django.urls import reverse

from .models import OrganizationAccess
from .organization import managed_teams_for


def has_strategy_access(user):
    if not getattr(settings, "TASKER_DIRECTION_ENABLED", False):
        return False
    if not user.is_authenticated or not user.is_active or not user.has_perm("tasker.view_team"):
        return False
    if user.is_superuser:
        return True
    if managed_teams_for(user).exists():
        return True
    return (user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker")) and OrganizationAccess.objects.filter(
        user=user, is_active=True, company__is_active=True, can_manage=True,
    ).exists()


def shell_navigation(request):
    user = request.user
    if not user.is_authenticated or not user.has_perm("tasker.view_task"):
        return {"tasker_primary_navigation": [], "tasker_page_label": "Tasker"}
    view = request.resolver_match.url_name or ""
    direction_enabled = getattr(settings, "TASKER_DIRECTION_ENABLED", False)
    strategy_allowed = has_strategy_access(user)
    if view.startswith("capture_"):
        section = "inbox"
    elif view in ("daily_plan", "daily_review", "planning_home", "work_policy", "availability_home", "availability_remove", "schedule_create", "schedule_edit", "schedule_detail", "schedule_accept", "schedule_cancel"):
        section = "plan"
    elif view.startswith(("goal_", "key_result_", "initiative_", "outcome_", "business_goal_", "contribution_")):
        section = "goals"
    elif view == "strategy_home" or view.startswith(("team_", "membership_", "organization_access_", "baseline_", "observation_")):
        section = "strategy" if strategy_allowed else "goals"
    elif view.startswith(("task_", "focus_", "checklist_", "comment_", "time_", "progress_")):
        section = "work"
    elif view.startswith("arrival_"):
        section = "now"
    elif view == "index" or view.startswith("operator_"):
        section = "now"
    elif view.startswith(("coordination_", "notification_", "jobs_")):
        section = "now"
    elif view.startswith("report_"):
        section = "strategy" if strategy_allowed else "plan"
    else:
        section = ""
    destinations = [
        ("now", "Now", "index", "sun", True),
        ("inbox", "Inbox", "capture_list", "inbox", True),
        ("plan", "Plan", "daily_plan", "calendar3", user.has_perm("tasker.view_dailyplan")),
        ("work", "Work", "task_list", "check2-square", True),
        ("goals", "Goals", "goal_list", "bullseye", direction_enabled and user.has_perm("tasker.view_goal")),
        ("strategy", "Strategy", "strategy_home", "diagram-3", strategy_allowed),
    ]
    nav = [{"key": key, "label": label, "url": reverse(f"ims:tasker:{route}"), "icon": icon, "active": key == section}
           for key, label, route, icon, allowed in destinations if allowed]
    titles = {"capture_list": "Inbox", "capture_create": "Capture", "capture_detail": "Capture", "capture_promote": "Turn capture into work", "capture_legacy": "Older scratchpads", "index": "Now", "task_list": "Work", "task_create": "Create task", "task_edit": "Edit task", "task_detail": "Task details", "daily_plan": "Plan", "daily_review": "End day review", "ui_preferences": "Preferences", "assistant_home": "Personal assistant", "ai_usage_dashboard": "AI usage", "strategy_home": "Strategy", "team_list": "Teams", "team_detail": "Team overview", "goal_list": "Goals", "goal_detail": "Goal overview", "baseline_list": "Productivity baseline", "baseline_detail": "Baseline evidence"}
    titles.update({"planning_home":"Time blocks","work_policy":"Work hours","availability_home":"Unavailable time","schedule_create":"Plan time","schedule_edit":"Revise work block","schedule_detail":"Review work block","arrival_home":"Arrival","task_dependencies":"Dependencies","task_effort":"Remaining effort"})
    titles.update({"operator_home": "Now", "operator_policy": "Ranking preferences", "operator_start": "Now", "operator_block": "Now"})
    titles.update({"coordination_home":"Coordination", "coordination_create":"Request help or handoff", "coordination_detail":"Coordination request", "notification_home":"Notifications", "notification_preferences":"Quiet hours and digests", "report_home":"Team reports", "report_detail":"Team report", "report_policy":"Test report policy", "jobs_home":"Job health"})
    return {"tasker_primary_navigation": nav, "tasker_page_label": titles.get(view, "Workspace"),
            "tasker_current_section": section,
            "tasker_strategy_allowed": strategy_allowed,
            "tasker_plan_available": user.has_perm("tasker.view_dailyplan"),
            "tasker_goals_available": direction_enabled and user.has_perm("tasker.view_goal")}
