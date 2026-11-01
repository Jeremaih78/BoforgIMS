"""Navigation availability follows the same live scope as its destination."""
from django.conf import settings
from django.urls import reverse

from .models import OrganizationAccess
from .organization import managed_teams_for


def has_strategy_access(user):
    if getattr(settings, "TASKER_STRATEGY_ENABLED", False):
        return bool(user and user.is_authenticated and user.is_active and user.has_perm("tasker.view_task"))
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


def has_business_access(user):
    """Adapter rollout gates precede source permission or record access."""
    collections = getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False)
    replenishment = getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False)
    from operations.registry import SOURCES
    from operations.policies import capable
    from tasker.services.business_links import enabled
    operational = any(enabled(kind) and capable(user,kind) for kind in SOURCES) if user.is_authenticated else False
    if not collections and not replenishment and not operational:
        return False
    if not user.is_authenticated or not user.is_active or not user.has_perm("tasker.view_task"):
        return False
    from core.permissions import allowed
    return bool(operational or (collections and (allowed(user, "credit_control.view_collectiontask", staff=False)
        or allowed(user, "credit_control.view_promisetopay", staff=False)))
        or (replenishment and allowed(user, "inventory.view_replenishmentplan", staff=False)
            and allowed(user, "inventory.view_product", staff=True)))


def shell_navigation(request,allowed_features=None):
    user = request.user
    if not user.is_authenticated or not user.has_perm("tasker.view_task"):
        return {"tasker_primary_navigation": [], "tasker_page_label": "Tasker"}
    view = request.resolver_match.url_name or ""
    from tasker.services.pilot_access import released_features,feature_for_route
    if allowed_features is None:allowed_features=released_features(user)
    direction_enabled = getattr(settings, "TASKER_DIRECTION_ENABLED", False)
    strategy_allowed = 'strategy' in allowed_features and has_strategy_access(user)
    business_allowed = 'business' in allowed_features and has_business_access(user)
    if view.startswith('pilot_'):
        section='pilot'
    elif view.startswith(('improvement_', 'sop_', 'routine_', 'experiment_', 'carry_decision','day_action')):
        section='improve'
    elif view.startswith("capture_"):
        section = "inbox"
    elif view in ("daily_plan", "daily_review", "planning_home", "work_policy", "availability_home", "availability_remove", "schedule_create", "schedule_edit", "schedule_detail", "schedule_accept", "schedule_cancel"):
        section = "plan"
    elif view.startswith(("goal_", "key_result_", "initiative_", "outcome_", "business_goal_", "contribution_")):
        section = "goals"
    elif view.startswith(("strategy_", "scenario_", "team_", "membership_", "organization_access_", "baseline_", "observation_")):
        section = "strategy" if strategy_allowed else "goals"
    elif view.startswith("business_"):
        section = "business"
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
        ('improve','Improve','improvement_home','arrow-repeat',getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False)),
        ('pilot','Pilot','pilot_list','clipboard-data',getattr(settings,'TASKER_PILOT_ENABLED',False) and user.has_perm('tasker.view_pilot')),
        ("business", "Business actions", "business_list", "briefcase", business_allowed),
        ("goals", "Goals", "goal_list", "bullseye", direction_enabled and user.has_perm("tasker.view_goal")),
        ("strategy", "Strategy", "strategy_home", "diagram-3", strategy_allowed),
    ]
    nav = [{"key": key, "label": label, "url": reverse(f"ims:tasker:{route}"), "icon": icon, "active": key == section}
           for key, label, route, icon, allowed in destinations if allowed and feature_for_route(route) in allowed_features]
    titles = {"capture_list": "Inbox", "capture_create": "Capture", "capture_detail": "Capture", "capture_promote": "Turn capture into work", "capture_legacy": "Older scratchpads", "index": "Now", "task_list": "Work", "task_create": "Create task", "task_edit": "Edit task", "task_detail": "Task details", "daily_plan": "Plan", "daily_review": "End day review", "ui_preferences": "Preferences", "assistant_home": "Personal assistant", "ai_usage_dashboard": "AI usage", "strategy_home": "Strategy", "team_list": "Teams", "team_detail": "Team overview", "goal_list": "Goals", "goal_detail": "Goal overview", "baseline_list": "Productivity baseline", "baseline_detail": "Baseline evidence"}
    titles.update({"planning_home":"Time blocks","work_policy":"Work hours","availability_home":"Unavailable time","schedule_create":"Plan time","schedule_edit":"Revise work block","schedule_detail":"Review work block","arrival_home":"Arrival","task_dependencies":"Dependencies","task_effort":"Remaining effort"})
    titles.update({"operator_home": "Now", "operator_policy": "Ranking preferences", "operator_start": "Now", "operator_block": "Now"})
    titles.update({"coordination_home":"Coordination", "coordination_create":"Request help or handoff", "coordination_detail":"Coordination request", "notification_home":"Notifications", "notification_preferences":"Quiet hours and digests", "report_home":"Team reports", "report_detail":"Team report", "report_policy":"Test report policy", "jobs_home":"Job health"})
    titles.update({"ai_work_home": "Personal assistant", "ai_work_detail": "Review suggestion", "ai_budget": "AI budget and privacy"})
    titles.update({"strategy_board": "Strategy board", "strategy_declare": "Confirm work", "scenario_list": "Planning scenarios", "scenario_create": "Draft a scenario", "scenario_detail": "Review scenario"})
    titles.update({"business_list": "Business actions", "business_detail": "Business action", "business_open_source": "Open source"})
    titles.update({'improvement_home':'Weekly review','sop_list':'SOP library','sop_detail':'SOP','sop_create':'Create SOP',
        'sop_edit':'Publish SOP version','routine_list':'Recurring work','routine_detail':'Recurring work','routine_edit':'Edit routine',
        'improvement_ideas':'Idea review','experiment_list':'Experiments','experiment_detail':'Experiment','carry_decision':'Carry-over decision'})
    titles.update({'pilot_list':'Pilot','pilot_detail':'Pilot evidence','pilot_create':'Draft protocol','pilot_edit':'Pilot protocol','pilot_member':'Cohort roster','pilot_release':'Release readiness'})
    titles.update({'pilot_observation_create':'Record evidence','pilot_observation_edit':'Measurement draft',
        'pilot_observation_review':'Review evidence','pilot_action':'Pilot decision','pilot_validate':'Review productivity'})
    return {"tasker_primary_navigation": nav, "tasker_page_label": titles.get(view, "Workspace"),
            'tasker_workspace_home_url':reverse('ims:tasker:'+('index' if 'operator' in allowed_features else 'task_list' if 'core' in allowed_features else 'pilot_list')),
            "tasker_current_section": section,
            "tasker_strategy_allowed": strategy_allowed,
            "tasker_business_available": business_allowed,
            "tasker_plan_available": 'planning' in allowed_features and user.has_perm("tasker.view_dailyplan"),
            "tasker_goals_available": 'strategy' in allowed_features and direction_enabled and user.has_perm("tasker.view_goal")}
