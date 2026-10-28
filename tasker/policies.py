from django.db.models import Q, QuerySet

from .models import Task


def visible_tasks_for(user) -> QuerySet:
    """Apply the Tasker object visibility boundary in one place."""
    from .organization import company_read_ids, managed_teams_for

    queryset = Task.objects.select_related("owner", "assigned_by", "category", "parent_task", "company", "team")
    if not user.is_authenticated or not user.is_active:
        return queryset.none()
    if user.is_superuser:
        return queryset
    return queryset.filter(
        Q(owner=user)
        | Q(company_id__in=company_read_ids(user))
        | Q(team_id__in=managed_teams_for(user).values("pk"), team__is_deleted=False)
    )


def can_change_task(user, task: Task) -> bool:
    from .organization import can_manage_company, can_manage_team

    return bool(user.is_authenticated and user.is_active and user.has_perm("tasker.change_task") and (
        user.is_superuser or task.owner_id == user.pk
        or (task.team_id and can_manage_team(user, task.team_id))
        or (task.company_id and can_manage_company(user, task.company_id))
    ))


def can_delete_task(user, task: Task) -> bool:
    return user.has_perm("tasker.delete_task") and can_change_task(user, task)
