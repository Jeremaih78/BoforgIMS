"""Deterministic local interval capacity; declared arrival is not an input."""
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from tasker.models import Task, TaskTimeEntry
from tasker.organization import can_manage_company, managed_teams_for
from tasker.planning_models import Availability, PlanningEvent, ScheduleBlock, WorkPolicy, validate_span

UTC = datetime_timezone.utc


class PlanningConflict(ValueError):
    pass


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if current is None or not current.has_perm("tasker.view_task"):
        raise PermissionDenied
    return current


def can_view_capacity(actor, owner):
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.has_perm("tasker.view_task"):
        return False
    if actor.pk == owner.pk or actor.is_superuser:
        return True
    if owner.tasker_team_memberships.filter(is_deleted=False, is_active=True, team__in=managed_teams_for(actor), team__is_deleted=False, team__company__is_active=True).exists():
        return True
    from tasker.models import OrganizationAccess
    companies = set(owner.tasker_team_memberships.filter(is_deleted=False, is_active=True, team__is_deleted=False, team__company__is_active=True).values_list("team__company_id", flat=True))
    companies.update(OrganizationAccess.objects.filter(user=owner, is_active=True, company__is_active=True).values_list("company_id", flat=True))
    return any(can_manage_company(actor, company) for company in companies)


def _self_editor(user):
    user = _actor(user)
    if not user.has_perm("tasker.change_dailyplan"):
        raise PermissionDenied
    return user


def policy_for(user):
    return WorkPolicy.objects.filter(owner=user).first()


def locked_policy(owner):
    """Call inside a transaction; shared serialization point without actor locks."""
    policy, _ = WorkPolicy.all_objects.get_or_create(owner=owner, defaults={"created_by": owner, "updated_by": owner})
    policy = WorkPolicy.all_objects.select_for_update().get(pk=policy.pk)
    if policy.is_deleted:
        raise ValidationError("Restore this work policy before changing plans.")
    return policy


def _expect(record, expected):
    if str(record.revision) != str(expected or ""):
        raise PlanningConflict("This planning record changed. Reload and review the current version before saving.")


def _event(*, owner, actor, action, record, details=None):
    return PlanningEvent.objects.create(owner=owner, actor=actor, action=action, object_type=type(record).__name__, object_id=record.pk, details=details or {})


@transaction.atomic
def save_work_policy(*, user, timezone_name, weekdays, start_time, end_time, buffer_minutes, expected_version=None):
    user = _self_editor(user)
    previous = policy_for(user)
    policy = locked_policy(user)
    if previous is not None or policy.configured:
        _expect(policy, expected_version)
    elif expected_version:
        raise PlanningConflict("The work policy changed. Reload before saving.")
    policy.timezone_name, policy.weekdays = timezone_name, weekdays
    policy.start_time, policy.end_time = start_time, end_time
    policy.buffer_minutes, policy.configured = buffer_minutes, True
    policy.revision, policy.updated_by = uuid.uuid4(), user
    policy.save()
    _event(owner=user, actor=user, action="policy.saved", record=policy, details={"revision": str(policy.revision), "timezone_name": policy.timezone_name,
        "weekdays": policy.weekdays, "start_time": str(policy.start_time), "end_time": str(policy.end_time), "buffer_minutes": policy.buffer_minutes})
    return policy


@transaction.atomic
def save_availability(*, user, kind, starts_at, ends_at, private_note="", item=None, expected_version=None):
    user = _self_editor(user)
    locked_policy(user)
    if item is not None:
        item = Availability.objects.select_for_update().filter(pk=item.pk, owner=user).first()
        if item is None:
            raise PermissionDenied
        _expect(item, expected_version)
    else:
        item = Availability(owner=user, created_by=user)
    item.kind, item.starts_at, item.ends_at, item.private_note = kind, starts_at, ends_at, private_note
    item.revision, item.updated_by = uuid.uuid4(), user
    item.save()
    _event(owner=user, actor=user, action="availability.saved", record=item, details={"kind": item.kind, "starts_at": item.starts_at.isoformat(), "ends_at": item.ends_at.isoformat(), "revision": str(item.revision)})
    return item


@transaction.atomic
def remove_availability(*, user, item, expected_version):
    user = _self_editor(user)
    locked_policy(user)
    item = Availability.objects.select_for_update().filter(pk=item.pk, owner=user).first()
    if item is None:
        raise PermissionDenied
    _expect(item, expected_version)
    item.revision = uuid.uuid4()
    item.delete(user=user)
    _event(owner=user, actor=user, action="availability.removed", record=item)
    return item


