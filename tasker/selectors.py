"""Reusable, read-only Tasker query composition.

Selectors keep access patterns consistent without mixing persistence or UI
concerns into models. Services may compose these querysets inside use cases.
"""

from django.db.models import Case, F, IntegerField, Q, Value, When

from .models import Task


def priority_rank_expression():
    return Case(
        When(priority=Task.Priority.CRITICAL, then=Value(0)),
        When(priority=Task.Priority.HIGH, then=Value(1)),
        When(priority=Task.Priority.MEDIUM, then=Value(2)),
        default=Value(3),
        output_field=IntegerField(),
    )


def owned_plannable_tasks(*, user, plan=None):
    queryset = (
        Task.objects.filter(owner=user, archived=False)
        .exclude(status=Task.Status.CANCELLED)
        .select_related("category")
    )
    active = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
    if plan and plan.pk:
        queryset = queryset.filter(
            Q(status__in=active) | Q(daily_plans=plan) | Q(priority_daily_plans=plan)
        ).distinct()
    else:
        queryset = queryset.filter(status__in=active)
    return queryset.annotate(priority_rank=priority_rank_expression()).order_by(
        "priority_rank", F("due_date").asc(nulls_last=True), "title"
    )
