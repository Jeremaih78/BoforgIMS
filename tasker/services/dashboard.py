from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from django.db import transaction
from django.db.models import Count, F, Q, QuerySet
from django.utils import timezone

from tasker.models import DailyPlan, Task
from tasker.selectors import priority_rank_expression


ACTIVE_STATUSES = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)


@dataclass(frozen=True)
class TodayDashboardData:
    date: date
    greeting: str
    completed_count: int
    pending_count: int
    total_count: int
    progress_percent: int
    top_priorities: tuple[Task, ...]
    timeline: tuple[Task, ...]
    carry_over_tasks: tuple[Task, ...]
    quick_notes: str
    has_daily_review: bool


def build_today_dashboard(*, user, today=None) -> TodayDashboardData:
    """Return the current user's dashboard using a bounded set of optimized queries."""
    today = today or timezone.localdate()
    owned_tasks = Task.objects.filter(owner=user, archived=False).select_related("category")
    today_tasks = _today_tasks(owned_tasks, user=user, today=today)

    counts = today_tasks.aggregate(
        total=Count("pk"),
        completed=Count("pk", filter=Q(status=Task.Status.COMPLETED)),
        pending=Count("pk", filter=Q(status__in=ACTIVE_STATUSES)),
    )
    total_count = counts["total"]
    completed_count = counts["completed"]
    pending_count = counts["pending"]
    progress_percent = round((completed_count / total_count) * 100) if total_count else 0

    priority_rank = priority_rank_expression()
    actionable = owned_tasks.filter(status__in=ACTIVE_STATUSES)
    top_priorities = tuple(
        actionable.filter(
            Q(due_date__lte=today)
            | Q(carry_forward=True)
            | Q(
                priority_daily_plans__owner=user,
                priority_daily_plans__plan_date=today,
                priority_daily_plans__is_deleted=False,
            )
        )
        .annotate(priority_rank=priority_rank)
        .order_by("priority_rank", F("due_date").asc(nulls_last=True), F("due_time").asc(nulls_last=True))
        .distinct()[:4]
    )
    timeline = tuple(
        today_tasks.annotate(timeline_priority_rank=priority_rank).order_by(
            F("due_time").asc(nulls_last=True), "timeline_priority_rank", "created_at"
        )[:50]
    )
    carry_over_tasks = tuple(
        actionable.filter(carry_forward=True)
        .annotate(priority_rank=priority_rank)
        .order_by("priority_rank", F("due_date").asc(nulls_last=True))[:5]
    )

    plan = (
        DailyPlan.objects.filter(owner=user, plan_date=today)
        .select_related("review")
        .first()
    )
    return TodayDashboardData(
        date=today,
        greeting=_greeting(),
        completed_count=completed_count,
        pending_count=pending_count,
        total_count=total_count,
        progress_percent=progress_percent,
        top_priorities=top_priorities,
        timeline=timeline,
        carry_over_tasks=carry_over_tasks,
        quick_notes=plan.quick_notes if plan else "",
        has_daily_review=bool(plan and hasattr(plan, "review")),
    )


def create_quick_task(*, user, title, priority, due_time=None, category=None, estimated_duration=None):
    """Create a task scheduled for today with complete assignment/audit information."""
    return Task.objects.create(
        title=title.strip(),
        owner=user,
        assigned_by=user,
        priority=priority,
        due_date=timezone.localdate(),
        due_time=due_time,
        category=category,
        estimated_duration=estimated_duration,
        created_by=user,
        updated_by=user,
    )


@transaction.atomic
def save_quick_notes(*, user, notes):
    """Persist today's scratchpad without creating records during dashboard reads."""
    today = timezone.localdate()
    plan, _ = DailyPlan.objects.update_or_create(
        owner=user,
        plan_date=today,
        defaults={"quick_notes": notes, "updated_by": user},
        create_defaults={
            "quick_notes": notes,
            "created_by": user,
            "updated_by": user,
        },
    )
    return plan


def _today_tasks(queryset: QuerySet, *, user, today):
    return queryset.exclude(status=Task.Status.CANCELLED).filter(
        Q(due_date=today)
        | Q(
            daily_plans__owner=user,
            daily_plans__plan_date=today,
            daily_plans__is_deleted=False,
        )
    ).distinct()


def _greeting():
    hour = timezone.localtime().hour
    if hour < 12:
        return "Good morning"
    if hour < 17:
        return "Good afternoon"
    return "Good evening"
