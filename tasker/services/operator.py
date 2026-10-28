"""One deterministic work operator, with hard gates before explainable ranking.

This service never starts a timer, changes a plan, sends a message, or invokes AI.
Evidence contains IDs, opaque source proofs and policy facts, never note bodies.
"""
from datetime import datetime, timedelta
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, Count, F, IntegerField, Q, Prefetch, Value, When
from django.utils import timezone

from tasker.models import DailyPlan, Task, TaskChecklistItem, TaskTimeEntry
from tasker.operator_models import (
    DEFAULT_WEIGHTS, OperatorOverride, OperatorPolicy, OperatorSnapshot,
    OperatorState, validate_operator_weights,
)
from tasker.organization import visible_companies_for, visible_teams_for
from tasker.policies import visible_tasks_for
from tasker.services.capacity import capacity_for_day, interval_intersections, interval_subtract, policy_for
from tasker.services.dependencies import readiness_for_tasks
from tasker.services.effort import effort_for_task
from tasker.services.workbench import work_version
from tasker.selectors import priority_rank_expression


POLICY_VERSION = "tasker-operator-v1"
DEFAULT_POLICY_REVISION = "default-v1"
SLICE_MINUTES = 25
SNAPSHOT_SECONDS = 300
CANDIDATE_LIMIT = 100
FAIRNESS_DAYS = 7
ACTIVE = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)


