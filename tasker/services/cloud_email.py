"""Prospective staff/report intents, one Cloud identity, and current eligibility."""

import uuid
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import DatabaseError, connection, transaction
from django.utils import timezone

from tasker.cloud_email_models import (
    StaffEmailBinding,
    StaffEmailPolicy,
    StaffEmailPreference,
    StaffEmailReceipt,
)
from tasker.email_protocol import SubmissionReceipt, digest
from tasker.notification_models import NotificationDelivery
from .jobs import JobCancelled, JobDeferred, enqueue_job

CATEGORIES = {
    "ASSIGNMENT": "assignment",
    "REQUEST": "request",
    "ESCALATION": "escalation",
    "TASK_UPDATE": "update",
    "BUSINESS_ACTION": "business",
    "DUE_SOON": "due_soon",
    "OVERDUE": "overdue",
}
PENDING = ("QUEUED", "HELD", "EXTERNAL_QUEUED")


def transport_bound(delivery_id):
    """Fence every legacy transport, including when the new feature gate is off."""
    try:
        if StaffEmailBinding._meta.db_table not in connection.introspection.table_names():
            return False
        return StaffEmailBinding.objects.filter(delivery_id=delivery_id).exists()
    except DatabaseError:
        return True


def unsettled_report_transport(company_id):
    try:
        if StaffEmailBinding._meta.db_table not in connection.introspection.table_names():
            return False
        return (
            StaffEmailBinding.objects.filter(delivery__report_snapshot__team__company_id=company_id)
            .exclude(delivery__state__in=("PROVIDER_ACCEPTED", "CANCELLED"))
            .exclude(phase__in=("admission_rejected", "cancelled"))
            .exclude(phase="held", payload_digest="", remote_message_id__isnull=True)
            .exists()
        )
    except DatabaseError:
        return True


def task_digest(task):
    return digest(
        {
            name: str(getattr(task, name, ""))
            for name in (
                "pk",
                "owner_id",
                "company_id",
                "team_id",
                "title",
                "priority",
                "due_date",
                "due_time",
                "status",
                "blocked_reason",
                "archived",
                "is_deleted",
            )
        }
    )


def source_digest(delivery):
    if delivery.report_snapshot_id:
        return delivery.report_snapshot.source_digest
    note = delivery.notification
    if note.business_link_id:
        return str(note.business_link.revision)
    return task_digest(note.task)


@transaction.atomic
def bind_delivery(delivery, policy, category, occurrence):
    binding, created = StaffEmailBinding.objects.get_or_create(
        delivery=delivery,
        defaults={
            "policy": policy,
            "category": category,
            "occurrence": occurrence,
            "source_digest": source_digest(delivery),
            "created_at": delivery.queued_at,
        },
    )
    if (
        binding.policy_id != policy.pk
        or binding.category != category
        or binding.occurrence != occurrence
    ):
        raise ValidationError("Transport binding belongs to another intent.")
    if created:
        enqueue_job(
            kind="email.cloud",
            dedupe_key=f"cloud-email:{binding.pk}",
            payload={"delivery_id": delivery.pk},
            owner=delivery.recipient,
        )
    return binding


@transaction.atomic
def queue_notification_email(notification):
    if not getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False):
        return None
    category = CATEGORIES.get(notification.kind)
    company_id = (
        notification.business_link.company_id
        if notification.business_link_id
        else notification.task.company_id
        if notification.task_id
        else None
    )
    policy = StaffEmailPolicy.objects.filter(company_id=company_id).first() if company_id else None
    if not category or not policy:
        return None
    delivery, _ = NotificationDelivery.objects.get_or_create(
        dedupe_key=f"staff:{notification.pk}:email",
        defaults={
            "recipient": notification.owner,
            "channel": "EMAIL",
            "notification": notification,
            "state": "HELD",
            "queued_at": notification.created_at,
        },
    )
    return bind_delivery(delivery, policy, category, notification.dedupe_key)


