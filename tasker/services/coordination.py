"""Versioned shared requests and factual coordination; no implicit assignment."""
from datetime import datetime, timedelta
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, F, IntegerField, Q, When
from django.shortcuts import get_object_or_404
from django.utils import timezone

from accounting.models import Company
from tasker.coordination_models import CoordinationEvent, CoordinationRequest
from tasker.models import OrganizationAccess, Task, Team, TeamMembership
from tasker.organization import require_task_access, visible_companies_for, visible_teams_for
from tasker.policies import can_change_task, visible_tasks_for
from tasker.services.focus import block_task
from tasker.services.workbench import work_version


ACTIVE = (CoordinationRequest.Status.OPEN, CoordinationRequest.Status.ACKNOWLEDGED)


class CoordinationConflict(ValueError):
    pass


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def scoped_tasks_for(user):
    """Current ownership does not bypass a revoked company/team membership."""
    current_memberships = TeamMembership.objects.filter(user=user, is_active=True,
        team__is_deleted=False, team__company__is_active=True).values("team_id")
    return visible_tasks_for(user).filter(
        Q(company__isnull=True) | Q(company__in=visible_companies_for(user))
    ).filter(Q(team__isnull=True) | Q(team__in=visible_teams_for(user))).filter(
        ~Q(owner=user) | Q(team__isnull=True) | Q(team_id__in=current_memberships)
    )


def visible_requests_for(user):
    actor = _actor(user)
    return CoordinationRequest.objects.filter(task__in=scoped_tasks_for(actor),
        task__owner_id=F("source_owner_id")).filter(
        Q(task__company__isnull=True, source_company__isnull=True) | Q(task__company_id=F("source_company_id"))
    ).filter(Q(task__team__isnull=True, source_team__isnull=True) | Q(task__team_id=F("source_team_id")))


def get_request(*, user, pk):
    return get_object_or_404(visible_requests_for(user).select_related("task", "raised_by", "resolver"), pk=pk)


def source_available(item):
    task = item.task
    return bool(not task.is_deleted and not task.archived and task.owner_id == item.source_owner_id
        and task.company_id == item.source_company_id and task.team_id == item.source_team_id
        and (not task.company_id or task.company.is_active)
        and (not task.team_id or not task.team.is_deleted))


def resolver_can_access(item):
    resolver = get_user_model().objects.filter(pk=item.resolver_id, is_active=True).first()
    return bool(resolver and resolver.has_perm("tasker.view_task") and source_available(item)
        and scoped_tasks_for(resolver).filter(pk=item.task_id).exists())


def resolver_candidates(*, task, user):
    actor = _actor(user)
    task = scoped_tasks_for(actor).filter(pk=task.pk).first()
    if task is None or not can_change_task(actor, task):
        raise PermissionDenied
    # Bound actual potential readers before evaluating current capabilities;
    # unrelated employee accounts must not crowd the owner or resolver out.
    cohort = Q(pk__in=(task.owner_id, actor.pk)) | Q(is_superuser=True)
    if task.team_id:
        managers = TeamMembership.objects.filter(team_id=task.team_id, is_active=True,
            role=TeamMembership.Role.MANAGER, team__is_deleted=False,
            team__company__is_active=True).values("user_id")
        cohort |= Q(pk__in=managers)
    if task.company_id:
        grants = OrganizationAccess.objects.filter(company_id=task.company_id, is_active=True,
            company__is_active=True).filter(Q(can_manage=True) | Q(can_view_all=True)).values("user_id")
        cohort |= Q(pk__in=grants)
    priority = Case(When(pk=task.owner_id, then=0), When(pk=actor.pk, then=1),
        default=2, output_field=IntegerField())
    candidates = get_user_model().objects.filter(is_active=True).filter(cohort).annotate(
        _resolver_priority=priority).order_by("_resolver_priority", "pk")[:100]
    ids = []
    for candidate in candidates:
        if candidate.has_perm("tasker.view_task") and scoped_tasks_for(candidate).filter(pk=task.pk).exists():
            ids.append(candidate.pk)
    return get_user_model().objects.filter(pk__in=ids, is_active=True).annotate(
        _resolver_priority=priority).order_by("_resolver_priority", "username", "pk")


def _text(value, *, required=True):
    if not isinstance(value, str) or len(value) > 1000 or "\x00" in value or (required and not value.strip()):
        raise ValidationError("Record a clear shared explanation of up to 1,000 characters.")
    return value.strip()


def _expect(item, expected_revision):
    if str(item.revision) != str(expected_revision or ""):
        raise CoordinationConflict("This request changed. Keep your draft and review its current version before acting.")


