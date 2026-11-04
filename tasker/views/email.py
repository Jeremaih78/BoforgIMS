"""Private staff diagnostics plus authenticated, fail-closed protocol endpoints."""

import json
import time
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required, permission_required
from django.db import transaction
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from tasker.cloud_email_models import (
    StaffEmailAuthorization,
    StaffEmailBinding,
    StaffEmailPreference,
)
from tasker.email_protocol import canonical, sign, verify
from tasker.services.cloud_email import apply_receipt, eligibility


def signed_request(request, *, receipt=False):
    if (
        request.method != "POST"
        or request.content_type != "application/json"
        or len(request.body) > 8192
        or (not request.is_secure() and not settings.DEBUG)
    ):
        raise ValueError("invalid_request")
    value = json.loads(request.body)
    if receipt and value.get("event_type") != "communication.status":
        raise ValueError("invalid_event_type")
    binding = StaffEmailBinding.objects.select_related(
        "policy__company",
        "delivery__recipient",
        "delivery__notification__task",
        "delivery__notification__request",
        "delivery__notification__business_link",
        "delivery__report_snapshot__team__company",
    ).get(pk=value["delivery_id"])
    key_setting = "TASKER_CLOUD_EMAIL_RECEIPT_KEYS" if receipt else "TASKER_CLOUD_EMAIL_KEYS"
    keys = getattr(settings, key_setting, {}).get(str(binding.policy.company_id), {})
    key_id = request.headers.get("X-Boforg-Key-ID", "current")
    verify(
        keys,
        key_id,
        request.headers.get("X-Boforg-Timestamp"),
        request.body,
        request.headers.get("X-Boforg-Signature"),
    )
    if (
        value.get("schema_version") != "2"
        or value.get("tenant_id") != binding.policy.tenant_id
        or value.get("client_id") != str(binding.policy.client_id)
        or value.get("payload_digest") != binding.payload_digest
    ):
        raise ValueError("invalid_binding")
    return value, binding, key_id, keys[key_id]


@csrf_exempt
def authorize(request):
    try:
        with transaction.atomic():
            value, seed, key_id, secret = signed_request(request)
            if value.get("company_id") != str(seed.policy.company_id):
                raise ValueError("invalid_binding")
            get_user_model().objects.select_for_update(no_key=True).get(
                pk=seed.delivery.recipient_id
            )
            if seed.delivery.report_snapshot_id:
                from tasker.services.report_email import _locked_delivery

                _locked_delivery(seed.delivery_id)
            else:
                from tasker.services.notifications import _lock_notification_sources

                source_ids = [seed.delivery.notification_id]
                if seed.digest_members:
                    source_ids.extend(
                        StaffEmailBinding.objects.filter(pk__in=seed.digest_members).values_list(
                            "delivery__notification_id", flat=True
                        )
                    )
                _lock_notification_sources(source_ids)
            from tasker.cloud_email_models import StaffEmailPolicy

            StaffEmailPolicy.objects.select_for_update().get(pk=seed.policy_id)
            binding = (
                StaffEmailBinding.objects.select_for_update(of=("self",))
                .select_related(
                    "policy__company",
                    "delivery__recipient",
                    "delivery__notification__task",
                    "delivery__notification__request",
                    "delivery__notification__business_link",
                    "delivery__report_snapshot__team__company",
                )
                .get(pk=seed.pk)
            )
            if binding.remote_message_id and str(binding.remote_message_id) != value["message_id"]:
                raise ValueError("invalid_message")
            if binding.phase not in ("submitting", "admitted"):
                raise ValueError("unregistered_dispatch")
            decision, reason, _ = eligibility(binding)
            expires = timezone.now() + timedelta(seconds=5)
            grant = StaffEmailAuthorization.objects.create(
                binding=binding,
                request_id=value["request_id"],
                message_id=value["message_id"],
                payload_digest=binding.payload_digest,
                decision=decision,
                reason=reason,
                expires_at=expires,
            )
            binding.remote_message_id = value["message_id"]
            binding.save(update_fields=["remote_message_id"])
            body = canonical(
                {
                    **value,
                    "decision": decision,
                    "reason": reason,
                    "expires_at": expires.isoformat(),
                    "authorization_ref": str(grant.pk),
                }
            )
        stamp = str(int(time.time()))
        response = HttpResponse(body, content_type="application/json")
        response["X-Boforg-Key-ID"], response["X-Boforg-Timestamp"] = key_id, stamp
        response["X-Boforg-Signature"] = sign(secret, stamp, body)
        response["Cache-Control"] = "no-store"
        return response
    except Exception:
        return JsonResponse({"error": "source_authorization_rejected"}, status=403)


@csrf_exempt
def receipts(request):
    try:
        value, binding, _, _ = signed_request(request, receipt=True)
        apply_receipt(binding.pk, value["event_id"], value)
    except Exception:
        return JsonResponse({"error": "receipt_rejected"}, status=403)
    return JsonResponse({"status": "stored"})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@transaction.atomic
def centre(request):
    get_user_model().objects.select_for_update(no_key=True).get(pk=request.user.pk)
    preference, _ = StaffEmailPreference.objects.get_or_create(owner=request.user)
    if request.method == "POST":
        for field in (
            "assignments",
            "requests",
            "updates",
            "reminders",
            "reports",
            "digest_reminders",
        ):
            setattr(preference, field, request.POST.get(field) == "on")
        preference.save()
        return redirect("ims:tasker:email_centre")
    rows = []
    query = (
        StaffEmailBinding.objects.filter(delivery__recipient=request.user)
        .select_related(
            "policy__company",
            "delivery__recipient",
            "delivery__notification__task",
            "delivery__notification__request",
            "delivery__notification__business_link",
            "delivery__report_snapshot__team__company",
        )
        .order_by("-created_at")
    )
    phase = request.GET.get("phase")
    if phase in ("held", "submitting", "admitted"):
        query = query.filter(phase=phase)
    state = request.GET.get("state")
    if state in ("HELD", "EXTERNAL_QUEUED", "PROVIDER_ACCEPTED", "FAILED", "UNKNOWN", "CANCELLED"):
        query = query.filter(delivery__state=state)
    for binding in query[:100]:
        decision, reason, instant = eligibility(binding)
        # Diagnostics retain identity/state after permission loss, never old work text.
        preview = {}
        if decision != "deny":
            try:
                from tasker.services.cloud_email import envelope

                preview = envelope(binding)["context"]
            except Exception:
                pass
        timeline = [
            {
                "at": fact.received_at,
                "state": fact.payload.get("status", ""),
                "version": fact.payload.get("state_version", ""),
                "quarantined": fact.quarantined,
            }
            for fact in binding.receipts.order_by("-received_at")[:10]
        ]
        rows.append(
            {
                "id": binding.pk,
                "category": binding.category,
                "state": binding.delivery.state,
                "reason": reason or binding.error_code,
                "decision": decision,
                "next_at": instant,
                "remote_state": binding.remote_state,
                "preview": preview,
                "timeline": timeline,
                "receipts": binding.receipts.count(),
            }
        )
    response = render(
        request,
        "tasker/email_centre.html",
        {
            "rows": rows,
            "preference": preference,
            "enabled": getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False),
        },
    )
    response["Cache-Control"] = "no-store"
    return response
