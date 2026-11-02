"""Explicit staff finalization; ordinary saves never enqueue communications."""
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from core.permissions import allowed
from sales.models import Quotation
from .services import model_for, queue_finalized_document, sendable


@transaction.atomic
def finalize_document(*, user, kind, document_id):
    if not allowed(user, f"sales.change_{kind}", staff=True):
        raise PermissionDenied()
    document = model_for(kind).objects.select_for_update().get(pk=document_id)
    if kind == "quotation" and (document.status == Quotation.CONVERTED or document.invoice_set.exists()):
        raise ValidationError("Converted quotations cannot be finalized again.")
    if sendable(document, kind):
        return None
    if not document.lines.exists():
        raise ValidationError("Add at least one line before finalizing this document.")
    if kind == "invoice":
        document.confirm(user=user)
    else:
        document.status = Quotation.SENT
        document.save(update_fields=["status"])
    return queue_finalized_document(user=user, kind=kind, document_id=document.pk)