def aware_local(day, clock, zone):
    """Reject ambiguous/nonexistent wall times rather than guessing a DST fold."""
    zone = ZoneInfo(zone) if isinstance(zone, str) else zone
    naive = datetime.combine(day, clock)
    candidates = []
    for fold in (0, 1):
        aware = naive.replace(tzinfo=zone, fold=fold)
        if aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) == naive and aware.utcoffset() not in [value.utcoffset() for value in candidates]:
            candidates.append(aware)
    if len(candidates) != 1:
        raise ValidationError("This local time is ambiguous or does not exist in the selected timezone. Choose another time.")
    return candidates[0]


def interval_union(intervals):
    """Merge overlaps/touching intervals in UTC; each second is counted once."""
    values = []
    for start, end in intervals:
        if not isinstance(start, datetime) or not isinstance(end, datetime) or timezone.is_naive(start) or timezone.is_naive(end):
            raise ValidationError("Interval calculation requires explicit timezone-aware datetimes.")
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        if end > start:
            values.append((start, end))
    ordered = sorted(values)
    merged = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    return merged


def interval_intersections(left, right):
    return interval_union((max(a, c), min(b, d)) for a, b in interval_union(left) for c, d in interval_union(right) if max(a, c) < min(b, d))


def interval_subtract(windows, excluded):
    pieces = interval_union(windows)
    for start, end in interval_union(excluded):
        next_pieces = []
        for left, right in pieces:
            if end <= left or start >= right:
                next_pieces.append((left, right))
            else:
                if left < start: next_pieces.append((left, start))
                if end < right: next_pieces.append((end, right))
        pieces = next_pieces
    return pieces


def interval_minutes(intervals):
    return sum((end - start).total_seconds() for start, end in interval_union(intervals)) / 60


def work_windows(policy, day):
    if policy is None or not policy.configured or policy.buffer_minutes is None:
        return None
    zone = ZoneInfo(policy.timezone_name)
    boundary = [(aware_local(day, time.min, zone), aware_local(day + timedelta(days=1), time.min, zone))]
    windows = []
    # An overnight shift beginning yesterday can contribute to this local day.
    for source_day in (day - timedelta(days=1), day):
        if source_day.weekday() not in policy.weekdays:
            continue
        end_day = source_day + timedelta(days=1) if policy.end_time < policy.start_time else source_day
        windows.append((aware_local(source_day, policy.start_time, zone), aware_local(end_day, policy.end_time, zone)))
    return interval_intersections(windows, boundary)


def active_schedule_blocks(owner):
    return ScheduleBlock.objects.filter(owner=owner, state=ScheduleBlock.State.ACCEPTED, task__is_deleted=False, task__archived=False,
        task__owner_id=owner.pk, task__status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)).select_related("task__remaining_effort_estimate")