@transaction.atomic
def record_task_change(task, previous=None):
    """Call inside authorized task service transactions; arbitrary model saves do nothing."""
    from .notifications import create_notification

    if not getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False):
        return None
    from tasker.models import Task, TaskActivity

    task = Task.all_objects.select_for_update().get(pk=task.pk)
    if (
        not task.company_id
        or not StaffEmailPolicy.objects.filter(company_id=task.company_id).exists()
    ):
        return None
    if previous is not None:
        obsolete = StaffEmailBinding.objects.filter(
            delivery__notification__task=task, delivery__state__in=PENDING
        ).exclude(source_digest=task_digest(task))
        obsolete.update(cancel_requested=True, error_code="source_changed")
    assigned = previous is None or previous.owner_id != task.owner_id
    changed = previous is not None and any(
        getattr(previous, name, None) != getattr(task, name, None)
        for name in ("due_date", "due_time", "priority", "blocked_reason")
    )
    changed = changed or (
        previous is not None and previous.status != task.status and task.status == "BLOCKED"
    )
    if not assigned and not changed:
        return None
    if task.archived or task.is_deleted or task.status in ("COMPLETED", "CANCELLED"):
        return None
    event_id = (
        TaskActivity.objects.filter(task=task).order_by("-pk").values_list("pk", flat=True).first()
    )
    if event_id is None:
        return None
    return create_notification(
        owner=task.owner,
        kind="ASSIGNMENT" if assigned else "TASK_UPDATE",
        task=task,
        source_revision=task_digest(task),
        dedupe_key=f"task-email:{task.pk}:activity:{event_id}",
    )


def eligibility(binding, *, now=None):
    """No network or secrets. Deny obsolete sources; defer policy/calendar holds."""
    from . import notifications, reporting, report_email

    now = now or timezone.now()
    delivery, policy = binding.delivery, binding.policy
    actor = delivery.recipient
    if binding.cancel_requested or not actor.is_active or not policy.company.is_active:
        return "deny", "source_unavailable", None
    if not actor.has_perm("tasker.view_task"):
        return "deny", "recipient_access_changed", None
    if binding.category == "digest":
        from .email_digest import digest_eligibility

        return digest_eligibility(binding, now=now)
    if delivery.report_snapshot_id:
        preference = StaffEmailPreference.objects.filter(owner=actor).first()
        if preference and not preference.reports:
            return "deny", "email_preference_disabled", None
        if report_email._check(delivery):
            return "deny", "report_sources_changed", None
        report_policy = reporting._policy(delivery.report_snapshot.team)
        if (
            not getattr(settings, "TASKER_REPORT_EMAIL_LIVE_ENABLED", False)
            or not getattr(settings, "TASKER_REPORTS_ENABLED", False)
            or not getattr(settings, "TASKER_IMPROVEMENT_ENABLED", False)
            or not report_policy
            or not report_policy.configured
            or not report_policy.email_enabled
            or report_policy.email_mode != "CLOUD"
        ):
            return "defer", "report_activation_pending", now + timedelta(hours=1)
        local = now.astimezone(ZoneInfo(report_policy.timezone_name))
        if delivery.report_snapshot.report_date.weekday() not in report_policy.weekdays:
            return "deny", "outside_reporting_days", None
        if (
            report_policy.consolidation == "AT_CUTOFF"
            and local.date() == delivery.report_snapshot.report_date
        ):
            from .capacity import aware_local

            cutoff = aware_local(
                local.date(), report_policy.cutoff_time, report_policy.timezone_name
            )
            if now < cutoff:
                return "defer", "awaiting_report_cutoff", cutoff
    else:
        note = delivery.notification
        if (
            note.cancelled_at
            or note.acknowledged_at
            or (note.expires_at and note.expires_at <= now)
            or not notifications._source_authorized(note)
        ):
            return "deny", "notification_obsolete", None
        if note.task_id:
            task = note.task
            if task.archived or task.is_deleted or task.status in ("COMPLETED", "CANCELLED"):
                return "deny", "task_closed", None
            if (
                binding.category in ("assignment", "update", "due_soon", "overdue")
                and task.owner_id != actor.pk
            ):
                return "deny", "assignment_changed", None
        if note.request_id:
            request = note.request
            if request.status != "OPEN" or str(request.revision) != note.source_revision:
                return "deny", "request_resolved", None
            if binding.category == "request" and request.resolver_id != actor.pk:
                return "deny", "resolver_changed", None
            if binding.category == "escalation" and request.escalated_to_id != actor.pk:
                return "deny", "resolver_changed", None
            if binding.category == "escalation":
                from tasker.models import TeamMembership

                if not TeamMembership.objects.filter(
                    team_id=request.source_team_id, user=actor, role="MANAGER", is_active=True
                ).exists():
                    return "deny", "escalation_authority_changed", None
        pref = StaffEmailPreference.objects.filter(owner=actor).first()
        field = (
            "reminders"
            if binding.category in ("due_soon", "overdue")
            else "assignments"
            if binding.category == "assignment"
            else "requests"
            if binding.category in ("request", "escalation")
            else "updates"
        )
        if pref and not getattr(pref, field):
            return "deny", "email_preference_disabled", None
        if binding.category in ("due_soon", "overdue") and (
            not pref or not pref.reminders or not policy.reminders_enabled
        ):
            return "defer", "reminders_disabled", now + timedelta(hours=1)
        if binding.category in ("due_soon", "overdue"):
            from .notifications import effective_notification_preferences

            zone = effective_notification_preferences(actor)["record"].timezone_name
            day = now.astimezone(ZoneInfo(zone)).date()
            if (
                not note.task.due_date
                or note.task.due_date > day + timedelta(days=policy.due_soon_days)
                or binding.category == "overdue"
                and binding.occurrence.rsplit(":", 1)[-1] != str(day)
                or binding.category == "due_soon"
                and note.task.due_date < day
            ):
                return "deny", "reminder_occurrence_expired", None
    if source_digest(delivery) != binding.source_digest:
        return "deny", "source_revision_changed", None
    if not policy.activation_after or binding.created_at < policy.activation_after:
        return "deny", "before_activation_watermark", None
    if (
        not getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False)
        or not policy.enabled
        or not policy.tenant_id
        or not policy.client_id
        or binding.category not in policy.categories
    ):
        return "defer", "cloud_activation_pending", now + timedelta(hours=1)
    instant, reason = notifications.delivery_window(recipient=actor, now=now)
    if instant > now:
        return "defer", reason, instant
    try:
        validate_email(actor.email)
    except ValidationError:
        return "deny", "recipient_email_missing", None
    if binding.payload_digest:
        try:
            if digest(envelope(binding)) != binding.payload_digest:
                return "deny", "payload_sources_changed", None
        except Exception:
            return "deny", "payload_sources_changed", None
    return "allow", "", None


