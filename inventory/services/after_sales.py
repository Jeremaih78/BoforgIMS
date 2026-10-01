from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from inventory.models import Product, ProductUnit, ServiceCase, UnitSale
from .identity import transition_unit, event, stock_move, check_consistency


@transaction.atomic
def open_case(*, unit_id, problem, actor):
    reference = ProductUnit.objects.get(pk=unit_id)
    Product.objects.select_for_update().get(pk=reference.product_id)
    unit = ProductUnit.objects.select_for_update().get(pk=unit_id)
    if not problem.strip() or unit.service_cases.exclude(status='CLOSED').exists():
        raise ValidationError('Describe the problem. A unit can have only one open service case.')
    if unit.status == 'SOLD':
        unit = transition_unit(unit_id=unit.pk, action='return', actor=actor, note=problem)
    if unit.status not in ('RETURNED', 'FAULTY'):
        raise ValidationError('Receive a sold return or mark an on-site unit faulty before opening a case.')
    case = ServiceCase.objects.create(unit=unit, sale=unit.sales_history.first(), problem=problem, created_by=actor)
    event(unit, kind='CASE_OPENED', actor=actor, note=f'Case {case.pk}: {problem}')
    return case


@transaction.atomic
def update_case(*, case_id, action, inspection, resolution, actor, replacement=None):
    ref = ServiceCase.objects.select_related('unit').get(pk=case_id)
    Product.objects.select_for_update().get(pk=ref.unit.product_id)
    case = ServiceCase.objects.select_for_update().get(pk=case_id)
    if case.status == 'CLOSED':
        raise ValidationError('This case is closed.')
    if action == 'repair':
        transition_unit(unit_id=case.unit_id, action='repair', actor=actor, note=inspection or case.problem)
        case.status = 'REPAIR'
    elif action == 'close':
        if not resolution.strip():
            raise ValidationError('Record the resolution before closing the case.')
        case.status = 'CLOSED'
        case.closed_at = timezone.now()
        if replacement:
            if ServiceCase.objects.filter(unit_id=case.unit_id, sale_id=case.sale_id, replacement__isnull=False).exists():
                raise ValidationError('A replacement was already issued for this unit and sale. Review the existing case.')
            new_unit = ProductUnit.objects.select_for_update().get(pk=replacement.pk)
            product = Product.objects.get(pk=ref.unit.product_id)
            check_consistency(product)
            if not case.sale or new_unit.product_id != product.pk or new_unit.status != 'AVAILABLE' or new_unit.sale_line_id or new_unit.order_item_id:
                raise ValidationError('Replacement must be an available unit of the same product, with an original recorded sale.')
            stock_move(product, 'OUT', 1, actor, f'Warranty replacement for {case.unit.unit_id}; case {case.pk}')
            new_unit.status = 'SOLD'
            new_unit.sold_at = timezone.now()
            new_unit.save(update_fields=['status', 'sold_at', 'updated_at'])
            UnitSale.objects.create(unit=new_unit, invoice=case.sale.invoice, order_item=case.sale.order_item,
                customer_name=case.sale.customer_name, unit_price=0, unit_cost=new_unit.landed_cost,
                sold_at=new_unit.sold_at, warranty_expires=case.sale.warranty_expires)
            event(new_unit, kind='WARRANTY_REPLACEMENT', actor=actor, note=f'Replaces {case.unit.unit_id}; case {case.pk}', previous='AVAILABLE')
            case.replacement = new_unit
            check_consistency(product)
    else:
        raise ValidationError('Unknown service action.')
    case.technician = actor
    case.inspection = inspection
    case.resolution = resolution
    case.save()
    event(case.unit, kind='CASE_' + action.upper(), actor=actor, note=f'Case {case.pk}: {inspection} {resolution}')
    return case
