"""Targeted in-app attention, digests and the shared delivery ledger."""
from datetime import timedelta, timezone as datetime_timezone
import uuid
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import CharField, Exists, F, OuterRef, Q
from django.db.models.functions import Cast
from django.urls import reverse
from django.utils import timezone

from tasker.notification_models import Notification, NotificationDelivery, NotificationPreference
from tasker.services.capacity import aware_local, work_windows


TERMINAL_DELIVERIES = ("DELIVERED", "TEST_ACCEPTED", "PROVIDER_ACCEPTED", "CANCELLED", "UNKNOWN")
DIGEST_KINDS = ("REQUEST", "ACKNOWLEDGED", "RESOLVED", "CANCELLED", "ESCALATION")


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def effective_notification_preferences(user):
    record = NotificationPreference.objects.filter(owner=user).first()
    return {"record": record or NotificationPreference(owner=user), "revision": str(record.revision) if record else "default-v1"}


@transaction.atomic
def save_notification_preferences(*, user, timezone_name, quiet_enabled, quiet_start, quiet_end,
        respect_work_hours, suppress_during_focus, digest_mode, expected_revision="default-v1"):
    actor = _actor(user)
    # One actor lock serializes first creates without adding another authority.
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    record = NotificationPreference.objects.select_for_update().filter(owner=actor).first()
    current = str(record.revision) if record else "default-v1"
    if str(expected_revision) != current:
        raise ValueError("These notification settings changed. Keep your draft and reload before saving.")
    record = record or NotificationPreference(owner=actor)
    for field, value in {"timezone_name": timezone_name, "quiet_enabled": quiet_enabled,
            "quiet_start": quiet_start, "quiet_end": quiet_end, "respect_work_hours": respect_work_hours,
            "suppress_during_focus": suppress_during_focus, "digest_mode": digest_mode}.items():
        setattr(record, field, value)
    record.revision = uuid.uuid4()
    record.save()
    return record


def _source_queryset(actor):
    from tasker.services.coordination import scoped_tasks_for, visible_requests_for
    from tasker.services.reporting import visible_reports_for
    queryset = Notification.objects.filter(owner=actor, cancelled_at__isnull=True).filter(
        Q(expires_at__isnull=True) | Q(expires_at__gt=timezone.now()))
    queryset = queryset.filter(Q(task__isnull=True) | Q(task__in=scoped_tasks_for(actor)))
    queryset = queryset.filter(Q(request__isnull=True) | Q(
        request__in=visible_requests_for(actor).filter(task__archived=False),
        request__task_id=F("task_id"), source_revision=Cast("request__revision", CharField())))
    active_source = Q(request__status="OPEN", task__archived=False, task__status__in=("PENDING", "IN_PROGRESS", "BLOCKED"))
    queryset = queryset.filter(~Q(kind="REQUEST") | (active_source & Q(request__resolver=actor)))
    queryset = queryset.filter(~Q(kind="ESCALATION") | (active_source & Q(request__escalated_to=actor)))
    queryset = queryset.filter(Q(report_snapshot__isnull=True) | Q(report_snapshot__in=visible_reports_for(actor)))
    return queryset


def visible_notifications_for(user, include_held=False):
    actor = _actor(user)
    children = _source_queryset(actor).filter(grouped_into_id=OuterRef("pk")).exclude(kind="DIGEST")
    queryset = _source_queryset(actor).filter(grouped_into__isnull=True).annotate(_has_current_items=Exists(children)).exclude(kind="DIGEST", _has_current_items=False)
    if not include_held:
        queryset = queryset.filter(published_at__isnull=False)
    return queryset.select_related("task", "request", "report_snapshot")


