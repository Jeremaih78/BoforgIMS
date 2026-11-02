import uuid

from django import forms
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.cache import add_never_cache_headers
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from core.permissions import allowed
from .client import APIError, verify_callback
from .models import CallbackReceipt, CommunicationRequest
from .services import approve_document, apply_remote, automatic_enabled, document_digest, model_for, sendable


class ApprovalForm(forms.Form):
    approval_id = forms.UUIDField(widget=forms.HiddenInput)
    document_digest = forms.CharField(max_length=64, widget=forms.HiddenInput)
    approved = forms.BooleanField(
        label="I approve this document and its customer email for delivery."
    )


@login_required
@require_POST
def finalize(request, kind, pk):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED or kind not in {"invoice", "quotation"}:
        raise Http404
    from .finalization import finalize_document

    try:
        row = finalize_document(user=request.user, kind=kind, document_id=pk)
    except model_for(kind).DoesNotExist:
        raise Http404 from None
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
    else:
        if row and row.status == "blocked":
            messages.warning(request, "Document finalized. Its email needs review in Email & history; check the customer email and PDF.")
        elif row:
            messages.success(request, "Document finalized. Its email is queued; see Email & history.")
        else:
            messages.success(request, "Document finalized. See Email & history for any existing delivery.")
    return redirect(f"ims:sales:{kind}_edit", pk)


@login_required
def document(request, kind, pk):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED or kind not in {"invoice", "quotation"}:
        raise Http404
    if not allowed(request.user, f"sales.change_{kind}", staff=True):
        raise PermissionDenied()
    doc = get_object_or_404(model_for(kind).objects.select_related("customer"), pk=pk)
    form = ApprovalForm(
        request.POST if request.method == "POST" else None,
        initial={"approval_id": uuid.uuid4(), "document_digest": document_digest(doc)},
    )
    if request.method == "POST" and form.is_valid():
        from sales.views import invoice_pdf, quotation_pdf

        try:
            row = approve_document(
                user=request.user,
                kind=kind,
                document_id=doc.pk,
                approval_id=form.cleaned_data["approval_id"],
                expected_digest=form.cleaned_data["document_digest"],
                render_pdf=lambda _: (
                    (
                        invoice_pdf(request, pk)
                        if kind == "invoice"
                        else quotation_pdf(request, pk)
                    ).content
                ),
            )
        except (ValidationError, ValueError) as error:
            form.add_error(
                None, error if isinstance(error, ValidationError) else "Invalid approval request."
            )
        else:
            return redirect("ims:sales:cloud_message", request_id=row.pk)
    history = CommunicationRequest.objects.filter(**{kind: doc})
    return render(
        request,
        "cloud_communications/document.html",
        {
            "document": doc,
            "kind": kind,
            "form": form,
            "history": history,
            "sendable": sendable(doc, kind),
            "automatic_email": automatic_enabled(kind),
        },
    )


@login_required
def detail(request, request_id):
    if not settings.CLOUD_COMMUNICATIONS_ENABLED:
        raise Http404
    row = get_object_or_404(CommunicationRequest, pk=request_id)
    kind = "invoice" if row.invoice_id else "quotation"
    if not allowed(request.user, f"sales.view_{kind}", staff=True):
        raise PermissionDenied()
    response = render(request, "cloud_communications/detail.html", {"entry": row})
    add_never_cache_headers(response)
    return response


@csrf_exempt
@require_POST
def callback(request):
    if (
        not settings.CLOUD_COMMUNICATIONS_ENABLED
        or not settings.CLOUD_COMMUNICATIONS_CALLBACK_SECRET
    ):
        return JsonResponse({"error": "unavailable"}, status=503)
    if not request.is_secure() and not settings.DEBUG:
        return JsonResponse({"error": "https_required"}, status=400)
    if len(request.body) > 8192:
        return JsonResponse({"error": "payload_too_large"}, status=413)
    try:
        value = verify_callback(
            settings.CLOUD_COMMUNICATIONS_CALLBACK_SECRET,
            request.headers.get("X-Boforg-Timestamp", ""),
            request.headers.get("X-Boforg-Signature", ""),
            request.body,
        )
        updated = parse_datetime(value["updated_at"])
        if updated is None or timezone.is_naive(updated):
            raise APIError("invalid_callback")
        with transaction.atomic():
            row = (
                CommunicationRequest.objects.select_for_update()
                .filter(payload__external_reference=value["external_reference"])
                .first()
            )
            if not row:
                return JsonResponse({"error": "unknown_reference"}, status=404)
            if CallbackReceipt.objects.filter(event_id=value["event_id"]).exists():
                return JsonResponse({"status": "duplicate"})
            apply_remote(row, {"id": value["message_id"], **value})
            row.save()
            CallbackReceipt.objects.create(event_id=value["event_id"], request=row)
    except (APIError, ValueError, KeyError, TypeError):
        return JsonResponse({"error": "invalid_callback"}, status=401)
    return JsonResponse({"status": "accepted"})
