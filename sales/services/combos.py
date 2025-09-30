from decimal import Decimal, ROUND_HALF_UP


def quantize(value):
    return (value or Decimal('0')).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def compute_combo_unit_price(combo):
    if combo.pricing_mode == combo.PRICING_FIXED:
        return quantize(combo.fixed_price or Decimal('0'))
    subtotal = Decimal('0')
    for item in combo.items.select_related('product'):
        base = item.price_override if item.price_override is not None else item.product.price
        subtotal += Decimal(base) * Decimal(item.qty_per_combo)
    discount = Decimal(combo.discount_percent) / Decimal('100')
    price = subtotal * (Decimal('1') - discount)
    return quantize(price)


def combo_available_qty(combo, products_queryset=None):
    limits = []
    items = combo.items.select_related('product')
    if products_queryset is not None:
        products = {p.pk: p for p in products_queryset}
    else:
        products = {}
    for item in items:
        product = products.get(item.product_id, item.product)
        stock = product.quantity
        per = Decimal(item.qty_per_combo)
        if per <= 0:
            limits.append(0)
        else:
            limits.append(int(Decimal(stock) // per))
    return min(limits) if limits else 0
