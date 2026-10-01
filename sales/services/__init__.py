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
            Q(scope=PriceRule.CART) | Q(scope=PriceRule.PRODUCT, product=product) | Q(scope=PriceRule.CATEGORY, category=product.category)
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
            return PricingResult(base_price, Decimal('0'), min(Decimal(best.value), Decimal('100')), best.id)
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
    def _demand(invoice):
        demand = {}
        for line in invoice.lines.select_related('product'):
            if not line.product or not line.product.track_inventory:
                continue
            if line.quantity <= 0 or line.quantity != int(line.quantity):
                raise ValueError('Stock quantities must be positive whole units.')
            demand[line.product_id] = demand.get(line.product_id, 0) + int(line.quantity)
        return demand

    @staticmethod
    @transaction.atomic
    def reserve_stock(invoice: Invoice, force: bool = False) -> None:
        invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if invoice.stock_finalized or invoice.status == Invoice.PAID:
            raise ValueError('Finalized invoices cannot reserve stock again.')
        demand = StockService._demand(invoice)
        reservations = list(StockReservation.objects.filter(invoice=invoice))
        existing = {}
        for res in reservations:
            if res.product_id in existing:
                raise ValueError('Duplicate historical reservations require reconciliation.')
            existing[res.product_id] = res
        products = Product.objects.select_for_update().filter(pk__in=set(demand) | set(existing)).order_by('pk')
        for product in products:
            from inventory.services.identity import check_consistency
            check_consistency(product)
            res = existing.get(product.pk)
            previous = res.quantity if res else 0
            wanted = demand.get(product.pk, 0)
            delta = wanted - previous
            if product.reserved < previous:
                raise ValueError('Historical reserved stock requires reconciliation.')
            if delta > product.quantity - product.reserved:
                raise ValueError(f'Insufficient stock for {product.sku}: need {wanted}, available {product.quantity - product.reserved + previous}')
            product.reserved += delta
            product.save(update_fields=['reserved'])
            if wanted:
                if res:
                    res.quantity = wanted
                    res.save(update_fields=['quantity'])
                else:
                    StockReservation.objects.create(invoice=invoice, product=product, quantity=wanted)
            elif res:
                res.delete()

    @staticmethod
    @transaction.atomic
    def release_reservation(invoice: Invoice) -> None:
        Invoice.objects.select_for_update().get(pk=invoice.pk)
        for res in StockReservation.objects.filter(invoice=invoice).order_by('product_id'):
            product = Product.objects.select_for_update().get(pk=res.product_id)
            if product.reserved < res.quantity:
                raise ValueError('Historical reserved stock requires reconciliation.')
            product.reserved -= res.quantity
            product.save(update_fields=['reserved'])
        StockReservation.objects.filter(invoice=invoice).delete()
        from inventory.services.identity import event
        for unit in ProductUnit.objects.select_for_update().filter(sale_line__invoice=invoice, status=ProductUnit.STATUS_RESERVED).order_by('pk'):
            unit.sale_line = None
            unit.status = ProductUnit.STATUS_AVAILABLE
            unit.save(update_fields=['sale_line', 'status', 'updated_at'])
            event(unit, kind='RELEASED', actor=invoice.created_by, invoice=invoice, previous='RESERVED')

    @staticmethod
    @transaction.atomic
    def finalize_sale(invoice: Invoice) -> None:
        from inventory.models import StockMovement
        locked = Invoice.objects.select_for_update().get(pk=invoice.pk)
        if locked.stock_finalized:
            return
        if locked.status == Invoice.PAID:
            raise ValueError('Legacy paid invoice requires reconciliation before stock changes.')
        StockService.reserve_stock(locked)
        for line in locked.lines.select_related('product').filter(product__tracking_mode=Product.TRACK_SERIAL):
            units = list(ProductUnit.objects.select_for_update().filter(sale_line=line).order_by('pk'))
            if len(units) != int(line.quantity) or any(unit.status != ProductUnit.STATUS_RESERVED for unit in units):
                raise ValueError(f'{line.product} requires {int(line.quantity)} reserved serial numbers before finalizing.')
            for unit in units:
                unit.mark_sold(line, actor=locked.created_by)
        for res in StockReservation.objects.filter(invoice=locked).order_by('product_id'):
            product = Product.objects.select_for_update().get(pk=res.product_id)
            product.reserved -= res.quantity
            product.save(update_fields=['reserved'])
            from inventory.services.identity import stock_move, check_consistency
            stock_move(product, StockMovement.OUT, res.quantity, locked.created_by, f'Invoice {locked.number}')
            check_consistency(product)
        StockReservation.objects.filter(invoice=locked).delete()
        locked.stock_finalized = True
        locked.save(update_fields=['stock_finalized'])
        invoice.stock_finalized = True

    @staticmethod
    def amount_paid(invoice: Invoice):
        return invoice.payments.aggregate(s=Sum('amount'))['s'] or 0