@contextmanager
def email_routes():
    from django.urls import get_urlconf, set_urlconf

    previous = get_urlconf()
    set_urlconf(getattr(settings, "TASKER_EMAIL_URLCONF", "config.host_urls.ai"))
    try:
        yield
    finally:
        set_urlconf(previous)


@email_routes()
def envelope(binding):
    if binding.category == "digest":
        from .email_digest import digest_envelope

        return digest_envelope(binding)
    delivery = binding.delivery
    note = delivery.notification
    source = delivery.report_snapshot if delivery.report_snapshot_id else note
    origin = getattr(settings, "TASKER_EMAIL_ORIGIN", "")
    parsed = urlsplit(origin)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise ValidationError("Configure the approved IMS HTTPS action origin.")
    if delivery.report_snapshot_id:
        from django.urls import reverse

        path = reverse("ims:tasker:report_detail", args=(source.pk,))
        title = "A current team report is ready in IMS"
        task = None
    else:
        from .notifications import notification_context

        context = notification_context(note, delivery.recipient)
        path = context.get("url", "")
        title = context["title"][:200]
        task = note.task
    if not path.startswith("/") or path.startswith("//"):
        raise ValidationError("A local authenticated IMS action route is required.")
    return {
        "schema_version": "2",
        "guard": {"delivery_id": str(binding.pk), "company_id": str(binding.policy.company_id)},
        "template": f"ims.staff.{binding.category}",
        "external_reference": f"ims:staff:{binding.pk}",
        "recipients": [delivery.recipient.email],
        "context": {
            "notification_title": title,
            "action_url": origin.rstrip("/") + path,
            "assigned_by": str(getattr(task, "assigned_by", "") or ""),
            "priority": str(getattr(task, "priority", "") or ""),
            "due_date": str(getattr(task, "due_date", "") or ""),
        },
    }


def configured_client(policy):
    from cloud_communications.client import CommunicationsClient
    from .cloud_email_client import CloudEmailAdapter

    config = getattr(settings, "TASKER_CLOUD_EMAIL_CLIENTS", {}).get(str(policy.company_id), {})
    token = Path(config["token_file"]).read_text(encoding="utf-8").strip()
    return CloudEmailAdapter(CommunicationsClient(config["url"], token))


