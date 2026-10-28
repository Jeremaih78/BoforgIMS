"""Bounded, owner-scoped work facts for the personal productivity assistant.

Scratchpad text, task notes and private reflections are deliberately excluded.
Tasks occur once; today's work and carry-over collections reference their IDs.
"""

import json
from datetime import timedelta

from django.db.models import BooleanField, Case, Count, F, IntegerField, Q, Sum, Value, When
from django.utils import timezone

from tasker.models import DailyPlan, Task, TaskTimeEntry
from tasker.selectors import priority_rank_expression

from .exceptions import AIConfigurationError
from .settings import AIEnvironmentSettings


ACTIVE_STATUSES = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
EXECUTABLE_STATUSES = (Task.Status.PENDING, Task.Status.IN_PROGRESS)
MAX_WORKLOAD_TASKS = 75
MAX_COMPLETED_TASKS = 50
PROMPT_ENVELOPE_CHARACTERS = 4096


def _minutes(value):
    return round(value.total_seconds() / 60) if value else None


def _checklist_counts(queryset):
    return queryset.annotate(
        checklist_total=Count("checklist_items", filter=Q(checklist_items__is_deleted=False)),
        checklist_completed=Count("checklist_items", filter=Q(
            checklist_items__is_deleted=False, checklist_items__is_completed=True)),
    )


def _task_payload(task, *, now):
    overdue = bool(task.due_date and (
        task.due_date < now.date()
        or (task.due_date == now.date() and task.due_time and task.due_time < now.time())
    ))
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
        "is_overdue": overdue,
        "is_planned_today": getattr(task, "is_planned_today", False),
        "is_top_priority": getattr(task, "is_top_priority", False),
        "is_current_focus": getattr(task, "is_current_focus", False),
        "is_best_executable": getattr(task, "is_best_executable", False),
        "checklist": {"total": task.checklist_total, "completed": task.checklist_completed},
    }


def _refresh_references(context):
    tasks = context["current_workload"]
    context["today_task_ids"] = [item["id"] for item in tasks if (
        item["is_planned_today"] or item["is_top_priority"] or item["carry_forward"]
        or (item["due_date"] and item["due_date"] <= context["date"])
    )]
    context["carry_over_task_ids"] = [item["id"] for item in tasks if item["carry_forward"]]
    context["daily_plan"]["top_priority_ids"] = [item["id"] for item in tasks if item["is_top_priority"]]
    context["daily_plan"]["planned_task_ids"] = [item["id"] for item in tasks if item["is_planned_today"]]
    context["workload"]["included_task_count"] = len(tasks)
    context["workload"]["omitted_task_count"] = context["workload"]["active_task_count"] - len(tasks)
    mandatory_included = sum(bool(item["is_current_focus"] or item["is_best_executable"] or item["is_top_priority"]
                                  or item["priority"] == Task.Priority.CRITICAL) for item in tasks)
    context["context_limits"]["omitted_mandatory_task_count"] = (
        context["context_limits"]["mandatory_task_count"] - mandatory_included
    )
    context["context_limits"]["omitted_completed_task_count"] = (
        context["workload"]["completed_today_count"] - len(context["completed_today"])
    )


def _fit_context(context, max_characters):
    """Compact text before omitting records; preserve focus/commitments first."""
    limits = context["context_limits"]
    limits["max_characters"] = max_characters
    for description_limit in (2000, 512, 128, 0):
        for item in context["current_workload"] + context["completed_today"]:
            item["description"] = item["description"][:description_limit]
        limits["description_character_limit"] = description_limit
        _refresh_references(context)
        if len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= max_characters:
            return context
    # Preserve an executable candidate while omitting less useful facts. An
    # insufficient input budget must never turn real ready work into a false
    # "no ready tasks" conclusion. Other omissions remain explicit in totals.
    while context["completed_today"] or context["current_workload"]:
        if context["completed_today"]:
            context["completed_today"].pop()
        else:
            removable = next((index for index in range(len(context["current_workload"]) - 1, -1, -1)
                              if not context["current_workload"][index]["is_best_executable"]), None)
            if removable is None:
                raise AIConfigurationError("The AI input budget cannot include the required executable task.")
            context["current_workload"].pop(removable)
        _refresh_references(context)
        if len(json.dumps(context, ensure_ascii=False, separators=(",", ":"))) <= max_characters:
            return context
    raise AIConfigurationError("The AI input budget is too small for a safe context envelope.")


