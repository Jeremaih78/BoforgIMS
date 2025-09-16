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
    # Default behavior: post revenue on payment event if not posted, then post AR receipt, then COGS per config
    try:
        posting.post_sales_invoice(instance.invoice_id)
    except Exception:
        # if anything fails here, we still try to post receipt to avoid blocking
        pass
    # Receipt
    try:
        posting.post_ar_receipt(instance.invoice_id, Decimal(instance.amount))
    except Exception:
        pass
    # COGS timing: default PAYMENT
    if getattr(settings, "ACCOUNTING_POST_COGS_ON", "PAYMENT") == "PAYMENT":
        try:
            posting.post_cogs_for_invoice(instance.invoice_id)
        except Exception:
            pass