@transaction.atomic
def apply_receipt(binding_id, event_id, value):
    binding = (
        StaffEmailBinding.objects.select_for_update()
        .select_related("delivery", "policy")
        .get(pk=binding_id)
    )
    previous = StaffEmailReceipt.objects.filter(event_id=event_id).first()
    if previous:
        if previous.binding_id != binding.pk:
            raise ValidationError("Receipt event belongs to another delivery.")
        if previous.payload != value:
            previous.quarantined = True
            previous.save(update_fields=["quarantined"])
        return False
    receipt = SubmissionReceipt.parse(value)
    collision = (
        StaffEmailBinding.objects.filter(remote_message_id=receipt.message_id)
        .exclude(pk=binding.pk)
        .exists()
    )
    valid = not collision and (
        receipt.tenant_id == binding.policy.tenant_id
        and receipt.client_id == str(binding.policy.client_id)
        and receipt.delivery_id == str(binding.pk)
        and receipt.payload_digest == binding.payload_digest
        and (not binding.remote_message_id or str(binding.remote_message_id) == receipt.message_id)
    )
    conflict = (
        receipt.state_version == binding.remote_version and receipt.state != binding.remote_state
    )
    if receipt.state_version == binding.remote_version:
        for fact in binding.receipts.filter(
            quarantined=False, payload__state_version=receipt.state_version
        ):
            known = SubmissionReceipt.parse(fact.payload)
            from django.utils.dateparse import parse_datetime

            conflict = conflict or (
                known.provider_acceptance != receipt.provider_acceptance
                or parse_datetime(known.accepted_at or "")
                != parse_datetime(receipt.accepted_at or "")
                or bool(
                    known.attempt_id
                    and receipt.attempt_id
                    and known.attempt_id != receipt.attempt_id
                )
            )
    StaffEmailReceipt.objects.create(
        event_id=event_id, binding=binding, payload=value, quarantined=not valid or conflict
    )
    if not valid or conflict or receipt.state_version <= binding.remote_version:
        return False
    if binding.delivery.accepted_at and receipt.state != "smtp_accepted":
        StaffEmailReceipt.objects.filter(event_id=event_id).update(quarantined=True)
        return False
    binding.remote_message_id, binding.remote_version, binding.remote_state = (
        receipt.message_id,
        receipt.state_version,
        receipt.state,
    )
    binding.phase, binding.checked_at = "admitted", timezone.now()
    binding.save()
    delivery = binding.delivery
    delivery.state = {
        "smtp_accepted": "PROVIDER_ACCEPTED",
        "unknown": "UNKNOWN",
        "cancelled": "CANCELLED",
        "rejected": "FAILED",
        "failed": "FAILED",
    }.get(receipt.state, "EXTERNAL_QUEUED")
    if receipt.provider_acceptance == "UNKNOWN":
        delivery.state = "UNKNOWN"
    if receipt.state == "smtp_accepted":
        from django.utils.dateparse import parse_datetime

        accepted = parse_datetime(receipt.accepted_at or "")
        if not accepted or timezone.is_naive(accepted):
            raise ValidationError("Acceptance requires a provider acceptance timestamp.")
        delivery.accepted_at = delivery.accepted_at or accepted
    delivery.save(update_fields=["state", "accepted_at"])
    if binding.category == "digest":
        from .email_digest import apply_member_state

        apply_member_state(
            binding,
            delivery.state,
            delivery.accepted_at,
            source_invalidated=binding.cancel_requested
            or value.get("error_code")
            in {"source_no_longer_eligible", "source_dispatch_cancelled"},
        )
    return True


