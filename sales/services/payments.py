"""Atomic staff receipt workflow. The submission key identifies a single attempt."""
from decimal import Decimal

from django.db import transaction

from sales.models import Invoice, Payment
from sales.services import StockService


@transaction.atomic
def record_payment(*, invoice_id, amount, date, method, note, submission_key, user):
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    existing = Payment.objects.filter(submission_key=submission_key).first()
    if existing:
        if (existing.invoice_id, existing.amount, existing.date, existing.method, existing.note or '') != (
            invoice.pk, amount, date, method, note or ''
        ):
            raise ValueError('This submission was already used for a different payment.')
        return existing
    if not submission_key:
        raise ValueError('Refresh the payment form before submitting.')
    balance = invoice.total - StockService.amount_paid(invoice)
    if amount <= 0 or amount > balance or invoice.status == Invoice.PAID:
        raise ValueError('Enter a positive payment no greater than the outstanding balance.')
    # Validate inventory before recording the receipt or posting accounting.
    StockService.reserve_stock(invoice)
    if amount == balance:
        StockService.finalize_sale(invoice)
        invoice.status = Invoice.PAID
    else:
        invoice.status = Invoice.PENDING
    if not invoice.created_by_id:
        invoice.created_by = user
    invoice.save(update_fields=['status', 'created_by'])
    return Payment.objects.create(invoice=invoice, amount=amount, date=date,
        method=method, note=note, submission_key=submission_key)
