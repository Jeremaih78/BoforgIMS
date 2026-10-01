"""Shared identity resolution and locked physical-unit lifecycle operations."""
import re
from datetime import timedelta
from uuid import UUID

from django.core.exceptions import ValidationError
from django.db import connection, transaction
from django.db.models import Q
from django.db.models.functions import Upper, Trim
from django.utils import timezone

from inventory.models import Product, ProductUnit, ProductBarcode, UnitEvent, UnitSale, ScanRequest, StockMovement

ON_HAND = (ProductUnit.STATUS_AVAILABLE, ProductUnit.STATUS_RESERVED)


class UnknownIdentifier(ValidationError):
    pass


def normalize(value):
    if not isinstance(value, str):
        raise ValidationError('Scan or type an identifier.')
    value = value.strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._ /:+\-]{0,119}', value) or '://' in value:
        raise ValidationError('Use an identifier of up to 120 letters, numbers or barcode punctuation; URLs are not accepted.')
    return value.upper()


def identity_lock():
    # One brief namespace lock for new aliases/serials, never held for HTTP calls.
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [740193201])


def resolve(value):
    code = normalize(value)
    unit_matches = list(ProductUnit.objects.annotate(canonical_serial=Upper(Trim('serial_number'))).filter(Q(unit_id=code) | Q(canonical_serial=code)).select_related('product')[:3])
    products = set(ProductBarcode.objects.filter(code=code).values_list('product_id', flat=True))
    products.update(Product.objects.filter(sku__iexact=code).values_list('pk', flat=True)[:3])
    if len(unit_matches) > 1 or (unit_matches and products) or len(products) > 1:
        raise ValidationError('Identifier is ambiguous. Ask a manager to resolve the conflicting labels.')
    if unit_matches:
        return unit_matches[0].product, unit_matches[0]
    if products:
        return Product.objects.get(pk=products.pop()), None
    raise UnknownIdentifier('Barcode not recognized. Assign it to an existing product first.')


def event(unit=None, *, product=None, kind, actor, note='', movement=None, invoice=None, previous=''):
    return UnitEvent.objects.create(unit=unit, product=product or unit.product, event=kind, actor=actor,
        note=note, previous_status=previous, status=unit.status if unit else '', movement=movement, invoice=invoice)


@transaction.atomic
def assign_barcode(*, product_id, code='', actor, internal=False):
    identity_lock()
    product = Product.objects.select_for_update().get(pk=product_id)
    code = f'BF-P-{product.pk:09d}' if internal else normalize(code)
    existing = ProductBarcode.objects.filter(code=code).first()
    if existing:
        if existing.product_id == product.pk:
            return existing
        raise ValidationError('Barcode already belongs to another product.')
    if not internal and code.startswith(('BF-P-', 'BF-U-')):
        raise ValidationError('BF-P and BF-U identifiers are reserved for Boforg-generated labels.')
    if ProductUnit.objects.annotate(canonical_serial=Upper(Trim('serial_number'))).filter(Q(unit_id=code) | Q(canonical_serial=code)).exists() or Product.objects.filter(sku__iexact=code).exclude(pk=product.pk).exists():
        raise ValidationError('This identifier is already in use.')
    barcode = ProductBarcode.objects.create(product=product, code=code, kind='INTERNAL' if internal else 'MANUFACTURER', created_by=actor)
    event(product=product, kind='BARCODE_ASSIGNED', actor=actor, note=code)
    return barcode


def check_consistency(product):
    if product.identity_enforced and product.is_serial_tracked:
        count = product.units.filter(status__in=ON_HAND).count()
        if count != product.quantity:
            raise ValidationError(f'{product.sku}: recorded stock {product.quantity} differs from {count} saleable units. Reconcile before continuing.')


def stock_move(product, direction, quantity, actor, note, cost=None):
    movement = StockMovement(product=product, movement_type=direction, quantity=quantity,
        unit_cost=cost, user=actor, note=note[:255])
    movement.save(unit_operation=True)
    product.refresh_from_db()
    return movement


def validate_serial(serial):
    if not serial:
        return None
    serial = normalize(serial)
    if serial.startswith(('BF-U-', 'BF-P-')):
        raise ValidationError('A Boforg ID is not a manufacturer serial number.')
    if ProductUnit.objects.annotate(canonical_serial=Upper(Trim('serial_number'))).filter(canonical_serial=serial).exists() or ProductBarcode.objects.filter(code=serial).exists() or Product.objects.filter(sku__iexact=serial).exists():
        raise ValidationError(f'Identifier {serial} is already assigned.')
    return serial


@transaction.atomic
def adopt_existing(*, product_id, actor, note, location=''):
    identity_lock()
    product = Product.objects.select_for_update().get(pk=product_id)
    if not note.strip():
        raise ValidationError('Record a reason and confirm the physical count before identifying existing stock.')
    if product.reserved or product.quantity < 0:
        raise ValidationError('Resolve reservations or negative stock before adopting unit tracking.')
    if not product.track_inventory:
        raise ValidationError('Enable inventory tracking before identifying physical stock.')
    existing = product.units.filter(status__in=ON_HAND).count()
    missing = product.quantity - existing
    if missing < 0 or missing > 1000:
        raise ValidationError('Unit count exceeds stock, or adoption exceeds 1,000 units. Reconcile with a manager.')
    for _ in range(missing):
        unit = ProductUnit.objects.create(product=product, location=location, landed_cost=product.avg_cost, created_by=actor)
        event(unit, kind='EXISTING_STOCK_IDENTIFIED', actor=actor, note=note)
    product.tracking_mode = Product.TRACK_SERIAL
    product.identity_enforced = True
    product.save(update_fields=['tracking_mode', 'identity_enforced'])
    event(product=product, kind='TRACKING_ENABLED', actor=actor, note=note)
    check_consistency(product)
    return product