def handle_cloud_delivery(payload, job=None, client=None):
    binding = (
        StaffEmailBinding.objects.select_related(
            "policy__company",
            "delivery__recipient",
            "delivery__notification__task",
            "delivery__notification__request",
            "delivery__notification__business_link",
            "delivery__report_snapshot__team__company",
        )
        .filter(delivery_id=payload.get("delivery_id"))
        .first()
    )
    if not binding:
        return JobCancelled("cloud_binding_missing")
    if binding.phase == "cancelled":
        return JobCancelled("intent_cancelled")
    if binding.phase in ("admission_rejected", "conflict"):
        return JobCancelled("cloud_admission_review_required")
    if binding.category == "digest" and binding.phase == "held":
        from .email_digest import refresh_before_submission

        refresh_before_submission(binding)
    if binding.phase == "digest_member":
        return {"digest_member": 1}
    if binding.category in ("due_soon", "overdue") and binding.phase == "held":
        pref = StaffEmailPreference.objects.filter(owner=binding.delivery.recipient).first()
        if pref and pref.digest_reminders:
            return JobDeferred(
                run_after=timezone.now() + timedelta(hours=1), reason="awaiting_reminder_digest"
            )
    if binding.phase in ("submitting", "admitted"):
        try:
            client = client or configured_client(binding.policy)
            result = client.request(
                "GET", f"/api/v1/communications/messages/lookup?key={binding.pk}"
            )
            event_id = uuid.uuid5(uuid.NAMESPACE_URL, digest(result))
            apply_receipt(binding.pk, event_id, result)
            if StaffEmailReceipt.objects.get(event_id=event_id).quarantined:
                return JobDeferred(
                    run_after=timezone.now() + timedelta(minutes=5),
                    reason="cloud_receipt_quarantined",
                )
            binding.refresh_from_db()
            if result["state_version"] < binding.remote_version:
                return JobDeferred(
                    run_after=timezone.now() + timedelta(minutes=1),
                    reason="cloud_receipt_superseded",
                )
            StaffEmailBinding.objects.filter(pk=binding.pk).update(checked_at=timezone.now())
            decision, _, _ = eligibility(binding)
            if (binding.cancel_requested or decision == "deny") and result["status"] in (
                "queued",
                "scheduled",
                "deferred",
            ):
                StaffEmailBinding.objects.filter(pk=binding.pk).update(cancel_requested=True)
                cancelled = client.request(
                    "POST",
                    f"/api/v1/communications/messages/{result['id']}/cancel",
                    {
                        "reason": "IMS source no longer eligible",
                        "expected_state_version": result["state_version"],
                    },
                )
                apply_receipt(
                    binding.pk, uuid.uuid5(uuid.NAMESPACE_URL, digest(cancelled)), cancelled
                )
            if result["status"] in ("queued", "scheduled", "processing", "deferred", "unknown"):
                return JobDeferred(
                    run_after=timezone.now() + timedelta(minutes=1), reason="cloud_receipt_pending"
                )
            return {"reconciled": 1}
        except Exception as error:
            if getattr(error, "status", None) == 404 and binding.payload:
                # This is an idempotent admission replay, never a new identity or
                # evidence that an earlier provider attempt did not happen.
                try:
                    result = client.submit(binding.payload, idempotency_key=str(binding.pk))
                    apply_receipt(
                        binding.pk, uuid.uuid5(uuid.NAMESPACE_URL, digest(result)), result
                    )
                except Exception:
                    pass
            return JobDeferred(
                run_after=timezone.now() + timedelta(minutes=5),
                reason="cloud_reconciliation_pending",
            )
    decision, reason, instant = eligibility(binding)
    if decision != "allow":
        with transaction.atomic():
            locked = StaffEmailBinding.objects.select_for_update().get(pk=binding.pk)
            locked.error_code = reason
            if decision == "deny":
                locked.phase = "cancelled"
            locked.save(update_fields=["error_code", "phase"])
            NotificationDelivery.objects.filter(pk=locked.delivery_id).update(
                state="CANCELLED" if decision == "deny" else "HELD"
            )
            if decision == "deny" and locked.category == "digest":
                from .email_digest import apply_member_state

                apply_member_state(locked, "CANCELLED", None, source_invalidated=True)
        return (
            JobCancelled(reason)
            if decision == "deny"
            else JobDeferred(run_after=instant, reason=reason)
        )
    try:
        value = envelope(binding)
        client = client or configured_client(binding.policy)
    except (KeyError, OSError, ValidationError):
        return JobDeferred(
            run_after=timezone.now() + timedelta(hours=1), reason="cloud_configuration_pending"
        )
    with transaction.atomic():
        locked = StaffEmailBinding.objects.select_for_update().get(pk=binding.pk)
        if locked.phase != "held":
            return {"noop": 1}
        locked.payload, locked.payload_digest, locked.phase = value, digest(value), "submitting"
        locked.save()
        NotificationDelivery.objects.filter(pk=locked.delivery_id).update(state="EXTERNAL_QUEUED")
    try:
        result = client.submit(value, idempotency_key=str(binding.pk))
        apply_receipt(binding.pk, uuid.uuid5(uuid.NAMESPACE_URL, digest(result)), result)
    except Exception as error:
        # Durable submitting state prevents transport fallback or a fresh identity.
        if getattr(error, "status", None) in (400, 401, 403, 409, 422):
            conflict = error.status == 409
            with transaction.atomic():
                StaffEmailBinding.objects.filter(pk=binding.pk).update(
                    phase="conflict" if conflict else "admission_rejected",
                    error_code="cloud_admission_review_required",
                )
                NotificationDelivery.objects.filter(pk=binding.delivery_id).update(
                    state="UNKNOWN" if conflict else "FAILED"
                )
            return JobCancelled("cloud_admission_review_required")
        return JobDeferred(
            run_after=timezone.now() + timedelta(minutes=5), reason="cloud_submission_unconfirmed"
        )
    return JobDeferred(
        run_after=timezone.now() + timedelta(minutes=1), reason="cloud_receipt_pending"
    )


