from __future__ import annotations

from decimal import Decimal
from django.conf import settings
from django.db.models.signals import post_save
from django.dispatch import receiver

from sales.models import Payment
from .services import posting


@receiver(post_save, sender=Payment)
def on_payment_created(sender, instance: Payment, created: bool, **kwargs):
    if not created:
        return
    # Payment.save and the receipt workflow provide the transaction boundary.
    # A ledger failure must roll the receipt back, never silently drop postings.
    posting.post_sales_invoice(instance.invoice_id)
    posting.post_ar_receipt(instance.invoice_id, Decimal(instance.amount), payment_date=instance.date)
    if getattr(settings, 'ACCOUNTING_POST_COGS_ON', 'PAYMENT') == 'PAYMENT':
        posting.post_cogs_for_invoice(instance.invoice_id)
