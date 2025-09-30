from __future__ import annotations

from decimal import Decimal
from typing import Tuple

from django.db import transaction

from .models import Cart, CartItem
from inventory.models import Product


def get_or_create_cart(request) -> Cart:
    session_key = request.session.session_key
    if not session_key:
        request.session.create()
        session_key = request.session.session_key
    cart, _ = Cart.objects.get_or_create(session_key=session_key)
    return cart


def add_product_to_cart(cart: Cart, product: Product, quantity: int = 1) -> Tuple[CartItem, bool]:
    with transaction.atomic():
        item, created = CartItem.objects.select_for_update().get_or_create(
            cart=cart,
            product=product,
            defaults={'quantity': quantity},
        )
        if not created:
            item.quantity += quantity
            item.save(update_fields=['quantity', 'updated_at'])
    return item, created


def remove_product_from_cart(cart: Cart, product: Product) -> None:
    CartItem.objects.filter(cart=cart, product=product).delete()


def clear_cart(cart: Cart) -> None:
    cart.items.all().delete()


def cart_totals(cart: Cart) -> Decimal:
    return cart.subtotal


def cart_item_count(cart: Cart) -> int:
    return cart.item_count
