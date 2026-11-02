from decimal import Decimal, ROUND_HALF_UP

from django.db import transaction

from inventory.models import Combo


TWOPLACES = Decimal('0.01')


def _validated_quantity(quantity):
    value = Decimal(str(quantity))
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError('Quantity must be a positive integer.')
    qty = int(value)
    if qty <= 0:
        raise ValueError('Quantity must be a positive integer.')
    return qty


def combo_discount_allocations(combo, quantity=1):
    """Allocate a bundle discount to tax-rate groups without losing a cent."""
    qty = _validated_quantity(quantity)
    base = combo.components_total() * qty
    discount = (base - combo.compute_price() * qty).quantize(TWOPLACES)
    if discount <= 0 or base <= 0:
        return []
    groups = {}
    for item in combo.items.all():
        rate = Decimal(item.product.tax_rate or 0)
        groups[rate] = groups.get(rate, Decimal('0')) + item.product.price * item.quantity * qty
    groups = sorted((rate, value) for rate, value in groups.items() if value > 0)
    left = discount
    allocations = []
    for index, (rate, value) in enumerate(groups):
        share = left if index == len(groups)-1 else min(left, (discount * value / base).quantize(TWOPLACES, rounding=ROUND_HALF_UP))
        left -= share
        if share:
            allocations.append((rate, share))
    return allocations


@transaction.atomic
def add_combo_to_document(document, combo_id, quantity=1, note_prefix='Combo: '):
    qty = _validated_quantity(quantity)
    combo = (
        Combo.objects.filter(is_active=True)
        .prefetch_related('items__product')
        .get(pk=combo_id)
    )

    items = list(combo.items.select_related('product'))
    if not items:
        raise ValueError('Combo has no components configured.')

    created_lines = []
    base_total = Decimal('0.00')

    for item in items:
        product = item.product
        if product is None:
            raise ValueError('Combo component is missing a product reference.')
        component_qty = item.quantity * qty
        line = document.add_product_line(
            product=product,
            qty=component_qty,
            unit_price=product.price,
            note=f"{note_prefix}{combo.name} ({combo.code})",
        )
        created_lines.append(line)
        base_total += Decimal(str(line.line_total))

    final_price = (Decimal(str(combo.compute_price())) * qty).quantize(TWOPLACES)
    discount_amount = (base_total - final_price).quantize(TWOPLACES)
    discount_line = None

    if discount_amount > Decimal('0.00'):
        if hasattr(document, 'add_misc_line'):
            allocations = combo_discount_allocations(combo, qty) if getattr(document, 'tax_added', False) else [(Decimal('0'), discount_amount)]
            for rate, amount in allocations:
                line = document.add_misc_line(description=f"{note_prefix}{combo.name} discount", amount=-amount, tax_rate_percent=rate)
                if discount_line is None:
                    discount_line = line
        else:
            total_value = sum(
                Decimal(str(line.unit_price)) * Decimal(str(line.quantity))
                for line in created_lines
            ) or Decimal('1.00')
            for line in created_lines:
                line_value = Decimal(str(line.unit_price)) * Decimal(str(line.quantity))
                share = line_value / total_value
                per_unit_discount = (discount_amount * share) / max(Decimal(str(line.quantity)), Decimal('1.00'))
                line.unit_price = max(
                    Decimal('0.00'),
                    Decimal(str(line.unit_price)) - per_unit_discount
                ).quantize(TWOPLACES)
                line.line_total = (Decimal(str(line.unit_price)) * Decimal(str(line.quantity))).quantize(TWOPLACES)
                line.save(update_fields=['unit_price', 'line_total'])

    if hasattr(document, 'append_note'):
        document.append_note(f"Added combo '{combo.name}' x{qty} (code: {combo.code}).")

    return created_lines, discount_line, final_price


def add_combo_to_invoice(invoice, combo_id, quantity=1, note_prefix='Combo: '):
    return add_combo_to_document(invoice, combo_id, quantity, note_prefix)


def add_combo_to_quotation(quotation, combo_id, quantity=1, note_prefix='Combo: '):
    return add_combo_to_document(quotation, combo_id, quantity, note_prefix)


def combo_available_quantity(combo):
    limits = []
    for item in combo.items.all():
        required = item.quantity
        if required <= 0:
            continue
        product = item.product
        stock = product.available_stock
        if not product.track_inventory:
            continue
        limits.append(stock // required if required else 0)
    return min(limits) if limits else 0