def _response_time(value):
    now = timezone.now()
    if not isinstance(value, datetime) or timezone.is_naive(value) or not now < value <= now + timedelta(days=90):
        raise ValidationError("Choose a timezone-aware expected response in the next 90 days.")
    return value


def _resolver(value, task):
    pk = getattr(value, "pk", value)
    actor = get_user_model().objects.filter(pk=pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task") or not scoped_tasks_for(actor).filter(pk=task.pk).exists():
        raise ValidationError("Choose a resolver who can currently read this task. Requests do not grant task access.")
    return actor


def _event(item, actor, action, **details):
    CoordinationEvent.objects.create(request=item, actor=actor, action=action, revision=item.revision,
        details=details, created_at=timezone.now())


def _enqueue(item, event):
    from tasker.services.jobs import enqueue_job
    return enqueue_job(kind="coordination.dispatch", dedupe_key=f"coord:{item.pk}:{item.revision}:{event}",
        payload={"request_id": item.pk, "revision": str(item.revision), "event": event}, owner=item.raised_by)


def _invalidate(item):
    from tasker.operator_events import invalidate_operator
    invalidate_operator(reason="work_changed", task_id=item.task_id)


@transaction.atomic
def create_request(*, user, task, kind, resolver, reason, requested_action, expected_response_at,
                   client_key, expected_task_version=None, escalation_policy="NONE"):
    actor = _actor(user)
    try:
        client_key = uuid.UUID(str(client_key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid request retry key is required.") from None
    reason, requested_action = _text(reason), _text(requested_action)
    if kind not in CoordinationRequest.Kind.values or escalation_policy not in CoordinationRequest.EscalationPolicy.values:
        raise ValidationError("Choose a supported request kind and escalation policy.")
    task = Task.objects.select_for_update().filter(pk=task.pk).first()
    if task is None:
        raise PermissionDenied
    require_task_access(actor, task, capability="tasker.view_task")
    if not scoped_tasks_for(actor).filter(pk=task.pk).exists():
        raise PermissionDenied
    previous = CoordinationRequest.objects.filter(raised_by=actor, client_key=client_key).first()
    if previous:
        supplied = (task.pk, kind, getattr(resolver, "pk", resolver), reason, requested_action, expected_response_at, escalation_policy)
        stored = (previous.task_id, previous.kind, previous.resolver_id, previous.reason, previous.requested_action, previous.expected_response_at, previous.escalation_policy)
        if supplied != stored:
            raise CoordinationConflict("This retry key already belongs to a different request.")
        return previous
    if not isinstance(expected_task_version, str) or expected_task_version != work_version(task):
        raise CoordinationConflict("The task changed. Review its current owner and state before requesting help.")
    if task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
        raise ValidationError("Only active work can have a new coordination request.")
    if CoordinationRequest.objects.filter(task=task, kind=kind, status__in=ACTIVE).exists():
        raise CoordinationConflict("An active request of this kind already exists for this task.")
    expected_response_at = _response_time(expected_response_at)
    resolver = _resolver(resolver, task)
    # Both a blocker and a handoff explicitly place the original commitment in
    # Waiting. Receipt or delivery does not accept the Task's definition of done.
    block_task(task=task, user=actor, reason=reason)
    item = CoordinationRequest(task=task, kind=kind, raised_by=actor, resolver=resolver, reason=reason,
        requested_action=requested_action, expected_response_at=expected_response_at, client_key=client_key,
        escalation_policy=escalation_policy, source_owner_id=task.owner_id,
        source_company_id=task.company_id, source_team_id=task.team_id, raised_at=timezone.now())
    item.save()
    _event(item, actor, "raised", resolver_id=resolver.pk, kind=kind)
    _enqueue(item, "raised")
    return item


def _locked_request(user, request_id, *, manage=False):
    actor = _actor(user)
    seed = CoordinationRequest.objects.filter(pk=request_id).values("task_id").first()
    if seed is None:
        raise PermissionDenied
    task = Task.all_objects.select_for_update().get(pk=seed["task_id"])
    require_task_access(actor, task, change=False, capability="tasker.view_task")
    item = CoordinationRequest.objects.select_for_update(of=("self",)).select_related("task__company", "task__team").get(pk=request_id)
    if not source_available(item) or not visible_requests_for(actor).filter(pk=item.pk).exists():
        raise PermissionDenied
    if manage and actor.pk != item.raised_by_id and not can_change_task(actor, task):
        raise PermissionDenied
    if not manage and actor.pk not in (item.raised_by_id, item.resolver_id) and not can_change_task(actor, task):
        raise PermissionDenied
    return actor, item, task


@transaction.atomic
def update_request(*, user, request_id, expected_revision, resolver, reason, requested_action,
                   expected_response_at, escalation_policy="NONE"):
    actor, item, task = _locked_request(user, request_id, manage=True)
    _expect(item, expected_revision)
    if item.status not in ACTIVE:
        raise CoordinationConflict("This request is closed. Raise a new request if more action is needed.")
    if escalation_policy not in CoordinationRequest.EscalationPolicy.values:
        raise ValidationError("Choose a supported escalation policy.")
    old_reason = item.reason
    item.reason, item.requested_action = _text(reason), _text(requested_action)
    item.expected_response_at = _response_time(expected_response_at)
    item.resolver, item.escalation_policy = _resolver(resolver, task), escalation_policy
    item.status, item.acknowledged_at, item.escalated_at, item.escalated_to = CoordinationRequest.Status.OPEN, None, None, None
    item.revision = uuid.uuid4()
    if task.status == Task.Status.BLOCKED and task.blocked_reason == old_reason:
        task.blocked_reason, task.updated_by = item.reason, actor
        task.save(update_fields=["blocked_reason", "updated_by", "updated_at"])
    else:
        _invalidate(item)
    item.save()
    _cancel_notifications(item)
    _event(item, actor, "updated", resolver_id=item.resolver_id)
    _enqueue(item, "updated")
    return item


@transaction.atomic
def acknowledge_request(*, user, request_id, expected_revision):
    actor, item, task = _locked_request(user, request_id)
    if actor.pk != item.resolver_id:
        raise PermissionDenied
    # Exact revision is always required; acknowledgement is not resolution.
    _expect(item, expected_revision)
    if item.status == CoordinationRequest.Status.ACKNOWLEDGED:
        return item
    if item.status != CoordinationRequest.Status.OPEN:
        raise CoordinationConflict("This request is no longer waiting for acknowledgement.")
    item.status, item.acknowledged_at, item.revision = CoordinationRequest.Status.ACKNOWLEDGED, timezone.now(), uuid.uuid4()
    _invalidate(item)
    item.save()
    _cancel_notifications(item)
    _event(item, actor, "acknowledged")
    _enqueue(item, "acknowledged")
    return item


def _cancel_notifications(item):
    from tasker.notification_models import Notification, NotificationDelivery
    notifications = Notification.objects.filter(request=item, cancelled_at__isnull=True)
    ids = list(notifications.select_for_update().order_by("pk").values_list("pk", flat=True))
    notifications.update(cancelled_at=timezone.now())
    NotificationDelivery.objects.filter(notification_id__in=ids, state__in=("QUEUED", "HELD")).update(state="CANCELLED", error_code="obsolete_request")


def _resume_own_block(item, task, actor):
    if item.blocks_task and task.status == Task.Status.BLOCKED and task.blocked_reason == item.reason:
        other = CoordinationRequest.objects.filter(task=task, status__in=ACTIVE).exclude(pk=item.pk).first()
        if other:
            task.blocked_reason = other.reason
        else:
            task.status, task.blocked_reason = Task.Status.PENDING, ""
        task.updated_by = actor
        task.save(update_fields=["status", "blocked_reason", "updated_by", "updated_at"])
    else:
        _invalidate(item)


@transaction.atomic
def resolve_request(*, user, request_id, expected_revision, resolution):
    actor, item, task = _locked_request(user, request_id)
    _expect(item, expected_revision)
    if item.status not in ACTIVE:
        raise CoordinationConflict("This request is already closed.")
    item.status, item.resolved_at, item.resolution, item.revision = CoordinationRequest.Status.RESOLVED, timezone.now(), _text(resolution), uuid.uuid4()
    _resume_own_block(item, task, actor)
    item.save()
    _cancel_notifications(item)
    _event(item, actor, "resolved")
    _enqueue(item, "resolved")
    return item


@transaction.atomic
def cancel_request(*, user, request_id, expected_revision, reason):
    actor, item, task = _locked_request(user, request_id, manage=True)
    _expect(item, expected_revision)
    if item.status not in ACTIVE:
        raise CoordinationConflict("This request is already closed.")
    item.status, item.resolved_at, item.resolution, item.revision = CoordinationRequest.Status.CANCELLED, timezone.now(), _text(reason), uuid.uuid4()
    _resume_own_block(item, task, actor)
    item.save()
    _cancel_notifications(item)
    _event(item, actor, "cancelled")
    _enqueue(item, "cancelled")
    return item


def _escalation_manager(item):
    ids = TeamMembership.objects.filter(team_id=item.source_team_id, is_active=True, role="MANAGER", user__is_active=True).order_by("user_id").values_list("user_id", flat=True)[:50]
    for manager in get_user_model().objects.filter(pk__in=ids).exclude(pk=item.resolver_id).order_by("pk"):
        if manager.has_perm("tasker.view_task") and scoped_tasks_for(manager).filter(pk=item.task_id).exists():
            return manager
    return None


def _lock_task_source(task_id):
    """Serialize source changes and membership changes before attention rows."""
    task = Task.all_objects.select_for_update().get(pk=task_id)
    if task.company_id:
        list(Company.objects.select_for_update().filter(pk=task.company_id))
    if task.team_id:
        list(Team.all_objects.select_for_update().filter(pk=task.team_id))
    return task


@transaction.atomic
def dispatch_event(payload, job=None):
    from tasker.services.notifications import create_notification
    seed = CoordinationRequest.objects.filter(pk=payload.get("request_id")).values("task_id").first()
    if seed is None:
        return {"obsolete": True}
    task = _lock_task_source(seed["task_id"])
    item = CoordinationRequest.objects.select_for_update(of=("self",)).select_related("task__company", "task__team", "resolver", "raised_by").filter(pk=payload.get("request_id")).first()
    if item is None or str(item.revision) != str(payload.get("revision")) or not source_available(item):
        return {"obsolete": True}
    event = payload.get("event")
    if event in ("raised", "updated") and item.status == CoordinationRequest.Status.OPEN:
        if task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
            return {"obsolete": True}
        if resolver_can_access(item):
            create_notification(owner=item.resolver, kind="REQUEST", task=item.task, request=item,
                source_revision=str(item.revision), dedupe_key=f"coord-alert:{item.pk}:{item.revision}:resolver")
    elif event in ("acknowledged", "resolved", "cancelled"):
        if item.raised_by.is_active and scoped_tasks_for(item.raised_by).filter(pk=item.task_id).exists():
            create_notification(owner=item.raised_by, kind=event.upper(), task=item.task, request=item,
                source_revision=str(item.revision), dedupe_key=f"coord-alert:{item.pk}:{item.revision}:raiser")
    elif event == "escalate" and item.status == CoordinationRequest.Status.OPEN and item.escalation_policy == "UNACKNOWLEDGED" and item.expected_response_at <= timezone.now():
        # Receipt stops this explicit unacknowledged-work escalation policy.
        if (item.status != CoordinationRequest.Status.OPEN or str(item.revision) != str(payload.get("revision"))
                or item.escalated_at or not source_available(item)
                or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED)):
            return {"obsolete": True}
        manager = _escalation_manager(item)
        if manager:
            item.escalated_at, item.escalated_to = timezone.now(), manager
            item.save()
            create_notification(owner=manager, kind="ESCALATION", task=task, request=item,
                source_revision=str(item.revision), dedupe_key=f"coord-alert:{item.pk}:{item.revision}:escalation")
            _event(item, None, "escalated", recipient_id=manager.pk)
    return {"request_id": item.pk, "event": str(event)[:30]}


def reconcile_coordination(*, now=None, limit=200):
    """Repair missed dispatches and cancel obsolete work; never invent a resolver."""
    now = now or timezone.now()
    counts = {"checked": 0, "cancelled": 0, "queued": 0}
    ids = list(CoordinationRequest.objects.filter(status__in=ACTIVE).order_by(F("last_checked_at").asc(nulls_first=True), "pk").values_list("pk", flat=True)[:max(1, min(limit, 1000))])
    for pk in ids:
        with transaction.atomic():
            seed = CoordinationRequest.objects.only("task_id").get(pk=pk)
            _lock_task_source(seed.task_id)
            item = CoordinationRequest.objects.select_for_update(of=("self",)).select_related("task__company", "task__team").get(pk=pk)
            CoordinationRequest.objects.filter(pk=pk).update(last_checked_at=now)
            counts["checked"] += 1
            if item.status not in ACTIVE:
                continue
            if not source_available(item) or item.task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED) or not resolver_can_access(item):
                item.status, item.resolved_at, item.resolution, item.revision = "CANCELLED", now, "Source or resolver access changed; review the commitment.", uuid.uuid4()
                _invalidate(item)
                item.save()
                _cancel_notifications(item)
                _event(item, None, "source_cancelled")
                counts["cancelled"] += 1
                continue
            if item.status == "OPEN":
                _enqueue(item, "raised")
                counts["queued"] += 1
                if item.escalation_policy == "UNACKNOWLEDGED" and item.expected_response_at <= now and not item.escalated_at:
                    _enqueue(item, "escalate")
                    counts["queued"] += 1
    return counts
