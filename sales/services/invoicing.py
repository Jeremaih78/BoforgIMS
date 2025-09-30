from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from inventory.models import StockMovement


def validate_combo_component_stock(invoice):
    short = []
    for line in invoice.lines.select_related('combo'):
        combo = line.combo
        if not combo:
            continue
        items = list(combo.items.select_related('product'))
        if not items:
            short.append({'combo': combo.sku, 'error': 'Combo has no components'})
            continue
        for item in items:
            need = Decimal(line.quantity) * Decimal(item.qty_per_combo)
            have = Decimal(item.product.quantity)
            if have < need:
                short.append({
                    'combo': combo.sku,
                    'product': item.product.sku,
                    'need': float(need),
                    'have': float(have),
                })
    if short:
        raise ValidationError({'components': short})


def explode_combo_lines_to_stock(invoice):

    with transaction.atomic():
        for line in invoice.lines.select_related('combo').all():
            combo = line.combo
            if not combo:
                continue
            items = combo.items.select_related('product')
            if not items:
                raise ValidationError({'components': [{'combo': combo.sku, 'error': 'Combo has no components'}]})
            qty_of_combos = Decimal(line.quantity)
            for item in items:
                total_units = qty_of_combos * Decimal(item.qty_per_combo)
                StockMovement.objects.create(
                    product=item.product,
                    movement_type=StockMovement.OUT,
                    quantity=int(total_units),
                    note=f"INV-{invoice.id} {combo.sku}",
                    user=invoice.created_by if hasattr(invoice, 'created_by') else None,
                )