@transaction.atomic
def repair_cloud_email_jobs(*, limit=100):
    """Recover the original reference job; never revive failed/cancelled budgets."""
    from django.db.models import F, Q
    from tasker.job_models import OutboxJob

    now = timezone.now()
    stale = (
        StaffEmailBinding.objects.filter(phase__in=("submitting", "admitted"))
        .exclude(delivery__state__in=("PROVIDER_ACCEPTED", "CANCELLED"))
        .filter(Q(checked_at__isnull=True) | Q(checked_at__lte=now - timedelta(minutes=5)))
        .select_related("delivery")
        .order_by(F("checked_at").asc(nulls_first=True), "pk")[:limit]
    )
    repaired = 0
    for binding in stale:
        key = f"cloud-email:{binding.pk}"
        job = OutboxJob.objects.select_for_update().filter(dedupe_key=key).first()
        if job is None:
            enqueue_job(
                kind="email.cloud",
                dedupe_key=key,
                payload={"delivery_id": binding.delivery_id},
                owner=binding.delivery.recipient_id,
            )
            repaired += 1
        elif (
            job.kind == "email.cloud"
            and job.payload == {"delivery_id": binding.delivery_id}
            and job.owner_id == binding.delivery.recipient_id
            and job.state == "SUCCEEDED"
            and job.attempts < job.max_attempts
        ):
            OutboxJob.objects.filter(pk=job.pk).update(
                state="QUEUED",
                run_after=now,
                finished_at=None,
                lease_token=None,
                lease_expires_at=None,
                locked_by="",
                last_error_code="cloud_status_repair",
                updated_at=now,
            )
            repaired += 1
        # Scheduling observation only; provider evidence remains in receipt rows.
        StaffEmailBinding.objects.filter(pk=binding.pk).update(checked_at=now)
    return repaired


@transaction.atomic
def schedule_reminders(*, limit=100):
    if not getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False):
        return 0
    repair_cloud_email_jobs(limit=limit)
    from tasker.models import Task
    from .notifications import create_notification, effective_notification_preferences

    count = 0
    policies = list(
        StaffEmailPolicy.objects.filter(reminders_enabled=True, company__is_active=True).order_by(
            "updated_at", "pk"
        )[:limit]
    )
    budget = max(1, limit // max(1, len(policies)))
    tasks = []
    for policy in policies:
        query = (
            Task.objects.filter(
                company_id=policy.company_id,
                archived=False,
                due_date__isnull=False,
                owner__is_active=True,
            )
            .exclude(status__in=("COMPLETED", "CANCELLED"))
            .order_by("pk")
        )
        batch = list(query.filter(pk__gt=policy.reminder_cursor)[:budget])
        if len(batch) < budget:
            batch.extend(query.filter(pk__lte=policy.reminder_cursor)[: budget - len(batch)])
        if batch:
            policy.reminder_cursor = batch[-1].pk
        policy.save(update_fields=["reminder_cursor", "updated_at"])
        tasks.extend(batch)
    for task in tasks:
        if not task.due_date:
            continue
        pref = effective_notification_preferences(task.owner)["record"]
        day = timezone.now().astimezone(ZoneInfo(pref.timezone_name)).date()
        policy = StaffEmailPolicy.objects.get(company_id=task.company_id)
        category = "OVERDUE" if task.due_date < day else "DUE_SOON"
        if task.due_date > day + timedelta(days=policy.due_soon_days):
            continue
        occurrence = str(day) if category == "OVERDUE" else str(task.due_date)
        create_notification(
            owner=task.owner,
            kind=category,
            task=task,
            source_revision=task_digest(task),
            dedupe_key=f"staff-reminder:{category}:{task.pk}:{task.owner_id}:{occurrence}",
        )
        count += 1
    from .email_digest import prepare_digests

    prepare_digests(limit=limit)
    return count
