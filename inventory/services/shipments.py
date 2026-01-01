from __future__ import annotations

from decimal import Decimal
from typing import Iterable, Mapping

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounting.models import Account, Currency, JournalEntry, JournalLine
from inventory.models import (
    Shipment,
    ShipmentItem,
    ProductUnit,
    ShipmentEventLog,
    StockMovement,
)


class ShipmentServiceError(ValidationError):
    """Domain-specific error for shipment workflows."""


def _base_currency() -> Currency:
    code = getattr(settings, 'BASE_CURRENCY_CODE', 'USD')
    return Currency.objects.get_or_create(code=code, defaults={'name': code, 'is_base': True})[0]


def _get_account(code: str) -> Account:
    return Account.objects.get(code=code)


def allocate_landed_costs(shipment: Shipment, *, basis: str | None = None) -> Decimal:
    """Distribute pooled costs across shipment items and persist landed unit costs."""
    basis = basis or shipment.allocation_basis
    items = list(shipment.items.select_related('product'))
    if not items:
        raise ShipmentServiceError("Shipment has no items to allocate costs against.")
    cost_pool = shipment.total_cost_base
    if cost_pool <= 0:
        shipment.costs.filter(allocated=False).update(allocated=True)
        shipment.landed_cost_allocated_at = timezone.now()
        shipment.save(update_fields=['landed_cost_allocated_at', 'updated_at'])
        ShipmentEventLog.objects.create(
            shipment=shipment,
            event_type=ShipmentEventLog.EVENT_COST,
            note='No landed costs to allocate; marked as complete.',
        )
        return Decimal('0.00')

    def _basis_value(item: ShipmentItem) -> Decimal:
        qty = Decimal(str(item.quantity_received or item.quantity_expected or 0))
        if basis == Shipment.COST_BASIS_QUANTITY:
            return qty
        unit_price = Decimal(str(item.unit_purchase_price))
        return qty * unit_price

    denominator = sum(_basis_value(item) for item in items)
    if denominator <= 0:
        raise ShipmentServiceError("Unable to allocate costs because basis denominator is zero.")

    for item in items:
        weight = _basis_value(item)
        share = (cost_pool * weight / denominator) if weight else Decimal('0.00')
        share = share.quantize(Decimal('0.0001'))
        item.apply_landed_cost(share)

    shipment.costs.filter(allocated=False).update(allocated=True)
    shipment.landed_cost_allocated_at = timezone.now()
    shipment.save(update_fields=['landed_cost_allocated_at', 'updated_at'])
    ShipmentEventLog.objects.create(
        shipment=shipment,
        event_type=ShipmentEventLog.EVENT_COST,
        note=f'Landed cost allocation completed using basis {basis}.',
    )
    return cost_pool


@transaction.atomic
def receive_shipment(*, shipment_id: int, receipts: Iterable[Mapping], received_by, basis: str | None = None, note: str = '') -> Shipment:
    """Finalize a shipment receipt, enforcing serial capture, landed costs, stock posting, and accounting."""
    shipment = Shipment.objects.select_for_update().get(pk=shipment_id)
    shipment.require_status({Shipment.STATUS_ARRIVED, Shipment.STATUS_CLEARED})
    item_qs = ShipmentItem.objects.select_for_update().filter(shipment=shipment).select_related('product')
    items_by_id = {item.id: item for item in item_qs}
    if not receipts:
        raise ShipmentServiceError('No receipt details supplied.')

    recorded = []
    now = timezone.now()
    for payload in receipts:
        item_id = payload.get('item_id')
        if item_id not in items_by_id:
            raise ShipmentServiceError(f"Shipment item {item_id} does not belong to shipment {shipment.shipment_code}.")
        item = items_by_id[item_id]
        quantity = int(payload.get('quantity') or 0)
        if quantity <= 0:
            raise ShipmentServiceError('Receipt quantity must be positive.')
        serials = payload.get('serials') or []
        if item.requires_serials and len(serials) != quantity:
            raise ShipmentServiceError(f'{item.product} requires serial numbers for every unit received.')
        if item.quantity_received + quantity > item.quantity_expected:
            raise ShipmentServiceError('Quantity received cannot exceed quantity expected.')
        item.quantity_received += quantity
        item.last_received_at = now
        item.full_clean()
        item.save(update_fields=['quantity_received', 'last_received_at'])
        recorded.append({'item': item, 'quantity': quantity, 'serials': serials})

    shipment.refresh_from_db()
    if not shipment.is_fully_received:
        raise ShipmentServiceError('All shipment items must be fully received before marking as received.')

    allocate_landed_costs(shipment, basis=basis)

    for row in recorded:
        item = ShipmentItem.objects.get(pk=row['item'].pk)
        unit_cost = item.landed_unit_cost or item.unit_purchase_price
        StockMovement.objects.create(
            product=item.product,
            movement_type=StockMovement.IN,
            quantity=row['quantity'],
            unit_cost=unit_cost,
            note=f'Shipment {shipment.shipment_code}',
            user=received_by,
        )
        if item.requires_serials:
            cleaned = []
            for serial in row['serials']:
                value = (serial or '').strip()
                if not value:
                    raise ShipmentServiceError('Serial numbers cannot be blank.')
                cleaned.append(value)
            for serial in cleaned:
                ProductUnit.objects.create(
                    serial_number=serial,
                    product=item.product,
                    shipment=shipment,
                    shipment_item=item,
                    purchase_price=item.unit_purchase_price,
                    landed_cost=unit_cost,
                    status=ProductUnit.STATUS_AVAILABLE,
                    created_by=received_by,
                )

    ShipmentEventLog.objects.create(
        shipment=shipment,
        event_type=ShipmentEventLog.EVENT_RECEIPT,
        note=note or 'Shipment received and stocked.',
        actor=received_by,
    )
    shipment.transition_status(Shipment.STATUS_RECEIVED, actor=received_by, note=note)
    _post_inventory_receipt_journal(shipment, actor=received_by)
    return shipment


