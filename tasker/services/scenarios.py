"""Deterministic management previews; only an explicit, fresh commit changes work.

Capacity is a bounded seven-calendar-day workload comparison, not a completion
forecast or employee score. Hidden commitments contribute aggregate minutes only.
"""
from copy import copy
from datetime import date, datetime, time, timedelta
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django import forms
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from accounting.models import Company
from tasker.models import Task, TaskActivity, TaskTimeEntry, Team, TeamMembership
from tasker.organization import can_manage_team, require_task_access, validate_task_scope
from tasker.planning_models import Availability, ScheduleBlock, WorkPolicy
from tasker.scenario_models import StrategyScenario, StrategyScenarioEvent
from tasker.services.capacity import (can_view_capacity, capacity_for_day, interval_intersections, interval_minutes,
    interval_subtract, aware_local)
from tasker.services.coordination import scoped_tasks_for
from tasker.services.dependencies import readiness_for_tasks
from tasker.services.effort import effort_for_task
from tasker.services.notes import notes_version
from tasker.services.workbench import work_version


ACTIVE = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
HORIZON_DAYS = 7
MAX_WORKLOAD_TASKS = 500


class ScenarioConflict(ValidationError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _actor(user, *, manager=False, team_id=None):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    if manager and (not actor.has_perms(("tasker.change_task", "tasker.assign_task")) or not can_manage_team(actor, team_id)):
        raise PermissionDenied
    return actor


def _enabled():
    if not getattr(settings, "TASKER_STRATEGY_ENABLED", False):
        raise ValidationError("Strategy scenarios are not enabled for this rollout.")


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ValidationError("Choose valid task, team and employee identifiers.")
    return value


def _changes(values):
    if type(values) is not list or not 1 <= len(values) <= 10:
        raise ValidationError("Preview between one and ten task changes.")
    result, seen = [], set()
    for value in values:
        required = {"task_id", "owner_id", "priority", "due_date"}
        if type(value) is not dict or not required.issubset(value) or set(value) - required - {"due_time"}:
            raise ValidationError("A scenario may change only the owner, priority and deadline of listed tasks.")
        row = {"task_id": _identifier(value["task_id"]), "owner_id": _identifier(value["owner_id"]),
            "priority": value["priority"], "due_date": value["due_date"]}
        if row["task_id"] in seen or row["priority"] not in Task.Priority.values:
            raise ValidationError("Choose distinct tasks and valid priorities.")
        seen.add(row["task_id"])
        if row["due_date"] is not None:
            try:
                if (type(row["due_date"]) is not str or date.fromisoformat(row["due_date"]).isoformat() != row["due_date"]
                        or not 1900 <= date.fromisoformat(row["due_date"]).year <= 2200):
                    raise ValueError
            except ValueError:
                raise ValidationError("Use an exact ISO calendar date from 1900 through 2200, or clear the deadline.") from None
        if "due_time" in value:
            clock = value["due_time"]
            if clock is not None:
                try:
                    if type(clock) is not str or len(clock) != 5 or time.fromisoformat(clock).strftime("%H:%M") != clock or row["due_date"] is None:
                        raise ValueError
                except ValueError:
                    raise ValidationError("Use HH:MM with a due date, or clear the due time.") from None
            row["due_time"] = clock
        result.append(row)
    return sorted(result, key=lambda row: row["task_id"])


def _state(task, owner=None):
    owner = owner or task.owner
    return {"owner_id": owner.pk, "owner_name": owner.get_full_name() or owner.get_username(),
        "priority": task.priority, "due_date": task.due_date.isoformat() if task.due_date else None,
        "due_time": task.due_time.strftime("%H:%M") if task.due_time else None}


def _candidate(task, change, owners):
    candidate = copy(task)
    candidate.owner = owners[change["owner_id"]]
    candidate.priority = change["priority"]
    candidate.due_date = date.fromisoformat(change["due_date"]) if change["due_date"] else None
    candidate.due_time = (time.fromisoformat(change["due_time"]) if change.get("due_time") else None) if "due_time" in change else task.due_time
    if candidate.due_date is None:
        candidate.due_time = None
    return candidate


def _read_sources(actor, team_id, changes):
    team = Team.objects.select_related("company").filter(pk=team_id, company__is_active=True).first()
    if team is None or not can_manage_team(actor, team):
        raise PermissionDenied
    tasks = list(scoped_tasks_for(actor).filter(pk__in=[row["task_id"] for row in changes], team=team,
        archived=False, status__in=ACTIVE).select_related("owner", "remaining_effort_estimate").order_by("pk"))
    if len(tasks) != len(changes):
        raise PermissionDenied
    target_ids = {row["owner_id"] for row in changes}
    qualified = set(TeamMembership.objects.filter(team=team, user_id__in=target_ids,
        is_active=True, user__is_active=True).values_list("user_id", flat=True))
    if qualified != target_ids:
        raise ValidationError("The proposed employee must be an active member of this team. Skills are not inferred.")
    owners = {row.pk: row for row in get_user_model().objects.filter(pk__in=target_ids | {task.owner_id for task in tasks})}
    return team, tasks, owners


def _deadline(task, zone_name):
    if task.due_date is None:
        return None
    if task.due_time is None:
        return aware_local(task.due_date+timedelta(days=1), time.min, zone_name)
    return aware_local(task.due_date, task.due_time, zone_name)


def _capacity_window(actor, owner, now):
    if not can_view_capacity(actor, owner):
        start_day = now.astimezone(ZoneInfo("Africa/Harare")).date()
        end_day = start_day+timedelta(days=HORIZON_DAYS)
        return {"owner_id": owner.pk, "day": start_day.isoformat(), "end_day": end_day.isoformat(),
            "known": False, "available_minutes": None, "usable_minutes": None, "committed_minutes": None,
            "conflict_count": 0, "timezone_name": "Africa/Harare", "version": _digest([owner.pk, "capacity_unavailable"]),
            "booked_by_task": {}, "days": [], "window_start": now,
            "window_end": aware_local(end_day, time.min, "Africa/Harare")}
    policy = WorkPolicy.objects.filter(owner=owner).first()
    zone_name = policy.timezone_name if policy else "Africa/Harare"
    start_day = now.astimezone(ZoneInfo(zone_name)).date()
    end_day = start_day + timedelta(days=HORIZON_DAYS)
    window_start = now
    window_end = aware_local(end_day, time.min, zone_name)
    known, available, usable, committed, conflicts = True, 0, 0, 0, 0
    proofs, free_intervals, booked_by_task = [], [], {}
    rows = []
    for offset in range(HORIZON_DAYS):
        day = start_day+timedelta(days=offset)
        cap = capacity_for_day(user=owner, day=day, actor=actor)
        known = known and cap["known"]
        conflicts += sum(cap[key] for key in ("outside_hours_count", "availability_conflict_count", "overlap_conflict_count"))
        boundary = [(now, window_end)]
        free = interval_intersections(cap["free_intervals"], boundary)
        booked = interval_intersections(cap["booked_intervals"], boundary)
        free = interval_subtract(free, booked)
        buffer = cap["buffer_minutes"] or 0
        future_free = max(0, interval_minutes(free)-buffer) if cap["known"] else None
        future_usable = max(0, interval_minutes(interval_intersections(cap["free_intervals"], boundary))-buffer) if cap["known"] else None
        if cap["known"]:
            available += future_free
            usable += future_usable
            committed += interval_minutes(booked)
        free_intervals.extend(free)
        proofs.append([day.isoformat(), cap["version"]])
        rows.append({"day": day.isoformat(), "available_minutes": future_free,
            "buffer_minutes": buffer, "free_intervals": free})
    # Query canonical current accepted reservations once for same-task accounting.
    blocks = list(ScheduleBlock.objects.filter(owner=owner, state="ACCEPTED", starts_at__lt=window_end,
        ends_at__gt=window_start, task__is_deleted=False, task__archived=False, task__owner=owner,
        task__status__in=ACTIVE).values("pk", "task_id", "starts_at", "ends_at", "revision"))
    for block in blocks:
        minutes = interval_minutes(interval_intersections([(block["starts_at"], block["ends_at"])], [(window_start, window_end)]))
        booked_by_task[block["task_id"]] = booked_by_task.get(block["task_id"], 0)+minutes
    return {"owner_id": owner.pk, "day": start_day.isoformat(), "end_day": end_day.isoformat(),
        "known": known, "available_minutes": available if known else None,
        "usable_minutes": usable if known else None, "committed_minutes": committed,
        "conflict_count": conflicts, "timezone_name": zone_name, "version": _digest(proofs),
        "booked_by_task": booked_by_task, "days": rows, "window_start": window_start, "window_end": window_end}


def _public_capacity(value, *, projected_available=None, backlog_unknown=0):
    return {key: value[key] for key in ("owner_id", "day", "end_day", "known", "available_minutes", "usable_minutes", "committed_minutes")} | {
        "projected_available_minutes": projected_available, "unknown_workload_count": backlog_unknown,
        "comparison": "7-day workload comparison, not promised completion"}


def _efforts(tasks, now):
    """Batch live timer inputs; never issue one timer lookup per backlog task."""
    tasks = list(tasks)
    running = {task.pk: [] for task in tasks}
    for task_id, pk, started in TaskTimeEntry.objects.filter(task_id__in=running,
            ended_at__isnull=True).values_list("task_id", "pk", "started_at"):
        running[task_id].append((pk, started))
    result = {}
    for task in tasks:
        total = max(0, int(task.actual_duration.total_seconds())) if task.actual_duration else 0
        total += sum(max(0, int((now-started).total_seconds())) for _, started in running[task.pk])
        result[task.pk] = effort_for_task(task=task, now=now, logged=(total, running[task.pk]))
    return result


def _preview(actor, team, tasks, owners, changes, *, now):
    changes_by_id = {row["task_id"]: row for row in changes}
    candidates = {task.pk: _candidate(task, changes_by_id[task.pk], owners) for task in tasks}
    readiness = readiness_for_tasks(tasks=tasks, user=actor)
    efforts = _efforts(tasks, now)
    running = set(TaskTimeEntry.objects.filter(task_id__in=candidates, ended_at__isnull=True).values_list("task_id", flat=True))
    capacities = {pk: _capacity_window(actor, employee, now) for pk, employee in owners.items()}
    workload_rows = list(scoped_tasks_for(actor).filter(owner_id__in=owners, archived=False, status__in=ACTIVE)
        .select_related("remaining_effort_estimate").order_by("pk")[:MAX_WORKLOAD_TASKS+1])
    overflow = len(workload_rows) > MAX_WORKLOAD_TASKS
    workload_rows = workload_rows[:MAX_WORKLOAD_TASKS]
    workload_effort = _efforts(workload_rows, now)
    # Evidence retains an opaque digest, not other teams' or personal task IDs/titles.
    workload_digest = _digest([[row.pk, work_version(row), workload_effort[row.pk]["version"]] for row in workload_rows])
    before_demand, after_demand = {pk: 0 for pk in owners}, {pk: 0 for pk in owners}
    before_unknown, after_unknown = {pk: 0 for pk in owners}, {pk: 0 for pk in owners}
    released = {pk: 0 for pk in owners}
    for task in workload_rows:
        after = candidates.get(task.pk, task)
        minutes = workload_effort[task.pk]["remaining_minutes"]
        booked = capacities[task.owner_id]["booked_by_task"].get(task.pk, 0)
        if minutes is None:
            before_unknown[task.owner_id] += 1
            after_unknown[after.owner_id] += 1
        else:
            before_demand[task.owner_id] += max(0, minutes-booked)
            after_demand[after.owner_id] += max(0, minutes-(booked if task.owner_id == after.owner_id else 0))
        if task.owner_id != after.owner_id:
            released[task.owner_id] += booked
    before_available, after_available = {}, {}
    for pk, cap in capacities.items():
        before_available[pk] = cap["available_minutes"]-before_demand[pk] if cap["known"] else None
        after_available[pk] = cap["available_minutes"]+released[pk]-after_demand[pk] if cap["known"] else None
    rows, blockers, warnings = [], [], []
    for task in tasks:
        after, cap = candidates[task.pk], capacities[candidates[task.pk].owner_id]
        reassigned = task.owner_id != after.owner_id
        local_blockers, local_warnings = [], []
        if reassigned:
            if task.pk in running:
                local_blockers.append("Stop the current Focus timer before moving this task to another employee.")
            if not readiness[task.pk].ready or task.status == Task.Status.BLOCKED:
                local_blockers.append("Resolve task blockers and prerequisites before moving executable work.")
            if not cap["known"]:
                local_blockers.append("Recipient working hours and interruption buffer are unknown.")
            if overflow or efforts[task.pk]["remaining_minutes"] is None or after_unknown[after.owner_id]:
                local_blockers.append("Recipient workload or remaining effort is unknown; review estimates before moving work.")
            if cap["conflict_count"]:
                local_blockers.append("Recipient accepted work has conflicts; review its time blocks first.")
            if after_available[after.owner_id] is not None and after_available[after.owner_id] < 0:
                local_blockers.append("The projected workload exceeds the recipient's remaining seven-day capacity.")
            deadline = _deadline(after, cap["timezone_name"])
            if deadline is not None:
                if deadline <= now:
                    local_blockers.append("The proposed deadline has passed; agree a future deadline before moving work.")
                elif deadline < cap["window_end"]:
                    # Reserve all other unreserved workload before this urgent task.
                    free_until = 0
                    for day in cap["days"]:
                        free_until += max(0, interval_minutes(interval_intersections(day["free_intervals"], [(now, deadline)]))-day["buffer_minutes"])
                    incoming = efforts[task.pk]["remaining_minutes"]
                    other_demand = max(0, after_demand[after.owner_id]-(incoming or 0))
                    if incoming is None or incoming > max(0, free_until-other_demand):
                        local_blockers.append("Known free capacity before this deadline cannot cover the proposed workload.")
            local_warnings.append("Moving ownership cancels this task's accepted time blocks. The new owner must review and accept a new schedule.")
            local_warnings.append("The previous owner may need to review their own intentions; private plans are not opened or changed.")
        elif efforts[task.pk]["remaining_minutes"] is None or not cap["known"]:
            local_warnings.append("Completion feasibility remains unknown; this metadata edit does not reserve additional time.")
        displaced = []
        blocks = list(ScheduleBlock.objects.filter(task=task, state="ACCEPTED").order_by("starts_at", "pk"))
        for block in blocks:
            if reassigned:
                displaced.append({"id": block.pk, "starts_at": block.starts_at.isoformat(), "ends_at": block.ends_at.isoformat()})
            elif after.due_date is not None and (after.due_date != task.due_date or after.due_time != task.due_time):
                if block.ends_at > _deadline(after, cap["timezone_name"]):
                    local_blockers.append("Revise existing accepted time blocks before shortening the deadline before their end.")
        if after.due_date and _deadline(after, cap["timezone_name"]) <= now:
            local_warnings.append("This deadline is already past. No completion forecast is inferred.")
        if not readiness[task.pk].ready:
            local_warnings.append(readiness[task.pk].reason)
        row = {"task_id": task.pk, "title": task.title, "before": _state(task), "after": _state(after),
            "remaining_minutes": efforts[task.pk]["remaining_minutes"],
            "readiness": {"ready": readiness[task.pk].ready, "code": readiness[task.pk].code, "reason": readiness[task.pk].reason},
            "displaced_blocks": displaced,
            "capacity_before": _public_capacity(capacities[task.owner_id], projected_available=before_available[task.owner_id], backlog_unknown=before_unknown[task.owner_id]),
            "capacity_after": _public_capacity(cap, projected_available=after_available[after.owner_id], backlog_unknown=after_unknown[after.owner_id]),
            "blockers": list(dict.fromkeys(local_blockers)), "warnings": list(dict.fromkeys(local_warnings))}
        rows.append(row)
        blockers.extend(row["blockers"])
        warnings.extend(row["warnings"])
    if overflow:
        warnings.append("The affected workload exceeds the 500-task review bound. Capacity effects remain unknown.")
    if not any(row["before"] != row["after"] for row in rows):
        blockers.append("The proposed values are unchanged. Choose a work change to preview.")
    proofs = {"team_id": team.pk, "company_id": team.company_id,
        "tasks": {str(task.pk): work_version(task) for task in tasks},
        "efforts": {str(task.pk): efforts[task.pk]["version"] for task in tasks},
        "readiness": {str(task.pk): readiness[task.pk].version for task in tasks},
        "capacity": {str(pk): cap["version"] for pk, cap in capacities.items()},
        "days": {str(pk): cap["day"] for pk, cap in capacities.items()},
        "owners": {str(pk): employee.is_active for pk, employee in owners.items()},
        "membership": _digest(list(TeamMembership.objects.filter(team=team, user_id__in=owners)
            .order_by("pk").values_list("pk", "user_id", "is_active", "role", "is_deleted"))),
        "workload": workload_digest, "workload_overflow": overflow,
        "blocks": _digest([[block.pk, str(block.revision), block.state, block.starts_at.isoformat(), block.ends_at.isoformat()]
            for block in ScheduleBlock.all_objects.filter(task_id__in=candidates).order_by("pk")]),
        "running": sorted(running)}
    preview = {"rows": rows, "can_commit": not blockers, "blockers": list(dict.fromkeys(blockers)),
        "warnings": list(dict.fromkeys(warnings)), "generated_at": now.isoformat(),
        "uncertainty": "7-day workload comparison, not promised completion. Readable shared workload plus all accepted commitments; other unplanned work may exist. Current team membership is checked; skills are not inferred. Future changes may alter the result."}
    return preview, proofs


def visible_scenarios_for(user):
    actor = _actor(user)
    from tasker.organization import managed_teams_for
    return StrategyScenario.objects.filter(owner=actor, team__in=managed_teams_for(actor), team__company__is_active=True)


def get_scenario(*, user, scenario_id):
    actor = _actor(user)
    scenario = visible_scenarios_for(actor).select_related("team", "owner").filter(pk=scenario_id).first()
    if scenario is None:
        raise PermissionDenied
    _actor(actor, manager=True, team_id=scenario.team_id)
    ids = {row["task_id"] for row in scenario.changes}
    if scoped_tasks_for(actor).filter(pk__in=ids, team_id=scenario.team_id).count() != len(ids):
        raise PermissionDenied
    return scenario


def _event(scenario, actor, action):
    return StrategyScenarioEvent.objects.create(scenario=scenario, actor=actor, action=action,
        revision=scenario.revision, details={"task_ids": [row["task_id"] for row in scenario.changes],
            "notification_ids": scenario.notification_ids})


def create_scenario(*, user, team_id, title, changes, client_key):
    _enabled()
    team_id = _identifier(team_id)
    actor = _actor(user, manager=True, team_id=team_id)
    if type(title) is not str or not title.strip() or len(title) > 140 or "\x00" in title:
        raise ValidationError("Name this scenario in up to 140 characters.")
    try:
        client_key = uuid.UUID(str(client_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid scenario retry key is required.") from None
    normalized = _changes(changes)
    request_digest = _digest({"team_id": team_id, "title": title.strip(), "changes": normalized})
    existing = StrategyScenario.objects.filter(owner=actor, client_key=client_key).first()
    if existing:
        if existing.request_digest != request_digest:
            raise ScenarioConflict("This retry key belongs to a different scenario.")
        return get_scenario(user=actor, scenario_id=existing.pk)
    team, tasks, owners = _read_sources(actor, team_id, normalized)
    preview, proofs = _preview(actor, team, tasks, owners, normalized, now=timezone.now())
    with transaction.atomic():
        get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
        existing = StrategyScenario.objects.filter(owner=actor, client_key=client_key).first()
        if existing:
            if existing.request_digest != request_digest:
                raise ScenarioConflict("This retry key belongs to a different scenario.")
            return get_scenario(user=actor, scenario_id=existing.pk)
        scenario = StrategyScenario.objects.create(owner=actor, team=team, title=title.strip(), changes=normalized,
            source_proofs=proofs, preview=preview, source_digest=_digest(proofs), preview_digest=_digest(preview),
            request_digest=request_digest, client_key=client_key, expires_at=timezone.now()+timedelta(minutes=15),
            created_by=actor, updated_by=actor)
        _event(scenario, actor, "CREATED")
    return scenario


class _MutationForm(forms.ModelForm):
    work_version = forms.CharField()
    notes_version = forms.CharField()
    definition_submitted = False

    class Meta:
        model = Task
        fields = ("owner", "priority", "due_date", "due_time")


@transaction.atomic
def commit_scenario(*, user, scenario_id, expected_revision):
    _enabled()
    actor = _actor(user)
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    scenario = StrategyScenario.objects.select_for_update().filter(pk=scenario_id, owner=actor).first()
    if scenario is None:
        raise PermissionDenied
    _actor(actor, manager=True, team_id=scenario.team_id)
    if scenario.state == "COMMITTED":
        # Receipt replay never re-runs work or emits another notification.
        initial = scenario.events.filter(action="CREATED").values_list("revision", flat=True).first()
        if str(expected_revision) not in (str(scenario.revision), str(initial)):
            raise ScenarioConflict("This commit retry does not match the reviewed scenario version.")
        return get_scenario(user=actor, scenario_id=scenario.pk)
    if scenario.state != "DRAFT" or str(scenario.revision) != str(expected_revision):
        raise ScenarioConflict("This scenario changed. Review its current state before committing.")
    if scenario.expires_at <= timezone.now():
        raise ScenarioConflict("This scenario expired. Generate a fresh preview.")
    if _digest(scenario.source_proofs) != scenario.source_digest or _digest(scenario.preview) != scenario.preview_digest:
        raise ScenarioConflict("Scenario evidence changed. Generate a fresh preview.")
    # One short pilot commit fence prevents a concurrent manual Task creation or
    # cross-team transfer from becoming an unobserved workload phantom. NOWAIT
    # fails safely before taking row/scope/policy locks if a writer is in flight.
    # Ordinary reads remain available; the preview itself takes no such lock.
    if connection.vendor != "postgresql":
        raise ScenarioConflict("Scenario commits require PostgreSQL allocation fencing.")
    try:
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("LOCK TABLE tasker_task IN EXCLUSIVE MODE NOWAIT")
    except DatabaseError as exc:
        if (getattr(exc.__cause__, "sqlstate", None) or getattr(exc.__cause__, "pgcode", None)) == "55P03":
            raise ScenarioConflict("Another work change is being saved. Keep this draft and try again after it finishes.") from None
        raise
    ids = sorted(row["task_id"] for row in scenario.changes)
    # The table fence prevents workload phantoms; row reads remain limited to
    # the manager's explicitly selected canonical work, not private task bodies.
    list(Task.all_objects.select_for_update(no_key=True).filter(pk__in=ids).order_by("pk"))
    list(Company.objects.select_for_update().filter(pk=scenario.team.company_id))
    Team.all_objects.select_for_update().get(pk=scenario.team_id)
    team, tasks, owners = _read_sources(actor, scenario.team_id, scenario.changes)
    # Stable policy parents serialize availability/scheduling/effort edits while
    # this commit rechecks their read-only preview evidence. No missing rows are
    # created: unknown policy remains unknown and blocks reassignment.
    list(WorkPolicy.all_objects.select_for_update().filter(owner_id__in=owners).order_by("owner_id"))
    preview, proofs = _preview(actor, team, tasks, owners, scenario.changes, now=timezone.now())
    if proofs != scenario.source_proofs:
        raise ScenarioConflict("Work, scope, dependencies or capacity changed. Review a fresh scenario before committing.")
    if not preview["can_commit"]:
        raise ScenarioConflict(" ".join(preview["blockers"]))
    from tasker.services.tasks import save_task
    previous = {task.pk: task.owner_id for task in tasks}
    notifications = []
    for task in tasks:
        change = next(row for row in scenario.changes if row["task_id"] == task.pk)
        candidate = _candidate(task, change, owners)
        validate_task_scope(actor, candidate, previous=task, lock=True)
        data = {"owner": candidate.owner_id, "priority": candidate.priority,
            "due_date": candidate.due_date.isoformat() if candidate.due_date else "",
            "due_time": candidate.due_time.isoformat() if candidate.due_time else "",
            "work_version": work_version(task), "notes_version": notes_version(task.notes)}
        form = _MutationForm(data=data, instance=copy(task))
        if not form.is_valid():
            raise ValidationError(form.errors.as_text())
        saved = save_task(form=form, user=actor, preserve_notes=True)
        TaskActivity.objects.create(task=saved, event_type="UPDATED", description="Reviewed strategy scenario committed",
            changes={"scenario_id": str(scenario.pk), "owner_from": previous[task.pk], "owner_to": saved.owner_id,
                "priority": saved.priority, "due_date": str(saved.due_date) if saved.due_date else None},
            created_by=actor, updated_by=actor)
        from tasker.services.notifications import create_notification
        for recipient_id in sorted({previous[task.pk], saved.owner_id}):
            recipient = get_user_model().objects.filter(pk=recipient_id, is_active=True).first()
            if recipient is None or not recipient.has_perm("tasker.view_task") or recipient.pk == actor.pk:
                continue
            readable = scoped_tasks_for(recipient).filter(pk=saved.pk).exists()
            notification = create_notification(owner=recipient, kind="WORK_ALLOCATION_CHANGED",
                dedupe_key=f"scenario:{scenario.pk}:task:{saved.pk}:recipient:{recipient.pk}",
                task=saved if readable else None, source_revision=str(scenario.revision))
            notifications.append(notification.pk)
    scenario.state, scenario.revision = "COMMITTED", uuid.uuid4()
    scenario.committed_at, scenario.updated_by = timezone.now(), actor
    scenario.notification_ids = notifications
    scenario.save(update_fields=["state", "revision", "committed_at", "updated_by", "notification_ids", "updated_at"])
    _event(scenario, actor, "COMMITTED")
    return scenario


@transaction.atomic
def discard_scenario(*, user, scenario_id, expected_revision):
    actor = _actor(user)
    scenario = StrategyScenario.objects.select_for_update().filter(pk=scenario_id, owner=actor).first()
    if scenario is None:
        raise PermissionDenied
    if scenario.state == "DISCARDED":
        initial = scenario.events.filter(action="CREATED").values_list("revision", flat=True).first()
        if str(expected_revision) not in (str(scenario.revision), str(initial)):
            raise ScenarioConflict("This discard retry does not match the reviewed scenario version.")
        return scenario
    if scenario.state != "DRAFT" or str(scenario.revision) != str(expected_revision):
        raise ScenarioConflict("This scenario changed. Only a current draft can be discarded.")
    scenario.state, scenario.revision = "DISCARDED", uuid.uuid4()
    scenario.discarded_at, scenario.updated_by = timezone.now(), actor
    scenario.save(update_fields=["state", "revision", "discarded_at", "updated_by", "updated_at"])
    _event(scenario, actor, "DISCARDED")
    return scenario
