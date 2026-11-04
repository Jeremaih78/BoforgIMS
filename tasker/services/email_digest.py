"""Reference-only reminder digests. In-app grouping and acknowledgement stay separate."""

from collections import defaultdict
from datetime import timedelta
from zoneinfo import ZoneInfo

from django.db import transaction
from django.urls import reverse
from django.utils import timezone

from tasker.cloud_email_models import StaffEmailBinding, StaffEmailPreference
from tasker.email_protocol import digest
from tasker.notification_models import Notification, NotificationDelivery


def members(binding):
    return list(
        StaffEmailBinding.objects.filter(pk__in=binding.digest_members)
        .select_related(
            "policy__company",
            "delivery__recipient",
            "delivery__notification__task",
            "delivery__notification__request",
            "delivery__notification__business_link",
        )
        .order_by("pk")
    )


def source_digest(items):
    return digest([{"id": str(item.pk), "source_digest": item.source_digest} for item in items])


def digest_eligibility(binding, *, now):
    from .cloud_email import eligibility

    preference = StaffEmailPreference.objects.filter(owner=binding.delivery.recipient).first()
    if not preference or not preference.digest_reminders:
        return "deny", "digest_preference_disabled", None
    items = members(binding)
    if not items or len(items) != len(binding.digest_members):
        return "deny", "digest_empty", None
    deferred = []
    for item in items:
        if (
            item.policy_id != binding.policy_id
            or item.delivery.recipient_id != binding.delivery.recipient_id
            or item.category not in ("due_soon", "overdue")
        ):
            return "deny", "digest_source_mismatch", None
        decision, reason, instant = eligibility(item, now=now)
        if decision == "deny":
            return "deny", "digest_item_obsolete", None
        if decision == "defer":
            deferred.append((instant, reason))
    if source_digest(items) != binding.source_digest:
        return "deny", "digest_revision_changed", None
    if deferred:
        instant, reason = max(deferred)
        return "defer", reason, instant
    if binding.payload_digest and digest(digest_envelope(binding)) != binding.payload_digest:
        return "deny", "digest_payload_changed", None
    return "allow", "", None


def digest_envelope(binding):
    from django.conf import settings
    from urllib.parse import urlsplit

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
        raise ValueError("invalid_action_origin")
    return {
        "schema_version": "2",
        "guard": {"delivery_id": str(binding.pk), "company_id": str(binding.policy.company_id)},
        "template": "ims.staff.digest",
        "external_reference": f"ims:staff:{binding.pk}",
        "recipients": [binding.delivery.recipient.email],
        "context": {
            "notification_title": f"{len(binding.digest_members)} pending work reminders",
            "action_url": origin.rstrip("/")
            + reverse(
                "ims:tasker:task_list",
                urlconf=getattr(settings, "TASKER_EMAIL_URLCONF", "config.host_urls.ai"),
            ),
            "assigned_by": "",
            "priority": "",
            "due_date": "",
        },
    }


@transaction.atomic
def refresh_before_submission(binding):
    """Remove invalid items before immutable admission; never rewrite a queued payload."""
    from .cloud_email import eligibility

    if binding.payload or binding.phase != "held":
        return
    valid = []
    for item in members(binding):
        if eligibility(item)[0] == "deny":
            item.delivery.state = "CANCELLED"
            item.delivery.save(update_fields=["state"])
        else:
            valid.append(item)
    binding.digest_members = [str(item.pk) for item in valid]
    binding.source_digest = source_digest(valid)
    binding.save(update_fields=["digest_members", "source_digest"])


@transaction.atomic
def prepare_digests(*, limit=100):
    from .cloud_email import bind_delivery, eligibility
    from .notifications import effective_notification_preferences

    now = timezone.now()
    candidates = (
        StaffEmailBinding.objects.select_for_update(skip_locked=True, of=("self",))
        .filter(
            phase="held",
            category__in=("due_soon", "overdue"),
            created_at__lte=now - timedelta(hours=1),
            delivery__state__in=("QUEUED", "HELD"),
            delivery__recipient__staffemailpreference__digest_reminders=True,
        )
        .select_related("policy__company", "delivery__recipient", "delivery__notification__task")
        .order_by("created_at", "pk")[:limit]
    )
    groups = defaultdict(list)
    for item in candidates:
        if eligibility(item, now=now)[0] == "allow":
            groups[(item.policy_id, item.delivery.recipient_id)].append(item)
    for (policy_id, recipient_id), items in groups.items():
        zone = effective_notification_preferences(items[0].delivery.recipient)[
            "record"
        ].timezone_name
        bucket = (
            now.astimezone(ZoneInfo(zone)).replace(minute=0, second=0, microsecond=0).isoformat()
        )
        occurrence = f"email-digest:{policy_id}:{recipient_id}:{bucket}:{digest(sorted(str(i.pk) for i in items))[:24]}"
        note, _ = Notification.objects.get_or_create(
            dedupe_key=occurrence,
            defaults={"owner_id": recipient_id, "kind": "EMAIL_DIGEST", "created_at": now},
        )
        delivery, _ = NotificationDelivery.objects.get_or_create(
            dedupe_key=occurrence,
            defaults={
                "recipient_id": recipient_id,
                "notification": note,
                "channel": "EMAIL",
                "state": "HELD",
                "queued_at": now,
            },
        )
        master = bind_delivery(delivery, items[0].policy, "digest", occurrence)
        if master.phase != "held":
            continue
        master.digest_members = [str(item.pk) for item in items]
        master.source_digest = source_digest(sorted(items, key=lambda row: row.pk))
        master.save(update_fields=["digest_members", "source_digest"])
        StaffEmailBinding.objects.filter(pk__in=master.digest_members).update(phase="digest_member")


def apply_member_state(binding, state, accepted_at, *, source_invalidated=False):
    from .cloud_email import eligibility

    for item in members(binding):
        if state == "CANCELLED" and source_invalidated and eligibility(item)[0] == "allow":
            # Confirmed unsent cancellation permits a new digest identity. An
            # UNKNOWN or accepted digest never releases members for resending.
            item.phase = "held"
            item.save(update_fields=["phase"])
            item.delivery.state = "HELD"
        else:
            item.delivery.state = state
        if accepted_at:
            item.delivery.accepted_at = item.delivery.accepted_at or accepted_at
        item.delivery.save(update_fields=["state", "accepted_at"])
