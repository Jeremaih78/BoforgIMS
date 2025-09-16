from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from inventory.models import Product
from .models import Invoice, InvoiceItem, StockReservation, PriceRule


@dataclass
class PricingResult:
    unit_price: Decimal
    discount_value: Decimal
    discount_percent: Decimal
    applied_rule_id: Optional[int] = None


class PricingService:
    @staticmethod
    def apply_best_rule(product: Product, qty: int, base_price: Decimal) -> PricingResult:
        now = timezone.now()
        qs = PriceRule.objects.filter(is_active=True).filter(
            Q(product__isnull=True) | Q(product=product) | Q(category=product.category)
        )
        # NOTE: extremely simple filter; broaden by category/time/min_qty.
        qs = qs.filter(min_qty__lte=qty)
        qs = qs.filter(Q(start_at__isnull=True) | Q(start_at__lte=now))
        qs = qs.filter(Q(end_at__isnull=True) | Q(end_at__gte=now))

        best: Optional[PriceRule] = None
        best_saving = Decimal('0')
        for r in qs:
            saving = PricingService._saving_for_rule(r, base_price)
            if saving > best_saving:
                best_saving = saving
                best = r
        if best is None:
            return PricingResult(base_price, Decimal('0'), Decimal('0'), None)
        if best.value_type == PriceRule.PERCENT:
            return PricingResult(base_price, Decimal('0'), Decimal(best.value), best.id)
        else:
            return PricingResult(base_price, Decimal(best.value), Decimal('0'), best.id)

    @staticmethod
    def _saving_for_rule(rule: PriceRule, base_price: Decimal) -> Decimal:
        if rule.value_type == PriceRule.PERCENT:
            return (Decimal(rule.value) / Decimal('100')) * base_price
        else:
            return Decimal(rule.value)


class StockService:
    @staticmethod
    @transaction.atomic
    def reserve_stock(invoice: Invoice, force: bool = False) -> None:
        # Create/update reservations based on invoice items
        for it in invoice.items.select_related('product'):
            p = it.product
            if not p or not p.track_inventory:
                continue
            qty = int(it.quantity)
            # Lock product row
            p = Product.objects.select_for_update().get(pk=p.pk)
            available = (p.quantity or 0) - (p.reserved or 0)
            if available < qty and not force:
                raise ValueError(f"Insufficient stock for {p.sku}: need {qty}, available {available}")
            # Upsert reservation
            res, _ = StockReservation.objects.get_or_create(invoice=invoice, product=p, defaults={'quantity': 0})
            delta = qty - res.quantity
            if delta != 0:
                res.quantity = qty
                res.save()
                p.reserved = (p.reserved or 0) + delta
                p.save(update_fields=['reserved'])

    @staticmethod
    @transaction.atomic
    def release_reservation(invoice: Invoice) -> None:
        for res in StockReservation.objects.select_for_update().filter(invoice=invoice).select_related('product'):
            p = Product.objects.select_for_update().get(pk=res.product_id)
            p.reserved = max(0, (p.reserved or 0) - int(res.quantity))
            p.save(update_fields=['reserved'])
        StockReservation.objects.filter(invoice=invoice).delete()

    @staticmethod
    @transaction.atomic
    def finalize_sale(invoice: Invoice) -> None:
        # apply reservations into actual stock deduction
        for res in StockReservation.objects.select_for_update().filter(invoice=invoice).select_related('product'):
            p = Product.objects.select_for_update().get(pk=res.product_id)
            qty = int(res.quantity)
            p.quantity = (p.quantity or 0) - qty
            p.reserved = max(0, (p.reserved or 0) - qty)
            p.save(update_fields=['quantity', 'reserved'])
        StockReservation.objects.filter(invoice=invoice).delete()

    @staticmethod
    def amount_paid(invoice: Invoice):
        return invoice.payments.aggregate(s=Sum('amount'))['s'] or 0
