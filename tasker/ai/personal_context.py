"""Privacy-bounded context assembly for the personal productivity assistant."""

from datetime import timedelta

from django.db.models import Count, Q
from django.utils import timezone

from tasker.models import DailyPlan, Task, TaskTimeEntry


ACTIVE_STATUSES = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
MAX_WORKLOAD_TASKS = 75


def _minutes(value):
    return round(value.total_seconds() / 60) if value else None


def _task_payload(task, *, today, planned_ids=frozenset(), priority_ids=frozenset()):
    return {
        "id": task.pk,
        "title": task.title,
        "description": (task.description or "")[:2000],
        "priority": task.priority,
        "status": task.status,
        "progress_percent": task.progress,
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_time": task.due_time.isoformat(timespec="minutes") if task.due_time else None,
        "estimated_minutes": _minutes(task.estimated_duration),
        "actual_minutes": _minutes(task.actual_duration),
        "category": task.category.name if task.category else None,
        "carry_forward": task.carry_forward,
        "is_overdue": bool(task.due_date and task.due_date < today),
        "is_planned_today": task.pk in planned_ids,
        "is_top_priority": task.pk in priority_ids,
        "checklist": {
            "total": task.checklist_total,
            "completed": task.checklist_completed,
        },
    }


def build_personal_context(*, user):
    """Return only the authenticated employee's own productivity data."""
    today = timezone.localdate()
    plan = (
        DailyPlan.objects.filter(owner=user, plan_date=today)
        .prefetch_related("planned_tasks", "top_priorities", "review__carry_over_tasks")
        .first()
    )
    planned_ids = {task.pk for task in plan.planned_tasks.all()} if plan else set()
    priority_ids = {task.pk for task in plan.top_priorities.all()} if plan else set()

    tasks = list(
        Task.objects.filter(owner=user, archived=False, status__in=ACTIVE_STATUSES)
        .select_related("category")
        .annotate(
            checklist_total=Count("checklist_items"),
            checklist_completed=Count(
                "checklist_items", filter=Q(checklist_items__is_completed=True)
            ),
        )
        .order_by("due_date", "due_time", "-priority", "created_at")[:MAX_WORKLOAD_TASKS]
    )
    task_payloads = [
        _task_payload(task, today=today, planned_ids=planned_ids, priority_ids=priority_ids)
        for task in tasks
    ]
    today_tasks = [
        item for item in task_payloads
        if item["is_planned_today"] or item["is_top_priority"] or item["carry_forward"]
        or (item["due_date"] and item["due_date"] <= today.isoformat())
    ]
    completed = list(
        Task.objects.filter(owner=user, completed_at__date=today, status=Task.Status.COMPLETED)
        .select_related("category")
        .annotate(
            checklist_total=Count("checklist_items"),
            checklist_completed=Count(
                "checklist_items", filter=Q(checklist_items__is_completed=True)
            ),
        )[:50]
    )
    running = (
        TaskTimeEntry.objects.filter(user=user, task__owner=user, ended_at__isnull=True)
        .select_related("task")
        .first()
    )
    review = None
    if plan:
        try:
            saved_review = plan.review
        except DailyPlan.review.RelatedObjectDoesNotExist:
            saved_review = None
        if saved_review:
            review = {
                "accomplishments": saved_review.accomplishments[:3000],
                "energy_level": saved_review.energy_level,
                "most_productive_time": saved_review.most_productive_time,
                "biggest_distraction": saved_review.biggest_distraction[:2000],
                "lessons_learned": saved_review.lessons_learned[:3000],
                "tomorrow_focus": saved_review.tomorrow_focus[:2000],
                "carry_over_task_ids": [task.pk for task in saved_review.carry_over_tasks.all()],
            }

    estimates = [item["estimated_minutes"] for item in task_payloads if item["estimated_minutes"]]
    return {
        "scope": "authenticated_employee_only",
        "date": today.isoformat(),
        "timezone": str(timezone.get_current_timezone()),
        "employee": {"id": user.pk, "name": user.get_full_name() or user.get_username()},
        "workload": {
            "active_task_count": len(task_payloads),
            "blocked_task_count": sum(item["status"] == Task.Status.BLOCKED for item in task_payloads),
            "overdue_task_count": sum(item["is_overdue"] for item in task_payloads),
            "estimated_minutes": sum(estimates),
        },
        "daily_plan": {
            "exists": bool(plan),
            "focus_area": plan.focus_area if plan else "",
            "motivational_quote": plan.motivational_quote if plan else "",
            "quick_notes": plan.quick_notes[:3000] if plan else "",
            "top_priority_ids": list(priority_ids),
            "planned_task_ids": list(planned_ids),
        },
        "focus_mode": {
            "active": bool(running),
            "task_id": running.task_id if running else None,
            "task_title": running.task.title if running else None,
            "started_at": running.started_at.isoformat() if running else None,
        },
        "today_tasks": today_tasks,
        "carry_over_tasks": [item for item in task_payloads if item["carry_forward"]],
        "current_workload": task_payloads,
        "completed_today": [
            _task_payload(task, today=today) for task in completed
        ],
        "daily_review": review,
    }


def build_personal_task_context(*, user, task):
    if task.owner_id != user.pk:
        raise PermissionError("Personal AI cannot access another employee's task.")
    checklist = list(
        task.checklist_items.values("id", "title", "position", "is_completed")[:50]
    )
    return {
        "id": task.pk,
        "title": task.title,
        "description": (task.description or "")[:4000],
        "priority": task.priority,
        "status": task.status,
        "progress_percent": task.progress,
        "estimated_minutes": _minutes(task.estimated_duration),
        "actual_minutes": _minutes(task.actual_duration),
        "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_time": task.due_time.isoformat(timespec="minutes") if task.due_time else None,
        "blocked_reason": (task.blocked_reason or "")[:2000],
        "checklist": checklist,
    }
