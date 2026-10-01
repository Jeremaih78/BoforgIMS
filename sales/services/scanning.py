from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import Product, ProductUnit, ScanRequest
from inventory.services.identity import normalize, resolve, request_result, event, check_consistency
from sales.models import Invoice, Quotation
from sales.services import PricingService, StockService


@transaction.atomic
def scan_document(*, document_id, kind, code, key, actor):
    code = normalize(code)
    model = Invoice if kind == 'invoice' else Quotation
    doc = model.objects.select_for_update().get(pk=document_id)
    key, previous = request_result(key, f'{kind}:{doc.pk}', code, actor)
    if previous is not None:
        return previous
    if kind == 'invoice' and (doc.stock_finalized or doc.status == Invoice.PAID or doc.payments.exists()):
        raise ValidationError('Invoices with payments or finalized stock cannot be changed.')
    if kind == 'quotation' and Invoice.objects.filter(quotation=doc).exists():
        raise ValidationError('This quotation has already been converted to an invoice.')
    product, unit = resolve(code)
    product_ids = set(doc.lines.exclude(product__isnull=True).values_list('product_id', flat=True)) | {product.pk}
    locked_products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')}
    product = locked_products[product.pk]
    if not product.is_active:
        raise ValidationError('This product is inactive.')
    check_consistency(product)
    if product.is_serial_tracked and kind == 'invoice' and not unit:
        units = list(product.units.filter(status='AVAILABLE', sale_line__isnull=True, order_item__isnull=True).order_by('pk').values('unit_id', 'serial_number', 'location')[:50])
        return {'needs_unit': True, 'message': f'{product.name}: scan or select the physical unit.', 'units': units}
    line = None
    if unit and kind == 'invoice':
        unit = ProductUnit.objects.select_for_update().get(pk=unit.pk)
        if unit.status == 'RESERVED' and unit.sale_line_id and unit.sale_line.invoice_id == doc.pk:
            result = {'message': f'{unit.unit_id} is already on this invoice.', 'duplicate': True}
            ScanRequest.objects.create(key=key, scope=f'{kind}:{doc.pk}', code=code, actor=actor, result=result)
            return result
        if unit.status != 'AVAILABLE' or unit.sale_line_id or unit.order_item_id:
            raise ValidationError(f'{unit.unit_id} is {unit.get_status_display()} or assigned elsewhere.')
        # Fill an existing component/manual line before adding another machine.
        for candidate in doc.lines.filter(product=product).order_by('pk'):
            if candidate.product_units.count() < int(candidate.quantity):
                line = candidate
                break
    if line is None:
        line = doc.lines.filter(product=product, scan_generated=True).first()
        qty = int(line.quantity) + 1 if line else 1
        pricing = PricingService.apply_best_rule(product, qty, product.price)
        price = max(Decimal('0'), product.price * (1 - pricing.discount_percent / 100) - pricing.discount_value).quantize(Decimal('.01'))
        if line:
            line.quantity = qty
            line.unit_price = price
            line.line_total = qty * price
            line.save(update_fields=['quantity', 'unit_price', 'line_total'])
        else:
            line = doc.lines.create(product=product, description=product.name, quantity=1, unit_price=price,
                tax_rate_percent=product.tax_rate, line_total=price, scan_generated=True)
    if kind == 'invoice':
        StockService.reserve_stock(doc)
        if unit:
            unit.sale_line = line
            unit.status = 'RESERVED'
            unit.save(update_fields=['sale_line', 'status', 'updated_at'])
            event(unit, kind='RESERVED', actor=actor, invoice=doc, previous='AVAILABLE')
    result = {'message': f'Added {unit.unit_id if unit and kind == "invoice" else product.name}.', 'line_id': line.pk,
        'quantity': str(line.quantity), 'total': str(doc.total), 'unit_id': unit.unit_id if unit else None}
    ScanRequest.objects.create(key=key, scope=f'{kind}:{doc.pk}', code=code, actor=actor, result=result)
    return result
