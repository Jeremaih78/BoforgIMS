"""Shared combo information for search, validation and the sales editor."""
from decimal import Decimal

from inventory.services.combos import combo_available_quantity


def combo_details(combo):
    items = list(combo.items.all())
    if not combo.is_active or not items:
        raise ValueError('Choose an active combo with at least one component.')
    if any(not item.product.is_active or item.quantity < 1 for item in items):
        raise ValueError(f'{combo.name} has an inactive product or an invalid component quantity.')
    currencies = {item.product.currency for item in items}
    if len(currencies) != 1:
        raise ValueError(f'{combo.name} contains multiple currencies. Review its components first.')
    if combo.discount_value < 0 or any(item.product.price < 0 for item in items):
        raise ValueError(f'{combo.name} has an invalid price or discount.')
    base = combo.components_total()
    price = combo.compute_price()
    return {
        'id': combo.pk, 'name': combo.name, 'sku': combo.code, 'code': combo.code,
        'category': f'{len(items)} component type{"s" if len(items) != 1 else ""}',
        'price': str(price), 'base_price': str(base), 'discount': str(base - price),
        'currency': next(iter(currencies)), 'available_stock': combo_available_quantity(combo),
        'track_inventory': any(item.product.track_inventory for item in items),
        'components': [{'name': item.product.name, 'sku': item.product.sku, 'quantity': item.quantity,
                        'price': str(item.product.price), 'tax_rate': str(item.product.tax_rate)} for item in items],
    }


def validate_combo_quantity(combo, quantity):
    details = combo_details(combo)
    maximum = Decimal('9999999999.99')  # DocumentLine's decimal fields.
    if any(item.quantity * quantity > maximum or item.product.price * item.quantity * quantity > maximum for item in combo.items.all()):
        raise ValueError('This combo quantity exceeds the document line limit.')
    if Decimal(details['discount']) * quantity > maximum:
        raise ValueError('This combo discount exceeds the document line limit.')
    return details
