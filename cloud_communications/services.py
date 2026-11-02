import base64
import hashlib
import json
import re
import uuid
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from core.permissions import allowed
from sales.models import Invoice, Quotation
from .client import APIError, CommunicationsClient
from .models import CommunicationRequest


def model_for(kind):
    if kind not in {"invoice", "quotation"}:
        raise ValidationError("Unsupported document kind.")
    return Invoice if kind == "invoice" else Quotation


def sendable(document, kind):
    if kind == "quotation":
        return document.status == Quotation.SENT
    return document.stock_finalized or document.status in {Invoice.CONFIRMED, Invoice.PAID}


def document_digest(document):
    fields = {
        name: getattr(document, name, None)
        for name in (
            "number",
            "date",
            "status",
            "stock_finalized",
            "due_date",
            "notes",
            "reference",
            "terms",
            "custom_fields",
        )
    }
    fields["customer"] = {
        "id": document.customer_id,
        "name": document.customer.name,
        "email": document.customer.email,
    }
    fields["lines"] = list(document.lines.order_by("id").values())
    return hashlib.sha256(
        json.dumps(fields, sort_keys=True, default=str, separators=(",", ":")).encode()
    ).hexdigest()


@transaction.atomic
def approve_document(*, user, kind, document_id, approval_id, expected_digest, render_pdf):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED:
        raise ValidationError("Business Cloud communications are not enabled.")
    if not re.fullmatch(r"[A-Z]{3}", settings.CLOUD_COMMUNICATIONS_CURRENCY):
        raise ValidationError(
            "The operator must confirm the IMS document currency before enabling communications."
        )
    if not allowed(user, f"sales.change_{kind}", staff=True):
        raise PermissionDenied()
    approval_id = uuid.UUID(str(approval_id))
    document = (
        model_for(kind).objects.select_for_update().select_related("customer").get(pk=document_id)
    )
    list(document.lines.select_for_update())
    digest = document_digest(document)
    existing = CommunicationRequest.objects.filter(pk=approval_id).first()
    if existing:
        if (
            getattr(existing, f"{kind}_id") != document.pk
            or existing.document_digest != expected_digest
        ):
            raise ValidationError("Approval key already belongs to another document snapshot.")
        return existing
    if not sendable(document, kind) or digest != expected_digest:
        raise ValidationError(
            "Approve/finalize this document and reload its preview before sending."
        )
    recipient = document.customer.email or ""
    validate_email(recipient)
    pdf = render_pdf(document)
    if (
        not isinstance(pdf, bytes)
        or not pdf.startswith(b"%PDF-")
        or b"%%EOF" not in pdf[-1024:]
        or len(pdf) > 2 * 1024 * 1024
    ):
        raise ValidationError("A valid approved PDF up to 2 MiB is required.")
    payload = {
        "template": kind,
        "event_type": f"{kind}.approved",
        "recipients": [recipient],
        "external_reference": f"ims:{kind}:{document.pk}:approval:{approval_id}",
        "context": {
            "customer_name": document.customer.name,
            "document_number": document.number,
            "currency": settings.CLOUD_COMMUNICATIONS_CURRENCY,
            "document_total": f"{document.total:.2f}",
            "due_date": str(getattr(document, "due_date", "") or ""),
        },
        "attachments": [
            {"filename": f"{document.number}.pdf", "content_base64": base64.b64encode(pdf).decode()}
        ],
    }
    return CommunicationRequest.objects.create(
        id=approval_id,
        **{kind: document},
        document_digest=digest,
        payload=payload,
        approved_by=user,
    )


def configured_client():
    try:
        token = Path(settings.CLOUD_COMMUNICATIONS_TOKEN_FILE).read_text(encoding="utf-8").strip()
        return CommunicationsClient(settings.CLOUD_COMMUNICATIONS_URL, token)
    except (OSError, ValueError):
        raise APIError("client_configuration", retryable=False) from None


def apply_remote(row, result):
    remote_id = uuid.UUID(str(result["id"]))
    updated = parse_datetime(result["updated_at"])
    if (
        updated is None
        or timezone.is_naive(updated)
        or (row.remote_message_id and row.remote_message_id != remote_id)
        or result["external_reference"] != row.payload["external_reference"]
    ):
        raise APIError("invalid_service_response", retryable=True)
    if not row.remote_updated_at or updated >= row.remote_updated_at:
        row.remote_message_id, row.remote_status, row.remote_updated_at = (
            remote_id,
            result["status"],
            updated,
        )
        row.status, row.error_code = "submitted", result.get("error_code", "")


def process_requests(*, limit=50, client=None):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED:
        return 0
    client = client or configured_client()
    processed = 0
    for _ in range(limit):
        with transaction.atomic():
            now = timezone.now()
            row = (
                CommunicationRequest.objects.select_for_update(skip_locked=True)
                .filter(status__in=["queued", "submitting"], available_at__lte=now)
                .filter(Q(leased_until__isnull=True) | Q(leased_until__lte=now))
                .order_by("available_at")
                .first()
            )
            if not row:
                break
            kind, document = (
                ("invoice", row.invoice) if row.invoice_id else ("quotation", row.quotation)
            )
            document = model_for(kind).objects.select_for_update().get(pk=document.pk)
            if not sendable(document, kind) or document_digest(document) != row.document_digest:
                row.status, row.error_code = "cancelled", "approved_document_changed"
                row.save(update_fields=["status", "error_code"])
                continue
            token = uuid.uuid4()
            row.status, row.lease_token, row.leased_until = (
                "submitting",
                token,
                now + timedelta(minutes=2),
            )
            row.attempts += 1
            row.save()
        error, result = None, None
        try:
            result = client.submit_event(row.payload, idempotency_key=str(row.pk))
        except APIError as exc:
            error = exc
        except Exception:
            error = APIError("service_unavailable", retryable=True)
        with transaction.atomic():
            row = CommunicationRequest.objects.select_for_update().get(pk=row.pk)
            if row.lease_token != token:
                continue
            if result is not None:
                try:
                    apply_remote(row, result)
                except (KeyError, ValueError, TypeError, APIError):
                    error = APIError("invalid_service_response", retryable=True)
            if error:
                row.status = "queued" if error.retryable and row.attempts < 10 else "failed"
                row.error_code = error.code
                row.available_at = timezone.now() + timedelta(
                    seconds=min(3600, 30 * 2 ** min(row.attempts, 7))
                )
            row.lease_token, row.leased_until = None, None
            row.save()
        processed += 1
    return processed


def reconcile(*, client=None, limit=100):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED:
        return 0
    client = client or configured_client()
    count = 0
    for row in (
        CommunicationRequest.objects.filter(remote_message_id__isnull=False)
        .exclude(remote_status__in=["smtp_accepted", "cancelled", "rejected", "failed"])
        .order_by("remote_updated_at")[:limit]
    ):
        try:
            result = client.status(row.remote_message_id)
            with transaction.atomic():
                locked = CommunicationRequest.objects.select_for_update().get(pk=row.pk)
                apply_remote(locked, result)
                locked.save()
            count += 1
        except (APIError, KeyError, ValueError, TypeError):
            continue
    return count