class OperatorConflict(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if current is None or not current.has_perm("tasker.view_task"):
        raise PermissionDenied
    return current


def operator_policy_for(user):
    record = OperatorPolicy.objects.filter(owner=user).first()
    weights = dict(record.weights) if record else dict(DEFAULT_WEIGHTS)
    validate_operator_weights(weights)
    return {"version": POLICY_VERSION, "revision": str(record.revision) if record else DEFAULT_POLICY_REVISION,
            "weights": weights, "slice_minutes": SLICE_MINUTES, "expires_seconds": SNAPSHOT_SECONDS,
            "fairness_days": FAIRNESS_DAYS, "candidate_limit": CANDIDATE_LIMIT, "critical_first": True}


@transaction.atomic
def save_operator_policy(*, user, weights, expected_revision=DEFAULT_POLICY_REVISION):
    actor = _actor(user)
    actor = get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    if not actor.has_perm("tasker.change_task"):
        raise PermissionDenied
    validate_operator_weights(weights)
    record = OperatorPolicy.objects.select_for_update().filter(owner=actor).first()
    previous = dict(record.weights) if record else dict(DEFAULT_WEIGHTS)
    revision = str(record.revision) if record else DEFAULT_POLICY_REVISION
    if str(expected_revision) != revision:
        raise OperatorConflict("Your ranking settings changed. Reload before saving.")
    if record is None:
        record = OperatorPolicy(owner=actor)
    record.weights, record.revision = dict(weights), uuid.uuid4()
    record.save()
    OperatorOverride.objects.create(owner=actor, action="POLICY_CHANGED", reason="Operator ranking weights updated.",
        details={"previous_revision": revision, "revision": str(record.revision), "before": previous, "after": weights})
    return record


def _candidates(user, running_id):
    """Four bounded cohorts preserve undated critical and old strategic work."""
    base = visible_tasks_for(user).filter(owner=user, archived=False, status__in=ACTIVE)
    total = base.count()
    ids = []
    if running_id:
        ids.extend(base.filter(pk=running_id).values_list("pk", flat=True))
    cohorts = (
        # Known blocked records belong in Waiting and cannot consume every
        # reserved critical slot ahead of newly executable undated work.
        base.filter(priority=Task.Priority.CRITICAL).annotate(operator_waiting=Case(
            When(status=Task.Status.BLOCKED, then=Value(1)), default=Value(0), output_field=IntegerField()
        )).order_by("operator_waiting", "created_at", "pk"),
        base.filter(primary_contribution__is_deleted=False, primary_contribution__initiative__is_deleted=False,
                    primary_contribution__initiative__goal__is_deleted=False,
                    primary_contribution__initiative__goal__status="ACTIVE").order_by("created_at", "pk"),
        base.order_by("created_at", "pk"),
        base.annotate(operator_priority=priority_rank_expression()).order_by("operator_priority", F("due_date").asc(nulls_last=True), "pk"),
    )
    for cohort, quota in zip(cohorts, (40, 20, 20, 20)):
        chosen = cohort.exclude(pk__in=ids).values_list("pk", flat=True)[:min(quota, CANDIDATE_LIMIT-len(ids))]
        ids.extend(chosen)
    if len(ids) < CANDIDATE_LIMIT:
        ids.extend(base.exclude(pk__in=ids).annotate(operator_priority=priority_rank_expression()).order_by(
            "operator_priority", F("due_date").asc(nulls_last=True), "created_at", "pk").values_list("pk", flat=True)[:CANDIDATE_LIMIT-len(ids)])
    tasks = list(base.filter(pk__in=ids).select_related("remaining_effort_estimate").prefetch_related(
        Prefetch("checklist_items", queryset=TaskChecklistItem.objects.filter(is_completed=False).order_by("position", "pk")[:1], to_attr="operator_steps")))
    return tasks, total


def _scope_access(user, tasks):
    companies = {task.company_id for task in tasks if task.company_id}
    teams = {task.team_id for task in tasks if task.team_id}
    allowed_companies = set(visible_companies_for(user).filter(pk__in=companies).values_list("pk", flat=True)) if companies else set()
    allowed_teams = set(visible_teams_for(user).filter(pk__in=teams, company__is_active=True).values_list("pk", flat=True)) if teams else set()
    # Ownership alone cannot turn a revoked membership into executable work.
    from tasker.models import TeamMembership
    memberships = set(TeamMembership.objects.filter(user=user, is_active=True, team_id__in=teams,
        team__is_deleted=False, team__company__is_active=True).values_list("team_id", flat=True)) if teams else set()
    return {task.pk: ((not task.company_id or task.company_id in allowed_companies)
                     and (not task.team_id or task.team_id in allowed_teams and task.team_id in memberships)) for task in tasks}


def _goal_links(user, tasks):
    from tasker.goal_models import TaskContribution
    from tasker.services.goals import visible_initiatives_for
    rows = TaskContribution.objects.filter(task_id__in=[task.pk for task in tasks], initiative__in=visible_initiatives_for(user),
        initiative__goal__status="ACTIVE").values_list("task_id", "initiative__goal_id")
    return dict(rows)


def _unlocked_work(user, tasks):
    from tasker.dependency_models import TaskDependency
    from tasker.models import TeamMembership
    visible = visible_tasks_for(user).filter(archived=False, status__in=ACTIVE)
    current_memberships = TeamMembership.objects.filter(user=user, is_active=True,
        team__is_deleted=False, team__company__is_active=True).values("team_id")
    # Ownership keeps a source reachable for review; it does not by itself
    # authorize using a revoked company/team assignment as ranking evidence.
    visible = visible.filter(
        Q(company__isnull=True) | Q(company__in=visible_companies_for(user))
    ).filter(
        Q(team__isnull=True) | Q(team__in=visible_teams_for(user))
    ).filter(
        ~Q(owner=user) | Q(team__isnull=True) | Q(team_id__in=current_memberships)
    )
    return dict(TaskDependency.objects.filter(prerequisite_id__in=[task.pk for task in tasks], task__in=visible).values(
        "prerequisite_id").annotate(total=Count("task_id", distinct=True)).values_list("prerequisite_id", "total"))


def _remaining_window(intervals, now):
    return max(((right-now).total_seconds()/60 for left, right in intervals if left <= now < right), default=0)


def _capacity_at(user, now):
    zone = ZoneInfo("Africa/Harare")
    try:
        policy = policy_for(user)
        zone = ZoneInfo(policy.timezone_name if policy else "Africa/Harare")
        today = now.astimezone(zone).date()
        work = capacity_for_day(user=user, day=today)
        return work, zone, today
    except (ValidationError, ZoneInfoNotFoundError):
        # Some valid calendar zones have missing/ambiguous midnight boundaries.
        # Keep guidance usable while refusing to invent capacity for that day.
        today = now.astimezone(zone).date()
        work = {"known": False, "timezone_name": zone.key, "day": today,
                "work_minutes": None, "excluded_minutes": None, "leave_minutes": None,
                "commitment_minutes": None, "buffer_minutes": None, "usable_minutes": None,
                "committed_minutes": None, "available_minutes": None, "overload_minutes": None,
                "work_intervals": [], "free_intervals": [], "booked_intervals": [],
                "warnings": ["Capacity needs review because the local calendar boundary or timezone is unavailable."],
                "version": _digest(["capacity_unavailable", zone.key, str(today)])}
        return work, zone, today


def _candidate(task, readiness, effort):
    first_item = task.operator_steps[0] if task.operator_steps else None
    first_step = first_item.title if first_item else task.title
    return {"task": task, "task_id": task.pk, "task_version": work_version(task),
            "dependency_version": readiness.version, "effort_version": effort["version"],
            "first_action_version": _digest([first_item.pk, first_item.title, first_item.position] if first_item else [None, task.title]),
            "first_action": first_step, "definition_of_done": task.definition_of_done,
            "effort_label": effort["label"], "remaining_minutes": effort["remaining_minutes"],
            "slice_minutes": None, "reason": "", "reasons": [], "score": 0,
            "is_active_focus": False, "can_start": False, "queue": "DECISIONS"}


def _decision(candidate, code, message, action="Review task"):
    return {**candidate, "code": code, "reason": message, "reasons": [message], "action": action, "queue": "DECISIONS"}


def _rank(candidate, *, today, now, weights, goal_id, unlocked, committed, last_category):
    task = candidate["task"]
    age = max(0, (today-task.created_at.astimezone(now.tzinfo).date()).days)
    remaining = candidate["remaining_minutes"]
    deadline = None
    if task.due_date:
        days = (task.due_date-today).days
        deadline = 5 if days < 0 else 4 if days == 0 else 3 if days == 1 else 2 if days <= 3 else 1 if days <= 7 else 0
    urgency = {Task.Priority.CRITICAL: 4, Task.Priority.HIGH: 3, Task.Priority.MEDIUM: 2, Task.Priority.LOW: 1}[task.priority]
    short_effort = max(0, 1-remaining/SLICE_MINUTES) if remaining is not None else None
    context = 1 if last_category is not None and task.category_id == last_category else 0
    features = {"goal": 1 if goal_id else None, "deadline": deadline, "urgency": urgency,
                "unlock": unlocked, "age": age, "effort": round(short_effort, 3) if short_effort is not None else None, "context": context}
    score = sum(weights[key]*value for key, value in features.items() if value is not None)
    # A protected lane after a week prevents a stream of fresh small jobs from
    # hiding older strategic/long work within the same critical/noncritical tier.
    fair = age >= FAIRNESS_DAYS and (goal_id is not None or remaining is not None and remaining > SLICE_MINUTES)
    reasons = [f"{task.get_priority_display()} priority"]
    if deadline is not None:
        reasons.append("Deadline is overdue" if task.due_date < today else "Deadline today" if task.due_date == today else f"Deadline in {(task.due_date-today).days} days")
    if goal_id: reasons.append("Contributes to an active company goal")
    if unlocked: reasons.append(f"Prerequisite for {unlocked} active task{'s' if unlocked != 1 else ''}")
    if fair: reasons.append("Protected progress on older strategic or long work")
    if age: reasons.append(f"Open for {age} day{'s' if age != 1 else ''}")
    if committed: reasons.append("Included in today's agreed intentions")
    if context: reasons.append("Keeps the same work context")
    reasons.append(f"A {candidate['slice_minutes']}-minute work slice fits current capacity")
    candidate.update(score=round(score, 3), features=features, reasons=reasons, reason=" · ".join(reasons),
                     goal_id=goal_id, goal_impact_known=goal_id is not None, fairness_protected=fair)
    return (task.priority == Task.Priority.CRITICAL, fair, score, -task.created_at.timestamp(), -task.pk)


def build_operator(*, user, now=None, persist=True, exclude_task=None):
    """Return current guidance. Persisted evidence is private to its employee.

    A valid running focus is sticky; urgent new critical work becomes an
    explicit decision and never interrupts or switches a timer automatically.
    """
    actor = _actor(user)
    now = now or timezone.now()
    if not isinstance(now, datetime) or timezone.is_naive(now):
        raise ValidationError("Operator evaluation requires a timezone-aware instant.")
    config = operator_policy_for(actor)
    if persist:
        state, _ = OperatorState.objects.get_or_create(owner=actor)
    else:
        state = OperatorState.objects.filter(owner=actor).first()
    initial_revision = state.revision if state else None
    work, zone, today = _capacity_at(actor, now)
    local_now = now.astimezone(zone)
    running = TaskTimeEntry.objects.filter(user=actor, ended_at__isnull=True).select_related("task").first()
    tasks, total = _candidates(actor, running.task_id if running else None)
    readiness = readiness_for_tasks(tasks=tasks, user=actor)
    access = _scope_access(actor, tasks)
    goals = _goal_links(actor, tasks)
    unlocked = _unlocked_work(actor, tasks)
    plan = DailyPlan.objects.filter(owner=actor, plan_date=today).first()
    commitments = set(plan.top_priorities.values_list("pk", flat=True)) if plan else set()
    if plan: commitments.update(plan.planned_tasks.values_list("pk", flat=True))
    last_entry = running or TaskTimeEntry.objects.filter(user=actor).select_related("task").order_by("-started_at", "-pk").first()
    last_category = last_entry.task.category_id if last_entry else None
    from tasker.services.capacity import active_schedule_blocks
    day_end = max((right for _, right in work["work_intervals"]), default=now)
    # Future reservations can truncate the work slice starting now.
    blocks = list(active_schedule_blocks(actor).filter(starts_at__lt=day_end, ends_at__gt=now).order_by("pk"))
    running_rows = {}
    for task_id, pk, start in TaskTimeEntry.objects.filter(task_id__in=[task.pk for task in tasks], ended_at__isnull=True).values_list("task_id", "pk", "started_at"):
        running_rows.setdefault(task_id, []).append((pk, start))
    unbooked = interval_subtract(work["free_intervals"], work["booked_intervals"])
    free_window = _remaining_window(unbooked, now)
    work_window = _remaining_window(work["free_intervals"], now)
    has_capability = actor.has_perm("tasker.change_task")
    excluded = {"omitted_by_limit": max(0, total-len(tasks)), "restricted": 0, "blocked": 0,
                "dependencies": 0, "hours": 0, "capacity": 0, "effort": 0, "capability": 0, "employee_choice": 0}
    waiting, decisions, eligible, evidence = [], [], [], []
    current_focus = None
    excluded_id = getattr(exclude_task, "pk", exclude_task)
    for task in tasks:
        links = running_rows.get(task.pk, [])
        logged = (max(0, int(task.actual_duration.total_seconds())) if task.actual_duration else 0) + sum(max(0, int((now-start).total_seconds())) for _, start in links)
        effort = effort_for_task(task=task, now=now, logged=(logged, links))
        candidate = _candidate(task, readiness[task.pk], effort)
        is_running = running is not None and running.task_id == task.pk
        candidate["is_active_focus"] = is_running
        if is_running: current_focus = candidate
        code = message = None
        if not access[task.pk]:
            excluded["restricted"] += 1
            if is_running:
                decisions.append({"task": None, "task_id": None, "code": "FOCUS_RESTRICTED", "reason": "Your running timer needs review because access changed. Stop your own timer from Work.", "action": "Open Work", "queue": "DECISIONS"})
                current_focus = None
            # Do not expose stale source titles when current scope was revoked.
            evidence.append({"task_id": task.pk, "queue": "EXCLUDED", "code": "RESTRICTED", "task_version": candidate["task_version"]})
            continue
        if not has_capability:
            code, message = "CAPABILITY", "Task editing permission is needed to start work."
            excluded["capability"] += 1
        elif task.status == Task.Status.BLOCKED:
            excluded["blocked"] += 1
            candidate.update(queue="WAITING", code="BLOCKED", reason="Resolve the recorded blocker before starting this task.", reasons=["Task is blocked"], action="Review blocker")
            waiting.append(candidate)
        elif not readiness[task.pk].ready:
            excluded["dependencies"] += 1
            candidate.update(queue="WAITING", code="DEPENDENCIES", reason=readiness[task.pk].reason, reasons=[readiness[task.pk].reason], action="Review prerequisites")
            waiting.append(candidate)
        elif not work["known"]:
            excluded["hours"] += 1
            code, message = "HOURS_UNKNOWN", "Set working hours and an interruption buffer to assess a feasible work slice."
        elif work_window <= 0:
            excluded["hours"] += 1
            code, message = "OUTSIDE_HOURS", "Current time is outside available working hours or within leave/a fixed commitment."
        elif work["overload_minutes"]:
            excluded["capacity"] += 1
            code, message = "OVERCAPACITY", "Review the overloaded plan before starting more work."
        elif effort["remaining_minutes"] is None and not is_running:
            excluded["effort"] += 1
            code, message = "EFFORT_UNKNOWN", "Estimate the remaining effort before choosing a feasible work slice."
        elif effort["remaining_minutes"] == 0 and not is_running:
            excluded["effort"] += 1
            code, message = "OUTCOME_REVIEW", "Review the outcome or update remaining effort; zero minutes does not accept completion."
        elif task.pk == excluded_id and not is_running:
            excluded["employee_choice"] += 1
            code, message = "EMPLOYEE_CHOICE", "Set aside for this review; the task remains available from Work."
        else:
            own_blocks = [block for block in blocks if block.task_id == task.pk and block.starts_at <= now < block.ends_at]
            other_blocks = [block for block in blocks if block.task_id != task.pk]
            # An accepted block consumes its own reservation, never another
            # task's or an exclusion's. No extra capacity is invented.
            reserved_window = 0
            if own_blocks:
                own_intervals = interval_intersections([(block.starts_at, block.ends_at) for block in own_blocks], work["free_intervals"])
                reserved_window = _remaining_window(interval_subtract(own_intervals,
                    [(block.starts_at, block.ends_at) for block in other_blocks]), now)
            available = max(min(free_window, work["available_minutes"] or 0), reserved_window)
            slice_minutes = min(SLICE_MINUTES, effort["remaining_minutes"]) if effort["remaining_minutes"] else SLICE_MINUTES
            if available < slice_minutes:
                excluded["capacity"] += 1
                code, message = "NO_CAPACITY", "No uncommitted work slice fits now. Review planned work, split scope, or defer this task."
            else:
                candidate["slice_minutes"] = slice_minutes
                candidate["queue"] = "ELIGIBLE"
                candidate["can_start"] = running is None
                rank = _rank(candidate, today=today, now=local_now, weights=config["weights"], goal_id=goals.get(task.pk),
                             unlocked=unlocked.get(task.pk, 0), committed=task.pk in commitments, last_category=last_category)
                eligible.append((rank, candidate))
                if is_running and effort["remaining_minutes"] is None:
                    decisions.append(_decision(candidate, "FOCUS_EFFORT_REVIEW", "Focus is continuing. Update its remaining estimate at a natural pause.", "Update remaining effort"))
        if code:
            candidate = _decision(candidate, code, message)
            decisions.append(candidate)
        elif candidate["queue"] in ("ELIGIBLE", "WAITING") and not task.definition_of_done.strip():
            decisions.append(_decision(candidate, "DEFINE_DONE", "Define the outcome so completion can be accepted against a clear standard.", "Clarify done condition"))
        evidence.append(_evidence(candidate))
    eligible.sort(key=lambda pair: pair[0], reverse=True)
    chosen = next((item for _, item in eligible if item["is_active_focus"]), None)
    if chosen is None and not running:
        chosen = eligible[0][1] if eligible else None
    if chosen:
        chosen["queue"] = "NOW"
        if chosen["is_active_focus"]:
            chosen["reasons"] = ["Preserves your current focus"] + chosen["reasons"]
            chosen["reason"] = " · ".join(chosen["reasons"])
    next_items = [item for _, item in eligible if item is not chosen]
    for item in next_items: item["queue"] = "NEXT"
    if running and chosen:
        urgent = next((item for item in next_items if item["task"].priority == Task.Priority.CRITICAL and chosen["task"].priority != Task.Priority.CRITICAL), None)
        if urgent:
            decisions.insert(0, _decision(urgent, "URGENT_REVIEW", "Critical work is ready. Review it at a safe pause; switching remains your explicit choice.", "Review urgent work"))
    if running and not chosen and not any(item.get("code") == "FOCUS_RESTRICTED" for item in decisions):
        decisions.insert(0, {"task": None, "task_id": None, "code": "FOCUS_REVIEW", "reason": "Your running timer needs review before another task starts. Pause or stop your current focus explicitly.", "action": "Review current focus", "queue": "DECISIONS"})
    if excluded["omitted_by_limit"]:
        decisions.insert(0, {"task": None, "task_id": None, "code": "CANDIDATE_LIMIT", "reason": f"{excluded['omitted_by_limit']} active tasks are outside this bounded review. Open Work to inspect all assigned work.", "action": "Open Work", "queue": "DECISIONS"})
    if excluded["restricted"]:
        decisions.insert(0, {"task": None, "task_id": None, "code": "ACCESS_REVIEW", "reason": "Some assignments need a current access review before work can start. Ask an authorized manager to check their scope.", "action": "Open Work", "queue": "DECISIONS"})
    # Replace eligible placeholder queues after sticky selection/ranking.
    placement = {item["task_id"]: item for item in ([chosen] if chosen else []) + next_items + waiting}
    evidence = [_evidence(placement[item["task_id"]]) if item["task_id"] in placement else item for item in evidence]
    proofs = [{key: item[key] for key in ("task_id", "queue", "code", "task_version", "dependency_version", "effort_version", "first_action_version") if key in item}
              for item in evidence]
    source = {"policy": config, "day": str(today), "capacity": work["version"], "tasks": sorted(proofs, key=lambda value:value["task_id"]),
              "goals": sorted(goals.items()), "unlocks": sorted(unlocked.items()), "last_category": last_category,
              "commitments": sorted(commitments), "has_capability": has_capability,
              "running": [running.pk, running.task_id] if running else None, "exclusions": excluded}
    source_digest = _digest(source)
    latest_revision = OperatorState.objects.filter(owner=actor).values_list("revision", flat=True).first()
    stale = latest_revision != initial_revision
    if stale:
        chosen, next_items, waiting, current_focus = None, [], [], None
        decisions = [{"task": None, "task_id": None, "code": "SOURCES_CHANGED", "reason": "Your work changed while guidance was checked. Refresh Now to review the current sources.", "action": "Refresh Now", "queue": "DECISIONS"}]
        evidence = [{**item, "queue": "EXPIRED"} for item in evidence]
    snapshot = None
    if persist:
        snapshot = OperatorSnapshot.objects.create(owner=actor, expires_at=now if stale else now+timedelta(seconds=SNAPSHOT_SECONDS), policy=config,
            candidates=evidence, exclusions=excluded, state_revision=initial_revision, source_digest=source_digest,
            selected_task_id=chosen["task_id"] if chosen else None)
    return {"now": chosen, "next": next_items[:6], "waiting": waiting[:10], "decisions": decisions[:12],
            "snapshot": snapshot, "policy": config, "config": config, "source_digest": source_digest,
            "state_revision": initial_revision,
            "freshness": {"evaluated_at": now, "expires_at": now if stale else now+timedelta(seconds=SNAPSHOT_SECONDS), "seconds": 0 if stale else SNAPSHOT_SECONDS, "stale": stale},
            "counts": {"total": total, "reviewed": len(tasks), "eligible": len(eligible), "next": len(next_items),
                       "waiting": len(waiting), "decisions": len(decisions), "omitted": excluded["omitted_by_limit"]},
            "exclusions": excluded, "capacity": work, "focus_active": running is not None,
            "running_entry": running if current_focus is not None else None, "current_focus": current_focus}


def _evidence(candidate):
    fields = ("task_id", "queue", "code", "reasons", "score", "task_version", "dependency_version", "effort_version", "first_action_version", "slice_minutes")
    return {key: candidate[key] for key in fields if key in candidate}
