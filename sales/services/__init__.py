from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone

from inventory.models import Product, ProductUnit
from sales.models import Invoice, StockReservation, PriceRule


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
        for line in invoice.items.select_related('product'):
            product = line.product
            if not product or not product.track_inventory:
                continue
            qty = int(line.quantity)
            product = Product.objects.select_for_update().get(pk=product.pk)
            available = (product.quantity or 0) - (product.reserved or 0)
            if available < qty and not force:
                raise ValueError(f"Insufficient stock for {product.sku}: need {qty}, available {available}")
            res, _ = StockReservation.objects.get_or_create(invoice=invoice, product=product, defaults={'quantity': 0})
            delta = qty - res.quantity
            if delta != 0:
                res.quantity = qty
                res.save()
                product.reserved = (product.reserved or 0) + delta
                product.save(update_fields=['reserved'])

    @staticmethod
    @transaction.atomic
    def release_reservation(invoice: Invoice) -> None:
        for res in StockReservation.objects.select_for_update().filter(invoice=invoice).select_related('product'):
            product = Product.objects.select_for_update().get(pk=res.product_id)
            product.reserved = max(0, (product.reserved or 0) - int(res.quantity))
            product.save(update_fields=['reserved'])
        StockReservation.objects.filter(invoice=invoice).delete()
        ProductUnit.objects.filter(sale_line__invoice=invoice, status=ProductUnit.STATUS_RESERVED).update(
            sale_line=None,
            status=ProductUnit.STATUS_AVAILABLE,
            sold_at=None,
        )

    @staticmethod
    @transaction.atomic
    def finalize_sale(invoice: Invoice) -> None:
        serial_lines = invoice.items.select_related('product').filter(product__tracking_mode=Product.TRACK_SERIAL)
        for line in serial_lines:
            required = int(line.quantity)
            units = list(ProductUnit.objects.select_for_update().filter(sale_line=line))
            if len(units) != required:
                raise ValueError(f'{line.product} requires {required} serial numbers before finalizing.')
            for unit in units:
                unit.mark_sold(line)
        for res in StockReservation.objects.select_for_update().filter(invoice=invoice).select_related('product'):
            product = Product.objects.select_for_update().get(pk=res.product_id)
            qty = int(res.quantity)
            product.quantity = (product.quantity or 0) - qty
            product.reserved = max(0, (product.reserved or 0) - qty)
            product.save(update_fields=['quantity', 'reserved'])
        StockReservation.objects.filter(invoice=invoice).delete()

    @staticmethod
    def amount_paid(invoice: Invoice):
        return invoice.payments.aggregate(s=Sum('amount'))['s'] or 0