def _post_inventory_receipt_journal(shipment: Shipment, actor=None) -> JournalEntry:
    existing = JournalEntry.objects.filter(source='SHIPMENT', source_id=shipment.id, is_posted=True).first()
    if existing:
        return existing
    currency = _base_currency()
    inventory_code = getattr(settings, 'SHIPMENT_INVENTORY_ACCOUNT', '1300')
    clearing_code = getattr(settings, 'SHIPMENT_CLEARING_ACCOUNT', '2000')
    inventory_account = _get_account(inventory_code)
    clearing_account = _get_account(clearing_code)
    total_value = shipment.items.aggregate(total=Sum('landed_total_cost'))['total'] or Decimal('0.00')
    entry = JournalEntry.objects.create(
        date=timezone.now().date(),
        memo=f'Shipment {shipment.shipment_code} receipt',
        currency=currency,
        fx_rate=Decimal('1.0'),
        is_posted=True,
        posted_at=timezone.now(),
        source='SHIPMENT',
        source_id=shipment.id,
        created_by=actor,
    )
    JournalLine.objects.create(
        entry=entry,
        account=inventory_account,
        debit=total_value,
        credit=Decimal('0.00'),
        debit_base=total_value,
    )
    JournalLine.objects.create(
        entry=entry,
        account=clearing_account,
        debit=Decimal('0.00'),
        credit=total_value,
        credit_base=total_value,
    )
    entry.clean()
    entry.save()
    return entry


def shipment_cost_summary(shipment_id: int) -> dict:
    shipment = Shipment.objects.get(pk=shipment_id)
    breakdown = list(
        shipment.costs.values('cost_type').annotate(total=Sum('amount_base')).order_by('cost_type')
    )
    return {
        'shipment': shipment,
        'total_cost_base': shipment.total_cost_base,
        'breakdown': breakdown,
    }


def landed_cost_per_product(product_id: int):
    return (
        ShipmentItem.objects.filter(product_id=product_id)
        .values('product__name', 'shipment__shipment_code')
        .annotate(quantity=Sum('quantity_received'), landed_total=Sum('landed_total_cost'))
        .order_by('-shipment__created_at')
    )


def profit_per_serial(serial_number: str) -> dict:
    unit = ProductUnit.objects.select_related('product', 'sale_line__invoice', 'sale_line__quotation').get(serial_number=serial_number)
    sale_doc = unit.sale_line.invoice if unit.sale_line else None
    return {
        'serial_number': serial_number,
        'product': unit.product,
        'landed_cost': unit.landed_cost,
        'sale_amount': unit.sale_line.line_total if unit.sale_line else None,
        'profit': unit.profit_amount,
        'invoice': sale_doc.number if sale_doc else None,
    }


def supplier_defect_rate(supplier_id: int) -> dict:
    units = ProductUnit.objects.filter(shipment__supplier_id=supplier_id)
    total = units.count()
    faulty = units.filter(status=ProductUnit.STATUS_FAULTY).count()
    rate = (Decimal(faulty) / Decimal(total)).quantize(Decimal('0.0001')) if total else Decimal('0.0000')
    return {'supplier_id': supplier_id, 'total_units': total, 'faulty_units': faulty, 'fault_rate': rate}


def shipment_delay_report():
    rows = []
    for shipment in Shipment.objects.exclude(eta_date__isnull=True).exclude(arrival_date__isnull=True):
        delta = shipment.arrival_date - shipment.eta_date
        rows.append({
            'shipment': shipment,
            'eta_date': shipment.eta_date,
            'arrival_date': shipment.arrival_date,
            'delay_days': delta.days,
        })
    return rows