def capacity_for_day(*, user, day, actor=None, exclude_block=None):
    actor = _actor(actor or user)
    if not can_view_capacity(actor, user):
        raise PermissionDenied
    if not isinstance(day, date) or isinstance(day, datetime):
        raise ValidationError("Choose a calendar date.")
    policy = policy_for(user)
    zone_name = policy.timezone_name if policy else "Africa/Harare"
    zone = ZoneInfo(zone_name)
    start, end = aware_local(day, time.min, zone), aware_local(day + timedelta(days=1), time.min, zone)
    availability = list(Availability.objects.filter(owner=user, starts_at__lt=end, ends_at__gt=start).order_by("pk"))
    blocks = active_schedule_blocks(user).filter(starts_at__lt=end, ends_at__gt=start)
    if exclude_block is not None:
        blocks = blocks.exclude(pk=getattr(exclude_block, "pk", exclude_block))
    blocks = list(blocks.order_by("pk"))
    warnings = []
    try:
        windows = work_windows(policy, day)
    except ValidationError:
        windows = None
        warnings.append("Working hours cross an ambiguous or missing local time. Review the timezone and hours.")
    known = windows is not None
    boundary = [(start, end)]
    excluded = interval_intersections([(item.starts_at, item.ends_at) for item in availability], windows or boundary)
    leave = interval_intersections([(item.starts_at, item.ends_at) for item in availability if item.kind == Availability.Kind.LEAVE], windows or boundary)
    commitments = interval_intersections([(item.starts_at, item.ends_at) for item in availability if item.kind == Availability.Kind.COMMITMENT], windows or boundary)
    work = interval_minutes(windows) if known else None
    buffer = policy.buffer_minutes if policy and policy.configured else None
    usable = max(0, work - interval_minutes(excluded) - buffer) if known else None
    booked = sum(interval_minutes(interval_intersections([(block.starts_at, block.ends_at)], boundary)) for block in blocks)
    from tasker.services.effort import effort_for_task
    tasks = {block.task_id: block.task for block in blocks}
    running = {task_id: [] for task_id in tasks}
    for task_id, pk, started in TaskTimeEntry.objects.filter(task_id__in=tasks, ended_at__isnull=True).values_list("task_id", "pk", "started_at"):
        running[task_id].append((pk, started))
    now = timezone.now()
    efforts = {}
    for task_id, task in tasks.items():
        closed = max(0, int(task.actual_duration.total_seconds())) if task.actual_duration else 0
        total = closed + sum(max(0, int((now-started).total_seconds())) for _, started in running[task_id])
        efforts[task_id] = effort_for_task(task=task, now=now, logged=(total, running[task_id]))
    unknown = sum(item["remaining_minutes"] is None for item in efforts.values())
    available = max(0, usable - booked) if known else None
    overload = max(0, booked - usable) if known else None
    conflict_counts = {"outside_hours_count": 0, "availability_conflict_count": 0, "overlap_conflict_count": 0}
    if known:
        free = interval_subtract(windows, excluded)
        accepted_intervals = []
        for block in blocks:
            pieces = interval_intersections([(block.starts_at, block.ends_at)], boundary)
            total = interval_minutes(pieces)
            inside = interval_minutes(interval_intersections(pieces, windows))
            unexcluded = interval_minutes(interval_intersections(pieces, free))
            if inside < total:
                conflict_counts["outside_hours_count"] += 1
            if unexcluded < inside:
                conflict_counts["availability_conflict_count"] += 1
            accepted_intervals.extend((left, right, block.pk) for left, right in pieces)
        # A sorted sweep identifies overlap participants without a quadratic
        # pair comparison; touching work blocks are not conflicts.
        overlapping = set()
        furthest_end, furthest_pk = None, None
        for left, right, pk in sorted(accepted_intervals):
            if furthest_end is not None and left < furthest_end:
                overlapping.update((pk, furthest_pk))
            if furthest_end is None or right > furthest_end:
                furthest_end, furthest_pk = right, pk
        conflict_counts["overlap_conflict_count"] = len(overlapping)
        if conflict_counts["outside_hours_count"]:
            warnings.append("Accepted work includes blocks outside the current working hours. Review the existing blocks.")
        if conflict_counts["availability_conflict_count"]:
            warnings.append("Accepted work overlaps current leave or fixed commitments. Review the existing blocks; private exclusion notes are not shared.")
        if overlapping:
            warnings.append("Accepted work blocks overlap one another. Review the existing commitments.")
    if not known:
        warnings.append("Capacity is unknown until working hours and an interruption buffer are explicitly configured.")
    if unknown:
        warnings.append("Some accepted work has unknown or overdue remaining-effort estimates.")
    snapshot = {"owner": user.pk, "day": str(day), "policy": [str(policy.revision), policy.timezone_name, policy.weekdays, str(policy.start_time), str(policy.end_time), policy.buffer_minutes, policy.configured] if policy else None,
                "availability": [(item.pk, str(item.revision), item.starts_at.isoformat(), item.ends_at.isoformat()) for item in availability],
                "blocks": [(block.pk, str(block.revision), block.starts_at.isoformat(), block.ends_at.isoformat(), block.task_id, efforts[block.task_id]["version"]) for block in blocks]}
    version = hashlib.sha256(json.dumps(snapshot, sort_keys=True).encode()).hexdigest()
    return {"known": known, "timezone_name": zone_name, "day": day, "work_minutes": work,
        "excluded_minutes": interval_minutes(excluded), "leave_minutes": interval_minutes(leave), "commitment_minutes": interval_minutes(commitments),
        "buffer_minutes": buffer, "usable_minutes": usable, "committed_minutes": booked, "available_minutes": available,
        "overload_minutes": overload, "unknown_effort_count": unknown, "warnings": warnings, "version": version,
        **conflict_counts,
        "work_intervals": windows or [], "free_intervals": interval_subtract(windows or [], excluded),
        "booked_intervals": [(block.starts_at, block.ends_at) for block in blocks], "policy": policy}
