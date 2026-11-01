"""Optional rollout enforcement. It cannot grant domain permissions or AI budgets."""
from django.conf import settings
from django.utils import timezone
from zoneinfo import ZoneInfo


def feature_for_route(route):
    if route=='ui_preferences':return 'measurement'
    if route=='index':return 'operator'
    if route.startswith(('pilot_','baseline_','observation_')):return 'measurement'
    if route.startswith(('ai_','assistant_')):return 'ai'
    if route.startswith(('routine_',)):return 'recurrence'
    if route.startswith(('improvement_','sop_','experiment_','carry_decision','day_action')):return 'improvement'
    if route.startswith(('strategy_','scenario_','team_','membership_','organization_access_','goal_','key_result_','initiative_','outcome_','contribution_','business_goal_')):return 'strategy'
    if route.startswith(('business_',)):return 'business'
    if route.startswith(('report_',)):return 'reports'
    if route.startswith(('coordination_','notification_','jobs_')):return 'coordination'
    if route.startswith(('daily_','planning_','work_policy','availability_','schedule_','arrival_')):return 'planning'
    if route.startswith(('operator_',)):return 'operator'
    return 'core'


def released_features(user):
    from tasker.pilot_models import FEATURES
    all_features=frozenset((*FEATURES,'measurement'))
    if not getattr(settings,'TASKER_PILOT_ACCESS_ENFORCED',False):return all_features
    if not getattr(settings,'TASKER_PILOT_ENABLED',False):return frozenset()
    if not user or not user.is_authenticated or not user.is_active:return frozenset()
    from django.contrib.auth import get_user_model
    from tasker.pilot_models import PilotMember
    from tasker.organization import managed_teams_for,company_read_ids
    from .baseline import _employee_in_scope
    user=get_user_model().objects.filter(pk=user.pk,is_active=True).first()
    if not user:return frozenset()
    # These pages still apply their own current selectors/permissions.
    allowed={'measurement'}
    rows=list(PilotMember.objects.filter(employee=user,pilot__is_deleted=False,pilot__study__company__is_active=True).select_related('pilot__study'))
    # Cohort subjects do not get a manager bypass that would contaminate a control.
    if rows:
        from .pilots import digest,protocol
        for row in rows:
            p=row.pilot
            if p.is_synthetic or p.state!='APPROVED' or not p.rollout_authorized_at or not row.access_enabled or row.withdrawn_at or not _employee_in_scope(user.pk,p.study):continue
            if not row.rollout_on or row.rollout_on>timezone.now().astimezone(ZoneInfo(p.timezone_name)).date():continue
            if digest(protocol(p))!=p.protocol_digest:continue
            allowed.update(p.features)
        return frozenset(allowed)
    return all_features if managed_teams_for(user).exists() or (user.has_perm('tasker.manage_tasker') and company_read_ids(user).exists()) else frozenset(allowed)


def feature_access(user,feature='core'):
    return feature in released_features(user)
