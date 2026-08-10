from django.db.models import Q, QuerySet

from .models import Task


def visible_tasks_for(user) -> QuerySet:
    """Apply the Tasker object visibility boundary in one place."""
    queryset = Task.objects.select_related("owner", "assigned_by", "category", "parent_task")
    if user.has_perm("tasker.view_all_tasks") or user.has_perm("tasker.manage_tasker"):
        return queryset
    return queryset.filter(Q(owner=user) | Q(assigned_by=user))


def can_change_task(user, task: Task) -> bool:
    return user.has_perm("tasker.change_task") and (
        task.owner_id == user.pk
        or user.has_perm("tasker.manage_tasker")
        or (task.assigned_by_id == user.pk and user.has_perm("tasker.assign_task"))
    )


def can_delete_task(user, task: Task) -> bool:
    return user.has_perm("tasker.delete_task") and (
        task.owner_id == user.pk or user.has_perm("tasker.manage_tasker")
    )
