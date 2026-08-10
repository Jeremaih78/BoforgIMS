from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from tasker.models import DailyPlan, Task
from tasker.selectors import priority_rank_expression

from .contracts import EmployeeDayContext, TaskSnapshot

AI_CONTEXT_TASK_LIMIT = 50
AI_CONTEXT_DESCRIPTION_LIMIT = 4000


def build_employee_day_context(*, user, work_date=None) -> EmployeeDayContext:
    """Build a minimal personal-work context without invoking an AI provider."""
    work_date = work_date or timezone.localdate()
    plan = (
        DailyPlan.objects.filter(owner=user, plan_date=work_date)
        .prefetch_related("top_priorities")
        .first()
    )
    tasks = (
        Task.objects.filter(owner=user, archived=False)
        .exclude(status=Task.Status.CANCELLED)
        .filter(
            Q(due_date__lte=work_date)
            | Q(carry_forward=True)
            | Q(
                daily_plans__owner=user,
                daily_plans__plan_date=work_date,
                daily_plans__is_deleted=False,
            )
        )
        .select_related("category")
        .annotate(priority_rank=priority_rank_expression())
        .order_by("priority_rank", "due_date", "due_time")
        .distinct()
        [:AI_CONTEXT_TASK_LIMIT]
    )
    snapshots = tuple(_snapshot(task) for task in tasks)
    employee_name = user.get_full_name().strip() or user.get_username()
    return EmployeeDayContext(
        employee_id=user.pk,
        employee_name=employee_name,
        work_date=work_date,
        generated_at=timezone.now(),
        timezone=str(timezone.get_current_timezone()),
        focus_area=plan.focus_area if plan else "",
        tasks=snapshots,
        top_priority_ids=(tuple(task.pk for task in plan.top_priorities.all()) if plan else ()),
        metadata={
            "schema_version": "1.0",
            "source": "boforg_tasker",
            "task_limit": AI_CONTEXT_TASK_LIMIT,
        },
    )


def _snapshot(task):
    estimate = (
        round(task.estimated_duration.total_seconds() / 60)
        if task.estimated_duration
        else None
    )
    return TaskSnapshot(
        id=task.pk,
        title=task.title,
        description=task.description[:AI_CONTEXT_DESCRIPTION_LIMIT],
        priority=task.priority,
        status=task.status,
        progress=task.progress,
        due_date=task.due_date,
        due_time=task.due_time.isoformat(timespec="minutes") if task.due_time else None,
        estimated_minutes=estimate,
        category=task.category.name if task.category else None,
        carry_forward=task.carry_forward,
    )
