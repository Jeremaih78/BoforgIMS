"""Atomic staff receipt workflow. The submission key identifies a single attempt."""
from decimal import Decimal

from django.db import transaction

from sales.models import Invoice, Payment
from sales.services import StockService


@transaction.atomic
def record_payment(*, invoice_id, amount, date, method, note, submission_key, user, bank=None, fx_rate=None):
    invoice = Invoice.objects.select_for_update().get(pk=invoice_id)
    was_finalized = invoice.stock_finalized or invoice.status in {Invoice.CONFIRMED, Invoice.PAID}
    existing = Payment.objects.filter(submission_key=submission_key).first()
    if existing:
        if (existing.invoice_id, existing.amount, existing.date, existing.method, existing.note or '') != (
            invoice.pk, amount, date, method, note or ''
        ):
            raise ValueError('This submission was already used for a different payment.')
        if (bank is not None and existing.bank_id!=bank.pk) or (fx_rate is not None and existing.fx_rate!=fx_rate):
            raise ValueError('This submission was already used with different money-account details.')
        return existing
    if not submission_key:
        raise ValueError('Refresh the payment form before submitting.')
    balance = invoice.total - StockService.amount_paid(invoice)
    if amount <= 0 or amount > balance or invoice.status == Invoice.PAID:
        raise ValueError('Enter a positive payment no greater than the outstanding balance.')
    from accounting.services.money import check_posting_date
    check_posting_date(date)
    if date < invoice.date:
        raise ValueError('The payment date cannot be before the invoice date.')
    # Validate inventory before recording the receipt or posting accounting.
    StockService.reserve_stock(invoice)
    from accounting.services.receipts import invoice_receipt_setup
    currency, bank, rate=invoice_receipt_setup(invoice,bank,fx_rate)
    if amount == balance:
        StockService.finalize_sale(invoice)
        invoice.status = Invoice.PAID
    else:
        invoice.status = Invoice.PENDING
    if not invoice.created_by_id:
        invoice.created_by = user
    invoice.save(update_fields=['status', 'created_by'])
    payment = Payment.objects.create(invoice=invoice, amount=amount, date=date,
        method=method, note=note, submission_key=submission_key,bank=bank,currency=currency,fx_rate=rate,created_by=user)
    if not was_finalized and invoice.stock_finalized:
        from cloud_communications.services import queue_finalized_document
        queue_finalized_document(user=user, kind="invoice", document_id=invoice.pk)
    return payment
