"""Company/team scope shared by pages, exports, admin and write services."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q

from accounting.models import Company
from .models import OrganizationAccess, Team, TeamMembership


def _active(user):
    return bool(user and user.is_authenticated and user.is_active)


def _memberships(user):
    return TeamMembership.objects.filter(user=user, user__is_active=True, is_active=True,
                                         team__is_deleted=False, team__company__is_active=True)


def _grants(user):
    return OrganizationAccess.objects.filter(user=user, user__is_active=True, is_active=True,
                                            company__is_active=True)


def company_read_ids(user):
    if not _active(user):
        return Company.objects.none().values_list("pk", flat=True)
    if user.is_superuser:
        return Company.objects.filter(is_active=True).values_list("pk", flat=True)
    allowed = user.has_perm("tasker.view_company_work") or user.has_perm("tasker.view_all_tasks") or user.has_perm("tasker.manage_tasker")
    managed = user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker")
    condition = Q(pk__in=[])
    if allowed:
        condition |= Q(can_view_all=True)
    if managed:
        condition |= Q(can_manage=True)
    return _grants(user).filter(condition).values_list("company_id", flat=True)


def visible_companies_for(user):
    if not _active(user):
        return Company.objects.none()
    return Company.objects.filter(is_active=True).filter(
        Q(pk__in=company_read_ids(user)) | Q(pk__in=_memberships(user).values("team__company_id"))
    ).distinct().order_by("name")


def can_manage_company(user, company):
    company_id = getattr(company, "pk", company)
    if not _active(user) or not company_id:
        return False
    if not Company.objects.filter(pk=company_id, is_active=True).exists():
        return False
    if user.is_superuser:
        return True
    return (user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker")) and _grants(user).filter(company_id=company_id, can_manage=True).exists()


def managed_teams_for(user):
    queryset = Team.objects.filter(company__is_active=True)
    if not _active(user):
        return queryset.none()
    if user.is_superuser:
        return queryset
    company_ids = []
    if user.has_perm("tasker.manage_organization") or user.has_perm("tasker.manage_tasker"):
        company_ids = _grants(user).filter(can_manage=True).values("company_id")
    condition = Q(company_id__in=company_ids)
    if user.has_perm("tasker.assign_task") and user.has_perm("tasker.change_task"):
        condition |= Q(pk__in=_memberships(user).filter(role=TeamMembership.Role.MANAGER).values("team_id"))
    return queryset.filter(condition)


def can_manage_team(user, team):
    return bool(getattr(team, "pk", team) and managed_teams_for(user).filter(pk=getattr(team, "pk", team)).exists())


def visible_teams_for(user):
    queryset = Team.objects.select_related("company").filter(company__is_active=True)
    if not _active(user):
        return queryset.none()
    return queryset.filter(Q(company_id__in=company_read_ids(user)) | Q(pk__in=_memberships(user).values("team_id"))).distinct()


def assignable_users_for(user, team=None, company=None):
    users = get_user_model().objects.filter(is_active=True)
    if not _active(user):
        return users.none()
    team_id = getattr(team, "pk", team)
    company_id = getattr(company, "pk", company)
    if user.is_superuser and not team_id and not company_id:
        return users
    if team_id:
        if not can_manage_team(user, team_id):
            return users.filter(pk=user.pk, tasker_team_memberships__team_id=team_id,
                                tasker_team_memberships__is_active=True,
                                tasker_team_memberships__is_deleted=False).distinct()
        return users.filter(tasker_team_memberships__team_id=team_id,
                            tasker_team_memberships__is_active=True,
                            tasker_team_memberships__is_deleted=False).distinct()
    if company_id:
        if not can_manage_company(user, company_id):
            return users.filter(pk=user.pk) if visible_companies_for(user).filter(pk=company_id).exists() else users.none()
        return users.filter(
            Q(tasker_team_memberships__team__company_id=company_id,
              tasker_team_memberships__is_active=True, tasker_team_memberships__is_deleted=False,
              tasker_team_memberships__team__is_deleted=False)
            | Q(tasker_organization_access__company_id=company_id,
                tasker_organization_access__is_active=True, tasker_organization_access__is_deleted=False)
        ).distinct()
    # A missing company/team is personal or unmapped legacy work. Never infer
    # that coworkers' personal tasks are delegable from their memberships.
    return users.filter(pk=user.pk)


def validate_task_scope(user, task, previous=None, *, lock=False):
    """Fresh write validation. UI queries are convenience, never authorization."""
    if not _active(user):
        raise PermissionDenied
    company_id, team_id = task.company_id, task.team_id
    if lock:
        # Membership/grant writers use these same stable parent locks.
        company_ids = sorted({value for value in (company_id, getattr(previous, "company_id", None)) if value})
        list(Company.objects.select_for_update().filter(pk__in=company_ids).order_by("pk"))
        team_ids = sorted({value for value in (team_id, getattr(previous, "team_id", None)) if value})
        list(Team.objects.select_for_update().filter(pk__in=team_ids).order_by("pk"))
        user = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
        if user is None or (previous is None and not user.has_perm("tasker.add_task")):
            raise PermissionDenied
    if not task.owner_id or not get_user_model().objects.filter(pk=task.owner_id, is_active=True).exists():
        raise ValidationError({"owner": "Choose an active employee."})
    if company_id and not visible_companies_for(user).filter(pk=company_id).exists():
        raise PermissionDenied
    if team_id:
        if not Team.objects.filter(pk=team_id, company_id=company_id, company__is_active=True).exists():
            raise ValidationError({"team": "Choose a live team belonging to this company."})
        if not TeamMembership.objects.filter(team_id=team_id, user_id=task.owner_id,
                                              is_active=True, user__is_active=True).exists():
            raise ValidationError({"owner": "The employee must be an active member of this team."})
        if not visible_teams_for(user).filter(pk=team_id).exists():
            raise PermissionDenied
        manager = can_manage_team(user, team_id)
    elif company_id:
        manager = can_manage_company(user, company_id)
    else:
        manager = user.is_superuser
    owner_changed = previous is None or previous.owner_id != task.owner_id
    scope_changed = previous is not None and (previous.company_id != company_id or previous.team_id != team_id)
    if scope_changed and previous.company_id and not (
        can_manage_company(user, previous.company_id)
        or (previous.team_id and can_manage_team(user, previous.team_id))
    ):
        # Ownership permits doing assigned work, not removing its reporting
        # scope so the delegating manager can no longer see it.
        raise PermissionDenied
    if scope_changed and previous.company_id and not company_id and not can_manage_company(user, previous.company_id):
        raise PermissionDenied
    if (owner_changed or scope_changed) and task.owner_id != user.pk:
        if not manager or not user.has_perm("tasker.assign_task"):
            raise PermissionDenied
        if company_id and not assignable_users_for(user, team=team_id, company=company_id).filter(pk=task.owner_id).exists():
            raise ValidationError({"owner": "Choose an employee in this scope."})
    if scope_changed and previous.owner_id != user.pk:
        from .policies import can_change_task
        if not can_change_task(user, previous):
            raise PermissionDenied
    if task.parent_task_id:
        from .policies import visible_tasks_for
        parent = visible_tasks_for(user).filter(pk=task.parent_task_id).first()
        if parent is None or parent.company_id != company_id or parent.team_id != team_id or (not company_id and parent.owner_id != task.owner_id):
            raise ValidationError({"parent_task": "Parent and child must share an authorized company/team scope."})
    if previous and scope_changed and previous.subtasks.filter(is_deleted=False).exclude(company_id=company_id, team_id=team_id).exists():
        raise ValidationError({"team": "Move or unlink child tasks before changing their parent's scope."})
    if lock and previous is not None:
        from .services.dependencies import validate_dependency_scope
        validate_dependency_scope(candidate=task, previous=previous)


def arrival_can_read(user, subject, team):
    """Stage 2 identity contract only: arrival is self-reported, never presence."""
    if not _active(user):
        return False
    if getattr(subject, "pk", subject) == user.pk:
        return True
    team_id = getattr(team, "pk", team)
    if not TeamMembership.objects.filter(team_id=team_id, user_id=getattr(subject, "pk", subject),
                                          is_active=True, user__is_active=True,
                                          team__is_deleted=False, team__company__is_active=True).exists():
        return False
    return can_manage_team(user, team_id) or visible_teams_for(user).filter(pk=team_id, company_id__in=company_read_ids(user)).exists()


def require_task_access(user, task, *, change=True, capability=None):
    """Recheck a locked task against current scope, after a stale page lookup."""
    if task.company_id:
        list(Company.objects.select_for_update().filter(pk=task.company_id))
    if task.team_id:
        list(Team.objects.select_for_update().filter(pk=task.team_id))
    from .policies import can_change_task, visible_tasks_for
    user = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if not _active(user) or task.is_deleted or (capability and not user.has_perm(capability)):
        raise PermissionDenied
    if change:
        if not can_change_task(user, task):
            raise PermissionDenied
    elif not visible_tasks_for(user).filter(pk=task.pk).exists():
        raise PermissionDenied
