from django.db import transaction
from django.db.models.functions import Upper, Trim

from inventory.models import ProductUnit, Product
from sales.models import DocumentLine, Invoice


@transaction.atomic
def assign_serials(*, line_id, serials, actor=None):
    reference = DocumentLine.objects.get(pk=line_id)
    if not reference.invoice_id:
        raise ValueError('Serials can only be assigned to invoice lines.')
    invoice = Invoice.objects.select_for_update().get(pk=reference.invoice_id)
    line = DocumentLine.objects.select_related('product').get(pk=line_id)
    if invoice.stock_finalized or invoice.status == Invoice.PAID:
        raise ValueError('Finalized serial assignments cannot be changed.')
    if not line.product or not line.product.is_serial_tracked:
        raise ValueError('Line does not require serials.')
    if not isinstance(serials, list) or any(not isinstance(s, str) for s in serials):
        raise ValueError('Serial numbers must be a list of strings.')
    from inventory.services.identity import normalize, event
    from sales.services import StockService
    StockService.reserve_stock(invoice)
    cleaned = [normalize(s) for s in serials]
    if len(set(cleaned)) != int(line.quantity) or len(cleaned) != int(line.quantity) or '' in cleaned:
        raise ValueError(f'Assign exactly {int(line.quantity)} distinct serial numbers.')
    # Lock existing and new assignments together, in deterministic order.
    from django.db.models import Q
    units = list(ProductUnit.objects.select_for_update().annotate(canonical_serial=Upper(Trim('serial_number'))).filter(
        Q(sale_line=line) | Q(product=line.product, canonical_serial__in=cleaned) | Q(product=line.product, unit_id__in=cleaned)).order_by('pk'))
    selected = [u for u in units if (u.canonical_serial in cleaned or u.unit_id in cleaned) and u.product_id == line.product_id]
    if len(selected) != len(cleaned):
        raise ValueError('Some serial numbers do not belong to this product.')
    for unit in selected:
        if unit.order_item_id or unit.sale_line_id not in (None, line.pk) or unit.status not in (ProductUnit.STATUS_AVAILABLE, ProductUnit.STATUS_RESERVED):
            raise ValueError('A selected serial is assigned elsewhere or unavailable.')
    for unit in units:
        previous = unit.status
        unit.sale_line = line if unit in selected else None
        unit.status = ProductUnit.STATUS_RESERVED if unit in selected else ProductUnit.STATUS_AVAILABLE
        unit.save(update_fields=['sale_line', 'status', 'updated_at'])
        if previous != unit.status:
            event(unit, kind='RESERVED' if unit in selected else 'RELEASED', actor=actor or invoice.created_by, previous=previous, invoice=invoice)
    return cleaned