def request_result(key, scope, code, actor):
    try:
        key = UUID(str(key))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError('A valid request ID is required. Refresh and retry.')
    previous = ScanRequest.objects.filter(key=key).first()
    if previous and (previous.scope, previous.code, previous.actor_id) != (scope, code, actor.pk):
        raise ValidationError('This request ID was used for a different action.')
    return key, previous.result if previous else None


@transaction.atomic
def record_sale(unit, *, sale_line=None, order_item=None, actor=None, timestamp=None):
    if bool(sale_line) == bool(order_item):
        raise ValidationError('A sale must identify exactly one invoice line or shop order item.')
    Product.objects.select_for_update().get(pk=unit.product_id)
    ProductUnit.objects.select_for_update().get(pk=unit.pk)
    unit.refresh_from_db()
    if unit.status != ProductUnit.STATUS_RESERVED:
        raise ValidationError('Only a reserved unit can be sold.')
    if sale_line and unit.sale_line_id != sale_line.pk or order_item and unit.order_item_id != order_item.pk:
        raise ValidationError('Unit is reserved by another transaction.')
    now = timestamp or timezone.now()
    UnitSale.objects.create(unit=unit, invoice=sale_line.invoice if sale_line else None, line=sale_line,
        order_item=order_item, customer_name=sale_line.invoice.customer.name if sale_line else order_item.order.full_name,
        unit_price=sale_line.unit_price if sale_line else order_item.unit_price, unit_cost=unit.landed_cost,
        sold_at=now, warranty_expires=now.date() + timedelta(days=unit.product.warranty_days) if unit.product.warranty_days else None)
    unit.status = ProductUnit.STATUS_SOLD
    unit.sold_at = now
    unit.save(update_fields=['status', 'sold_at', 'updated_at'])
    event(unit, kind='SOLD', actor=actor, invoice=sale_line.invoice if sale_line else None, previous='RESERVED')


@transaction.atomic
def transition_unit(*, unit_id, action, actor, note, location=None):
    ref = ProductUnit.objects.get(pk=unit_id)
    product = Product.objects.select_for_update().get(pk=ref.product_id)
    unit = ProductUnit.objects.select_for_update().get(pk=unit_id)
    check_consistency(product)
    if not note.strip():
        raise ValidationError('A reason is required for unit actions.')
    if action == 'transfer':
        if not location or unit.status not in (ProductUnit.STATUS_AVAILABLE, 'FAULTY', 'RETURNED', 'REPAIR'):
            raise ValidationError('Choose a location; only unreserved units on site can be transferred.')
        previous = unit.location
        unit.location = location.strip()[:100]
        unit.save(update_fields=['location', 'updated_at'])
        event(unit, kind='TRANSFERRED', actor=actor, note=f'{previous or "Unassigned"} → {unit.location}. {note}')
        return unit
    transitions = {
        'return': ({'SOLD'}, 'RETURNED'),
        'fault': ({'AVAILABLE', 'RETURNED'}, 'FAULTY'),
        'repair': ({'RETURNED', 'FAULTY'}, 'REPAIR'),
        'restock': ({'RETURNED', 'FAULTY', 'REPAIR'}, 'AVAILABLE'),
        'customer_return': ({'RETURNED', 'FAULTY', 'REPAIR'}, 'SOLD'),
        'supplier_return': ({'AVAILABLE', 'RETURNED', 'FAULTY', 'REPAIR'}, 'SUPPLIER_RET'),
        'write_off': ({'AVAILABLE', 'RETURNED', 'FAULTY', 'REPAIR'}, 'WRITTEN_OFF'),
    }
    if action not in transitions or unit.status not in transitions[action][0]:
        raise ValidationError('This action is not allowed in the current unit status.')
    if unit.service_cases.exclude(status='CLOSED').exists() and action == 'restock':
        raise ValidationError('Close the service case before restocking this unit.')
    if action == 'customer_return' and not unit.sales_history.exists() and not unit.sale_line_id:
        raise ValidationError('No original sale is recorded for this unit.')
    if action == 'customer_return' and unit.service_cases.filter(sale=unit.sales_history.first(), replacement__isnull=False).exists():
        raise ValidationError('This machine was already replaced. Review the replacement case before any customer handover.')
    previous = unit.status
    next_status = transitions[action][1]
    movement = None
    if previous in ON_HAND and next_status not in ON_HAND:
        movement = stock_move(product, 'OUT', 1, actor, f'{unit.unit_id}: {note}')
    elif previous not in ON_HAND and next_status in ON_HAND:
        movement = stock_move(product, 'IN', 1, actor, f'{unit.unit_id}: {note}', unit.landed_cost)
    unit.status = next_status
    if action == 'restock':
        unit.sale_line = None
        unit.order_item = None
    unit.save(update_fields=['status', 'sale_line', 'order_item', 'updated_at'])
    event(unit, kind=action.upper(), actor=actor, note=note, movement=movement, previous=previous)
    check_consistency(product)
    return unit


@transaction.atomic
def correct_serial(*, unit_id, serial, actor, note):
    identity_lock()
    unit = ProductUnit.objects.select_for_update().get(pk=unit_id)
    new = validate_serial(serial)
    if not note.strip():
        raise ValidationError('Explain the serial correction.')
    old = unit.serial_number
    ProductUnit.objects.filter(pk=unit.pk).update(serial_number=new)
    event(unit, kind='SERIAL_CORRECTED', actor=actor, note=f'{old or "None"} → {new or "None"}. {note}')
    return unit
