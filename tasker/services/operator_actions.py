"""Execute a reviewed operator suggestion through the existing Focus lifecycle.

The snapshot is evidence, never permission. Every write rechecks the actor, the
canonical task, source proofs and current feasibility under the same planning
serialization lock used by accepted work blocks. No second timer or dispatch
queue is created here.
"""
from __future__ import annotations

import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.utils import timezone

from tasker.models import Task
from tasker.operator_models import OperatorOverride, OperatorSnapshot, OperatorState
from tasker.organization import require_task_access
from tasker.planning_models import WorkPolicy
from tasker.services.dependencies import assert_task_ready
from tasker.services.focus import ActiveTimerConflict, block_task, running_entry_for, start_focus
from tasker.services.operator import build_operator


class OperatorActionConflict(ValueError):
    """The employee must review a fresh recommendation before acting."""


def _reason(value, *, required):
    if not isinstance(value, str) or len(value) > 1000 or "\x00" in value:
        raise OperatorActionConflict("Use a reason of up to 1,000 characters.")
    value = value.strip()
    if required and not value:
        raise OperatorActionConflict("Briefly explain this choice so your decision is recorded.")
    return value


def _identity(snapshot_id, task_id):
    try:
        snapshot_id = uuid.UUID(str(snapshot_id))
        if isinstance(task_id, bool):
            raise ValueError
        task_text = str(task_id)
        if not task_text.isascii() or not task_text.isdigit() or len(task_text) > 18:
            raise ValueError
        task_id = int(task_text)
        if task_id < 1:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        raise OperatorActionConflict("This recommendation is unavailable. Refresh Now and try again.") from None
    return snapshot_id, task_id


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    current = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if current is None or not current.has_perm("tasker.view_task") or not current.has_perm("tasker.change_task"):
        raise PermissionDenied
    return current


def _reviewed(*, user, snapshot_id, task_id, for_block=False):
    """Caller holds the actor lock; lock order matches Focus and scheduling."""
    snapshot = OperatorSnapshot.objects.filter(pk=snapshot_id, owner=user).first()
    if snapshot is None:
        raise OperatorActionConflict("This recommendation is unavailable. Refresh Now and try again.")
    if snapshot.expires_at <= timezone.now():
        raise OperatorActionConflict("This recommendation expired. Refresh Now before starting work.")
    stored = next((item for item in snapshot.candidates
                   if isinstance(item, dict) and item.get("task_id") == task_id), None)
    if stored is None or str(stored.get("queue", "")).upper() not in ("NOW", "NEXT"):
        raise OperatorActionConflict("Choose a current Now or Next suggestion before acting.")
    task = Task.objects.select_for_update().filter(pk=task_id, owner=user).first()
    if task is None:
        raise OperatorActionConflict("This task is no longer available in your work. Refresh Now.")
    require_task_access(user, task, capability="tasker.view_task")
    # Do not invent hours as a side effect of starting a recommendation.
    if not WorkPolicy.objects.select_for_update().filter(owner=user, configured=True).exists():
        raise OperatorActionConflict("Configure your working hours and capacity before using this recommendation.")
    state = OperatorState.objects.filter(owner=user).first()
    if state is None or state.revision != snapshot.state_revision:
        raise OperatorActionConflict("Your work changed after this recommendation. Refresh Now and review it again.")
    fresh = build_operator(user=user, persist=False)
    # A direct QuerySet write can bypass signals. Content proofs therefore remain
    # necessary in addition to synchronous event invalidation.
    if fresh.get("source_digest") != snapshot.source_digest:
        raise OperatorActionConflict("The task, priorities or capacity changed. Refresh Now and review the new guidance.")
    candidates = ([fresh["now"]] if fresh.get("now") else []) + list(fresh.get("next", ()))
    current = next((item for item in candidates if item.get("task_id") == task_id), None)
    if current is None:
        raise OperatorActionConflict("This task is no longer feasible now. Refresh Now for the next move.")
    for proof in ("task_version", "dependency_version", "effort_version"):
        if current.get(proof) != stored.get(proof):
            raise OperatorActionConflict("This task changed. Review its current action and effort before continuing.")
    # Another event arriving during the read cannot leave us using a mixed
    # revision. Dependency readiness also performs its own graph revision check.
    revision = OperatorState.objects.filter(owner=user).values_list("revision", flat=True).first()
    if revision != snapshot.state_revision:
        raise OperatorActionConflict("Your work changed while it was checked. Refresh Now and try again.")
    if not for_block:
        assert_task_ready(task=task, user=user, expected_version=current.get("dependency_version"))
    # Row/graph locks and a large review can consume the remaining lifetime.
    # Permission at the beginning of the request never extends that lifetime.
    if snapshot.expires_at <= timezone.now():
        raise OperatorActionConflict("This recommendation expired while it was checked. Refresh Now before acting.")
    return user, snapshot, task, stored, current


@transaction.atomic
def execute_recommendation(*, user, snapshot_id, task_id, reason="", choose_another=False):
    """Start reviewed Now, or an explicitly reasoned alternative from Next.

    A healthy running commitment is never replaced. The employee can stop it
    through Focus and review the refreshed operator before choosing another.
    Replayed/stale snapshots are rejected without creating another timer.
    """
    reason = _reason(reason, required=bool(choose_another))
    snapshot_id, task_id = _identity(snapshot_id, task_id)
    user = _actor(user)
    user, snapshot, task, stored, current = _reviewed(user=user, snapshot_id=snapshot_id, task_id=task_id)
    if choose_another:
        if str(stored.get("queue", "")).upper() != "NEXT":
            raise OperatorActionConflict("Choose another from the current Next queue.")
    elif snapshot.selected_task_id != task_id or str(stored.get("queue", "")).upper() != "NOW":
        raise OperatorActionConflict("This is an alternative. Choose another with a brief reason before starting it.")
    running = running_entry_for(user=user)
    if running and running.task_id != task_id:
        raise OperatorActionConflict("Your current Focus is still running. Stop it before choosing another task.")
    if running:
        return running
    try:
        entry = start_focus(task=task, user=user, interval_minutes=current.get("slice_minutes"))
    except ActiveTimerConflict:
        raise OperatorActionConflict("Your current Focus is still running. Return to it before choosing another task.") from None
    OperatorOverride.objects.create(owner=user, snapshot=snapshot, task_id=task_id,
                                    action="CHOOSE_ANOTHER" if choose_another else "START", reason=reason)
    return entry


@transaction.atomic
def block_recommendation(*, user, snapshot_id, task_id, reason):
    """Mark reviewed work blocked, retaining it for the next Waiting queue."""
    reason = _reason(reason, required=True)
    snapshot_id, task_id = _identity(snapshot_id, task_id)
    user = _actor(user)
    user, snapshot, task, stored, current = _reviewed(user=user, snapshot_id=snapshot_id, task_id=task_id, for_block=True)
    task = block_task(task=task, user=user, reason=reason)
    OperatorOverride.objects.create(owner=user, snapshot=snapshot, task_id=task_id, action="BLOCK", reason=reason)
    return task