def _source_authorized(notification):
    actor = get_user_model().objects.filter(pk=notification.owner_id, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task") or not _source_queryset(actor).filter(pk=notification.pk).exists():
        return False
    if notification.kind == "DIGEST":
        return _source_queryset(actor).filter(grouped_into=notification).exclude(kind="DIGEST").exists()
    if notification.report_snapshot_id:
        # Reports own their explicit executive audience boundary.
        from tasker.services.reporting import snapshot_can_view
        if not snapshot_can_view(actor, notification.report_snapshot):
            return False
    return True


def _context(notification, actor):
    titles = {"REQUEST": "Action requested", "ACKNOWLEDGED": "Request acknowledged",
        "RESOLVED": "Requested action resolved", "CANCELLED": "Request cancelled",
        "ESCALATION": "Unacknowledged request needs attention", "DIGEST": "Your coordination digest"}
    result = {"title": titles.get(notification.kind, "Work update"), "action_label": "Review Work",
        "url": reverse("ims:tasker:task_list"), "details": "", "items": []}
    if notification.kind == "DIGEST":
        children = _source_queryset(actor).filter(grouped_into=notification).exclude(kind="DIGEST").select_related("task", "request")[:100]
        for child in children:
            result["items"].append(_context(child, actor))
        result["details"] = f"{len(result['items'])} current updates."
    elif notification.request_id:
        result.update(title=f"{result['title']}: {notification.task.title}", details=notification.request.requested_action,
            action_label="Review request", url=reverse("ims:tasker:coordination_detail", args=(notification.request_id,)))
    elif notification.task_id:
        result.update(title=notification.task.title, action_label="Open task", url=notification.task.get_absolute_url())
    return result


def notification_context(notification, user):
    actor = _actor(user)
    # Callers may retain model instances across a source edit or reassignment.
    notification = Notification.objects.select_related("task", "request", "report_snapshot").filter(pk=notification.pk, owner=actor).first()
    if notification is None or not _source_authorized(notification):
        raise PermissionDenied
    return _context(notification, actor)


@transaction.atomic
def mark_notification(*, user, notification_id, acknowledge=False):
    actor = _actor(user)
    # A request acknowledgement locks Task before attention rows, matching
    # resolution/cancellation. Reading alone never changes a request's state.
    seed = Notification.objects.select_related("request").filter(pk=notification_id, owner=actor).first()
    if seed is None or not _source_authorized(seed):
        raise PermissionDenied
    if acknowledge and seed.kind == "REQUEST" and seed.request and seed.request.resolver_id == actor.pk and seed.request.status == "OPEN":
        from tasker.services.coordination import acknowledge_request
        acknowledge_request(user=actor, request_id=seed.request_id, expected_revision=str(seed.request.revision))
    notification = Notification.objects.select_for_update().get(pk=notification_id, owner=actor)
    if not acknowledge and not _source_authorized(notification):
        raise PermissionDenied
    notification.read_at = notification.read_at or timezone.now()
    if acknowledge:
        notification.acknowledged_at = notification.acknowledged_at or timezone.now()
    notification.save(update_fields=["read_at", "acknowledged_at"])
    if acknowledge:
        NotificationDelivery.objects.filter(notification=notification, state__in=("QUEUED", "HELD")).update(state="CANCELLED", error_code="acknowledged")
    return notification


@transaction.atomic
def create_notification(*, owner, kind, dedupe_key, task=None, request=None, report_snapshot=None,
                        source_revision="", expires_at=None):
    if request is not None and (task is None or task.pk != request.task_id):
        raise ValidationError("A request notification must retain its original task source.")
    notification, created = Notification.objects.get_or_create(dedupe_key=dedupe_key,
        defaults={"owner": owner, "kind": kind, "task": task, "request": request,
                  "report_snapshot": report_snapshot, "source_revision": source_revision,
                  "expires_at": expires_at, "created_at": timezone.now()})
    if (notification.owner_id, notification.kind, notification.task_id, notification.request_id, notification.report_snapshot_id, notification.source_revision) != (
            owner.pk, kind, getattr(task, "pk", None), getattr(request, "pk", None), getattr(report_snapshot, "pk", None), source_revision):
        raise ValueError("The attention deduplication key belongs to another source.")
    if created or (not notification.cancelled_at and not notification.published_at):
        enqueue_delivery(recipient=owner, channel="IN_APP", dedupe_key=f"notify:{notification.pk}:in-app", notification=notification)
    return notification


@transaction.atomic
def enqueue_delivery(*, recipient, channel, dedupe_key, notification=None, report_snapshot=None, run_after=None):
    if channel not in NotificationDelivery.Channel.values or bool(notification) == bool(report_snapshot):
        raise ValidationError("Choose one supported delivery source and channel.")
    if channel == "IN_APP" and (notification is None or notification.owner_id != recipient.pk):
        raise ValidationError("An in-app delivery belongs to the notification's authorized owner.")
    delivery, created = NotificationDelivery.objects.get_or_create(dedupe_key=dedupe_key,
        defaults={"recipient": recipient, "channel": channel, "notification": notification,
                  "report_snapshot": report_snapshot, "next_attempt_at": run_after or timezone.now(),
                  "queued_at": timezone.now()})
    if (delivery.recipient_id, delivery.channel, delivery.notification_id, delivery.report_snapshot_id) != (
            recipient.pk, channel, getattr(notification, "pk", None), getattr(report_snapshot, "pk", None)):
        raise ValueError("The delivery deduplication key belongs to another source.")
    if delivery.state not in TERMINAL_DELIVERIES:
        from tasker.services.jobs import enqueue_job
        enqueue_job(kind="notification.deliver" if channel == "IN_APP" else "reports.deliver",
            dedupe_key=f"delivery:{delivery.pk}", payload={"delivery_id": delivery.pk}, run_after=run_after, owner=recipient)
    return delivery


def delivery_window(*, recipient, now=None):
    """Return (earliest available instant, safe reason), never private notes."""
    now = now or timezone.now()
    from tasker.models import TaskTimeEntry, WorkPolicy
    preference = effective_notification_preferences(recipient)["record"]
    zone = ZoneInfo(preference.timezone_name)
    local = now.astimezone(zone)
    if preference.quiet_enabled:
        for day in (local.date()-timedelta(days=1), local.date()):
            end_day = day + timedelta(days=1) if preference.quiet_end < preference.quiet_start else day
            try:
                left = aware_local(day, preference.quiet_start, zone)
                right = aware_local(end_day, preference.quiet_end, zone)
            except ValidationError:
                return now+timedelta(hours=1), "quiet_calendar_review"
            if left <= now < right:
                return right, "quiet_hours"
    if preference.suppress_during_focus and TaskTimeEntry.objects.filter(user=recipient, ended_at__isnull=True).exists():
        return now+timedelta(minutes=5), "focus_active"
    if preference.respect_work_hours:
        from tasker.models import Availability
        unavailable = list(Availability.objects.filter(owner=recipient, starts_at__lte=now, ends_at__gt=now).values_list("ends_at", flat=True))
        if unavailable:
            return max(unavailable), "unavailable_window"
        policy = WorkPolicy.objects.filter(owner=recipient).first()
        if not policy or not policy.configured:
            return now+timedelta(hours=6), "work_hours_unknown"
        work_zone = ZoneInfo(policy.timezone_name)
        work_day = now.astimezone(work_zone).date()
        try:
            today = work_windows(policy, work_day) or []
            if any(left <= now < right for left, right in today):
                return now, ""
            for offset in range(9):
                intervals = work_windows(policy, work_day+timedelta(days=offset)) or []
                starts = [left for left, _ in intervals if left > now]
                if starts:
                    return min(starts), "outside_work_hours"
        except ValidationError:
            return now+timedelta(hours=1), "work_calendar_review"
        return now+timedelta(days=1), "no_work_window"
    return now, ""


def _cancel(delivery, notification=None, code="source_unavailable"):
    delivery.state, delivery.error_code, delivery.next_attempt_at = "CANCELLED", code, None
    delivery.save(update_fields=["state", "error_code", "next_attempt_at"])
    if notification and not notification.cancelled_at:
        notification.cancelled_at = timezone.now()
        notification.save(update_fields=["cancelled_at"])


def _held(delivery, instant, reason):
    from tasker.services.jobs import JobDeferred
    delivery.state, delivery.error_code, delivery.next_attempt_at = "HELD", reason, instant
    delivery.save(update_fields=["state", "error_code", "next_attempt_at"])
    return JobDeferred(run_after=instant, reason=reason)


def _lock_notification_sources(notification_ids):
    """Use Task -> scope -> Notification -> Delivery throughout attention writes."""
    from accounting.models import Company
    from tasker.models import Task, Team
    sources = Notification.objects.filter(pk__in=notification_ids).values_list("task_id", "request__task_id")
    task_ids = {pk for pair in sources for pk in pair if pk is not None}
    tasks = list(Task.all_objects.select_for_update().filter(pk__in=task_ids).order_by("pk"))
    list(Company.objects.select_for_update().filter(pk__in={task.company_id for task in tasks if task.company_id}).order_by("pk"))
    list(Team.all_objects.select_for_update().filter(pk__in={task.team_id for task in tasks if task.team_id}).order_by("pk"))


@transaction.atomic
def deliver_notification(payload, job=None):
    seed = NotificationDelivery.objects.select_related("recipient", "notification").filter(pk=payload.get("delivery_id"), channel="IN_APP").first()
    if seed is None or seed.state in TERMINAL_DELIVERIES:
        return {"noop": True}
    # Focus starts and preference saves acquire this actor lock first too.
    get_user_model().objects.select_for_update(no_key=True).get(pk=seed.recipient_id)
    preference = effective_notification_preferences(seed.recipient)["record"]
    if preference.digest_mode == "HOURLY" and seed.notification and seed.notification.kind in DIGEST_KINDS:
        return deliver_digest(payload, job=job)
    _lock_notification_sources([seed.notification_id])
    list(Notification.objects.select_for_update().filter(pk=seed.notification_id))
    delivery = NotificationDelivery.objects.select_for_update(of=("self",)).select_related("notification__request__task__company", "notification__request__task__team",
        "notification__task", "recipient").filter(pk=seed.pk, channel="IN_APP").first()
    if delivery is None or delivery.state in TERMINAL_DELIVERIES:
        return {"noop": True}
    notification = delivery.notification
    if notification is None or notification.owner_id != delivery.recipient_id or not _source_authorized(notification):
        _cancel(delivery, notification)
        return {"cancelled": True}
    now = timezone.now()
    instant, reason = delivery_window(recipient=delivery.recipient, now=now)
    if instant > now:
        return _held(delivery, instant, reason)
    from tasker.services.jobs import assert_current_job_lease
    if job is not None:
        assert_current_job_lease()
    notification.published_at = notification.published_at or now
    notification.save(update_fields=["published_at"])
    delivery.state, delivery.error_code, delivery.next_attempt_at = "DELIVERED", "", None
    delivery.attempt_count += 1
    delivery.attempted_at = delivery.accepted_at = delivery.delivered_at = now
    delivery.save()
    return {"delivered": 1}


@transaction.atomic
def deliver_digest(payload, job=None):
    source = NotificationDelivery.objects.select_related("recipient", "notification").filter(pk=payload.get("delivery_id"), channel="IN_APP").first()
    if source is None or source.state in TERMINAL_DELIVERIES:
        return {"noop": True}
    get_user_model().objects.select_for_update(no_key=True).get(pk=source.recipient_id)
    now = timezone.now()
    # Next wall-clock hour after the original alert; repeated jobs share it.
    zone = ZoneInfo(effective_notification_preferences(source.recipient)["record"].timezone_name)
    local = source.notification.created_at.astimezone(zone)
    bucket = (local.replace(minute=0, second=0, microsecond=0)+timedelta(hours=1)).astimezone(datetime_timezone.utc)
    instant, reason = delivery_window(recipient=source.recipient, now=now)
    hold = instant > now or now < bucket
    if hold:
        pending_ids = [(source.pk, source.notification_id)]
    else:
        pending_query = NotificationDelivery.objects.filter(recipient=source.recipient, channel="IN_APP",
            state__in=("QUEUED", "HELD"), notification__created_at__lt=bucket, notification__grouped_into__isnull=True,
            notification__kind__in=DIGEST_KINDS).order_by("pk")
        pending_ids = list(pending_query.values_list("pk", "notification_id")[:100])
        if source.pk not in {pk for pk, _ in pending_ids}:
            pending_ids = pending_ids[:99]+[(source.pk, source.notification_id)]
    _lock_notification_sources([item[1] for item in pending_ids])
    list(Notification.objects.select_for_update().filter(pk__in=[item[1] for item in pending_ids]).order_by("pk"))
    pending = list(NotificationDelivery.objects.select_for_update(of=("self",)).filter(pk__in=[item[0] for item in pending_ids]).order_by("pk").select_related("notification__request__task__company", "notification__request__task__team", "notification__task"))
    valid = []
    for delivery in pending:
        if delivery.state in TERMINAL_DELIVERIES:
            continue
        if delivery.notification and delivery.notification.owner_id == delivery.recipient_id and _source_authorized(delivery.notification):
            valid.append(delivery)
        else:
            _cancel(delivery, delivery.notification)
    if not valid:
        return {"delivered": 0}
    if hold:
        return _held(valid[0], instant, reason) if instant > now else _held(valid[0], bucket, "digest_wait")
    from tasker.services.jobs import assert_current_job_lease
    if job is not None:
        assert_current_job_lease()
    key = f"digest:{source.recipient_id}:{bucket.isoformat()}"
    digest, _ = Notification.objects.get_or_create(dedupe_key=key, defaults={"owner": source.recipient,
        "kind": "DIGEST", "created_at": now, "published_at": now, "summary_count": len(valid)})
    Notification.objects.filter(pk__in=[item.notification_id for item in valid]).update(grouped_into=digest, published_at=now)
    digest.summary_count = digest.digest_items.filter(cancelled_at__isnull=True).count()
    digest.save(update_fields=["summary_count"])
    NotificationDelivery.objects.get_or_create(dedupe_key=f"notify:{digest.pk}:in-app", defaults={
        "notification": digest, "recipient": source.recipient, "channel": "IN_APP", "state": "DELIVERED",
        "attempt_count": 1, "queued_at": now, "attempted_at": now, "accepted_at": now, "delivered_at": now})
    for delivery in valid:
        delivery.state, delivery.error_code, delivery.next_attempt_at = "DELIVERED", "", None
        delivery.attempt_count += 1
        delivery.attempted_at = delivery.accepted_at = delivery.delivered_at = now
        delivery.save()
    return {"delivered": len(valid), "digest_id": digest.pk}


def reconcile_notifications(*, now=None, limit=200):
    now = now or timezone.now()
    counts = {"checked": 0, "cancelled": 0, "queued": 0}
    ids = list(NotificationDelivery.objects.filter(channel="IN_APP", state__in=("QUEUED", "HELD", "FAILED")).order_by(F("last_checked_at").asc(nulls_first=True), "pk").values_list("pk", flat=True)[:max(1, min(limit, 1000))])
    for pk in ids:
        with transaction.atomic():
            seed = NotificationDelivery.objects.only("notification_id").get(pk=pk)
            _lock_notification_sources([seed.notification_id])
            if seed.notification_id:
                Notification.objects.select_for_update().get(pk=seed.notification_id)
            delivery = NotificationDelivery.objects.select_for_update(of=("self",)).select_related("notification__request__task__company", "notification__request__task__team", "notification__task", "recipient").get(pk=pk)
            NotificationDelivery.objects.filter(pk=pk).update(last_checked_at=now)
            counts["checked"] += 1
            if delivery.state in TERMINAL_DELIVERIES:
                continue
            if not delivery.notification or not _source_authorized(delivery.notification):
                _cancel(delivery, delivery.notification)
                counts["cancelled"] += 1
            elif delivery.state in ("QUEUED", "HELD"):
                enqueue_delivery(recipient=delivery.recipient, channel="IN_APP", dedupe_key=delivery.dedupe_key,
                    notification=delivery.notification, run_after=delivery.next_attempt_at or now)
                counts["queued"] += 1
    return counts
