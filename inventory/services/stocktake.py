from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from inventory.models import Product, ProductUnit, Stocktake, StocktakeLine, StocktakeScan, ScanRequest
from .identity import ON_HAND, normalize, resolve, request_result, stock_move, transition_unit, event


def baseline(product):
    return {
        'baseline_quantity': product.quantity, 'baseline_reserved': product.reserved,
        'baseline_event_id': product.identity_events.exclude(event__in=['LABEL_EXPORTED', 'BARCODE_ASSIGNED', 'SERIAL_CORRECTED']).aggregate(n=Max('pk'))['n'] or 0,
        'baseline_movement_id': product.movements.aggregate(n=Max('pk'))['n'] or 0,
    }


@transaction.atomic
def start_stocktake(*, name, location='', actor):
    if not name.strip():
        raise ValidationError('Name the stocktake.')
    session = Stocktake.objects.create(name=name.strip()[:150], location=location.strip()[:100], created_by=actor)
    for product in Product.objects.select_for_update().filter(track_inventory=True, is_active=True).order_by('pk'):
        values = baseline(product)
        if product.is_serial_tracked:
            if not product.identity_enforced:
                raise ValidationError(f'{product.sku}: identify/reconcile existing serialized stock before stocktaking.')
            units = product.units.filter(status__in=ON_HAND)
            if location:
                units = units.filter(location=location)
            for unit in units.order_by('pk'):
                StocktakeLine.objects.create(session=session, product=product, unit=unit, expected=1,
                    expected_status=unit.status, **values)
        elif not location:
            StocktakeLine.objects.create(session=session, product=product, expected=product.quantity, **values)
    return session


@transaction.atomic
def scan_stocktake(*, session_id, code, key, actor):
    session = Stocktake.objects.select_for_update().get(pk=session_id)
    code = normalize(code)
    key, previous = request_result(key, f'stocktake:{session.pk}', code, actor)
    if previous is not None:
        return previous
    if session.status != 'OPEN':
        raise ValidationError('This stocktake is no longer open for counting.')
    outcome = 'COUNTED'
    try:
        product, unit = resolve(code)
    except ValidationError as exc:
        result = {'message': exc.messages[0], 'warning': True}
        outcome = 'UNKNOWN'
    else:
        if product.is_serial_tracked and not unit:
            raise ValidationError('Scan each physical Unit ID for serialized stock, not the product barcode.')
        if not product.track_inventory:
            raise ValidationError('This product is not inventory-tracked.')
        if session.location and not unit:
            raise ValidationError('Location stocktakes count physical units only. Use an all-stock session for quantity products.')
        line, created = StocktakeLine.objects.get_or_create(session=session, product=product, unit=unit,
            defaults={**baseline(product), 'expected': 0, 'expected_status': unit.status if unit else ''})
        if unit and line.counted:
            outcome = 'DUPLICATE'
        else:
            line.counted += 1
            line.save(update_fields=['counted'])
            if created:
                outcome = 'UNEXPECTED'
        result = {'message': f'{outcome.title()}: {unit.unit_id if unit else product.name}', 'outcome': outcome}
    StocktakeScan.objects.create(session=session, code=code, outcome=outcome, actor=actor)
    ScanRequest.objects.create(key=key, scope=f'stocktake:{session.pk}', code=code, actor=actor, result=result)
    return result


@transaction.atomic
def set_quantity_count(*, session_id, line_id, count, reason, actor):
    session = Stocktake.objects.select_for_update().get(pk=session_id)
    if session.status != 'OPEN' or not reason.strip():
        raise ValidationError('Counts can only be corrected while open, with a reason.')
    try:
        count = int(count)
    except (ValueError, TypeError):
        raise ValidationError('Enter a whole number for the physical count.')
    if not 0 <= count <= 1000000:
        raise ValidationError('Count must be between zero and 1,000,000.')
    line = session.lines.select_related('product').filter(pk=line_id, unit__isnull=True, product__tracking_mode=Product.TRACK_QUANTITY).first()
    if not line:
        raise ValidationError('Choose a quantity-tracked product in this count. Machines must be scanned individually.')
    previous = line.counted
    line.counted = count
    line.save(update_fields=['counted'])
    StocktakeScan.objects.create(session=session, code=line.product.sku, outcome=f'SET_COUNT:{count}', actor=actor,
        note=f'{previous} → {count}. {reason.strip()[:1000]}')


@transaction.atomic
def submit_stocktake(*, session_id):
    session = Stocktake.objects.select_for_update().get(pk=session_id)
    if session.status != 'OPEN':
        raise ValidationError('Only an open stocktake can be submitted.')
    session.status = 'REVIEW'
    session.save(update_fields=['status'])


@transaction.atomic
def approve_stocktake(*, session_id, decisions, reason, actor):
    session = Stocktake.objects.select_for_update().get(pk=session_id)
    if session.status != 'REVIEW' or not reason.strip():
        raise ValidationError('Submit the count for review and provide an approval reason.')
    lines = list(session.lines.select_related('product', 'unit').order_by('product_id', 'pk'))
    products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=[l.product_id for l in lines]).order_by('pk')}
    current = {pk: baseline(product) for pk, product in products.items()}
    # Check the entire snapshot before any change. An activity that restored the
    # same final quantity still invalidates a count via movement/event versions.
    for line in lines:
        if current[line.product_id] != {k: getattr(line, k) for k in current[line.product_id]}:
            raise ValidationError('Stock changed during this count. Keep this session as evidence and start a fresh count.')
        if line.variance and decisions.get(str(line.pk)) not in ('KEEP', 'ADJUST'):
            raise ValidationError('Choose Keep records or Apply count for every discrepancy.')
    for line in lines:
        decision = decisions.get(str(line.pk), 'KEEP')
        if decision == 'ADJUST' and line.variance:
            if line.unit_id:
                if line.expected != 1 or line.counted != 0 or line.expected_status != 'AVAILABLE':
                    raise ValidationError('Unexpected/reserved units require their normal transfer, return or reservation workflow. Choose Keep records.')
                transition_unit(unit_id=line.unit_id, action='write_off', actor=actor, note=f'Stocktake {session.pk}: {reason}')
            else:
                product = products[line.product_id]
                if line.counted < product.reserved:
                    raise ValidationError('Count cannot be below reserved quantity. Resolve reservations first.')
                movement = stock_move(product, 'IN' if line.variance > 0 else 'OUT', abs(line.variance), actor,
                    f'Stocktake {session.pk}: {reason}', product.avg_cost if line.variance > 0 else None)
                event(product=product, kind='STOCKTAKE_ADJUSTED', actor=actor, note=reason, movement=movement)
        line.decision = decision
        line.save(update_fields=['decision'])
    session.status = 'CLOSED'
    session.reason = reason
    session.approved_by = actor
    session.closed_at = timezone.now()
    session.save(update_fields=['status', 'reason', 'approved_by', 'closed_at'])
    return session
