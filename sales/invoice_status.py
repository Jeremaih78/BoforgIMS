from decimal import Decimal

from django.db.models import Sum


STATUS_PAID = "paid"
STATUS_UNPAID = "unpaid"
STATUS_PARTIALLY_PAID = "partially-paid"
STATUS_OVERDUE = "overdue"
STATUS_VOID = "void"
STATUS_DRAFT = "draft"


INVOICE_STATUS_LABELS = {
    "PAID": ("PAID", STATUS_PAID),
    "PARTIALLY_PAID": ("PARTIALLY PAID", STATUS_PARTIALLY_PAID),
    "SENT": ("UNPAID", STATUS_UNPAID),
    "UNPAID": ("UNPAID", STATUS_UNPAID),
    "PENDING": ("UNPAID", STATUS_UNPAID),
    "CONFIRMED": ("UNPAID", STATUS_UNPAID),
    "OVERDUE": ("OVERDUE", STATUS_OVERDUE),
    "VOID": ("VOID", STATUS_VOID),
    "CANCELLED": ("VOID", STATUS_VOID),
    "DRAFT": ("DRAFT", STATUS_DRAFT),
}


def _is_partially_paid(invoice, status):
    if status not in {"PENDING", "CONFIRMED", "SENT", "UNPAID"}:
        return False

    payments = getattr(invoice, "payments", None)
    if payments is None or not hasattr(payments, "aggregate"):
        return False

    total = Decimal(str(getattr(invoice, "total", 0) or 0))
    paid = payments.aggregate(total=Sum("amount")).get("total") or Decimal("0")
    return Decimal(str(paid)) > Decimal("0") and Decimal(str(paid)) < total


def get_invoice_status_context(invoice):
    status = (getattr(invoice, "status", "") or "").upper()
    if _is_partially_paid(invoice, status):
        label, css_class = INVOICE_STATUS_LABELS["PARTIALLY_PAID"]
        return {
            "invoice_status": status,
            "invoice_status_label": label,
            "invoice_status_class": css_class,
        }

    label, css_class = INVOICE_STATUS_LABELS.get(status, (status.replace("_", " "), STATUS_UNPAID))
    return {
        "invoice_status": status,
        "invoice_status_label": label,
        "invoice_status_class": css_class,
    }