def build_personal_context(*, user, max_characters=None):
    """Return true workload totals and a bounded selection of employee work."""
    now = timezone.localtime()
    today = now.date()
    if max_characters is None:
        max_characters = AIEnvironmentSettings.load().max_prompt_characters - PROMPT_ENVELOPE_CHARACTERS
    if max_characters <= 0:
        raise AIConfigurationError("The AI input budget must allow a context envelope.")
    plan = DailyPlan.objects.filter(owner=user, plan_date=today).first()
    active = Task.objects.filter(owner=user, archived=False, status__in=ACTIVE_STATUSES)
    top = active.filter(priority_daily_plans=plan) if plan else active.none()
    planned = active.filter(daily_plans=plan) if plan else active.none()
    top_ids = top.values("pk")
    planned_ids = planned.values("pk")
    running = TaskTimeEntry.objects.filter(
        user=user, task__owner=user, task__is_deleted=False, task__archived=False,
        task__status__in=EXECUTABLE_STATUSES, ended_at__isnull=True,
    ).first()
    focus_id = running.task_id if running else None
    mandatory = Q(priority=Task.Priority.CRITICAL) | Q(pk__in=top_ids)
    if focus_id:
        mandatory |= Q(pk=focus_id)
    totals = active.aggregate(
        active_task_count=Count("id"),
        executable_task_count=Count("id", filter=Q(status__in=EXECUTABLE_STATUSES)),
        blocked_task_count=Count("id", filter=Q(status=Task.Status.BLOCKED)),
        overdue_task_count=Count("id", filter=(Q(due_date__lt=today)
            | Q(due_date=today, due_time__lt=now.time()))),
        estimated_duration_sum=Sum("estimated_duration"),
        unestimated_task_count=Count("id", filter=(Q(estimated_duration__isnull=True)
            | Q(estimated_duration=timedelta(0)))),
    )
    estimated_duration = totals.pop("estimated_duration_sum")
    totals["estimated_minutes"] = _minutes(estimated_duration) or 0
    # Reserve one hard-cap slot for the best executable candidate according to
    # the same focus/commitment/priority/deadline policy as the full selection.
    # A large blocked-critical backlog must not hide all useful ready work.
    best_executable_id = active.filter(status__in=EXECUTABLE_STATUSES).annotate(
        priority_rank=priority_rank_expression(),
        selection_rank=Case(
            When(pk=focus_id, then=Value(0)),
            When(pk__in=top_ids, then=Value(1)),
            When(priority=Task.Priority.CRITICAL, then=Value(2)),
            When(pk__in=planned_ids, then=Value(3)),
            default=Value(4), output_field=IntegerField(),
        ),
    ).order_by("selection_rank", "priority_rank", F("due_date").asc(nulls_last=True),
               F("due_time").asc(nulls_last=True), "created_at", "pk").values_list("pk", flat=True).first()
    if best_executable_id:
        mandatory |= Q(pk=best_executable_id)
    selected = _checklist_counts(active).select_related("category").annotate(
        priority_rank=priority_rank_expression(),
        selection_rank=Case(
            When(pk=focus_id, then=Value(0)),
            When(pk=best_executable_id, then=Value(1)),
            When(pk__in=top_ids, then=Value(2)),
            When(priority=Task.Priority.CRITICAL, then=Value(3)),
            When(pk__in=planned_ids, then=Value(4)),
            default=Value(5), output_field=IntegerField(),
        ),
        is_current_focus=Case(When(pk=focus_id, then=Value(True)), default=Value(False), output_field=BooleanField()),
        is_best_executable=Case(When(pk=best_executable_id, then=Value(True)), default=Value(False), output_field=BooleanField()),
        is_top_priority=Case(When(pk__in=top_ids, then=Value(True)), default=Value(False), output_field=BooleanField()),
        is_planned_today=Case(When(pk__in=planned_ids, then=Value(True)), default=Value(False), output_field=BooleanField()),
    ).order_by("selection_rank", "priority_rank", F("due_date").asc(nulls_last=True),
               F("due_time").asc(nulls_last=True), "created_at", "pk")[:MAX_WORKLOAD_TASKS]
    completed = Task.objects.filter(owner=user, archived=False, completed_at__date=today,
                                    status=Task.Status.COMPLETED)
    totals["completed_today_count"] = completed.count()
    context = {
        "schema_version": "2",
        "scope": "authenticated_employee_only",
        "date": today.isoformat(),
        "timezone": str(timezone.get_current_timezone()),
        "employee": {"id": user.pk, "name": (user.get_full_name() or user.get_username())[:255]},
        "workload": totals,
        "daily_plan": {"exists": bool(plan), "focus_area": plan.focus_area if plan else "",
                       "top_priority_count": top.count(), "planned_task_count": planned.count()},
        "focus_mode": {"active": bool(running), "task_id": focus_id,
                       "started_at": running.started_at.isoformat() if running else None},
        "current_workload": [_task_payload(task, now=now) for task in selected],
        "completed_today": [_task_payload(task, now=now) for task in
            _checklist_counts(completed).select_related("category").order_by("-completed_at", "pk")[:MAX_COMPLETED_TASKS]],
        "context_limits": {"task_limit": MAX_WORKLOAD_TASKS, "mandatory_task_count": active.filter(mandatory).count(),
                           "private_content_included": False,
                           "selection_policy": "focus, protected best executable, top commitments, critical, planned, semantic priority/deadline"},
    }
    context = _fit_context(context, max_characters)
    if totals["executable_task_count"] and not any(
            item["status"] in EXECUTABLE_STATUSES for item in context["current_workload"]):
        raise AIConfigurationError("The AI input budget cannot include an executable task safely.")
    return context


def build_personal_task_context(*, user, task):
    if task.owner_id != user.pk or task.is_deleted:
        raise PermissionError("Personal AI cannot access an unavailable task.")
    checklist = list(task.checklist_items.values("id", "title", "position", "is_completed")[:50])
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
        "omitted_checklist_count": max(0, task.checklist_items.count() - len(checklist)),
    }
