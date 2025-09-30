from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from inventory.models import Product

from .models import Cart, Order, OrderItem


@dataclass
class OrderCreationResult:
    order: Order
    reserved_products: list


class OrderCreationError(Exception):
    pass


def generate_order_number() -> str:
    base = timezone.now().strftime('BOF%y%m%d')
    sequence = 1
    while True:
        candidate = f'{base}-{sequence:04d}'
        if not Order.objects.filter(number=candidate).exists():
            return candidate
        sequence += 1


def ensure_product_available(product: Product, quantity: int) -> None:
    if not product.is_active:
        raise OrderCreationError(f'{product.name} is not available for purchase.')
    if product.track_inventory and product.available_stock < quantity:
        raise OrderCreationError(f'Not enough stock for {product.name}.')


@transaction.atomic
def create_order_from_cart(cart: Cart, *, email: str, full_name: str = '', notes: str = '') -> OrderCreationResult:
    items = list(cart.items.select_related('product'))
    if not items:
        raise OrderCreationError('Your cart is empty.')

    reserved_products = []
    order = Order.objects.create(
        number=generate_order_number(),
        email=email,
        full_name=full_name.strip(),
        notes=notes.strip(),
        total=Decimal('0.00'),
    )

    for cart_item in items:
        product = cart_item.product
        if not product:
            continue
        ensure_product_available(product, cart_item.quantity)
        line_total = (product.price or Decimal('0.00')) * cart_item.quantity
        OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=cart_item.quantity,
            line_total=line_total,
        )
        if product.track_inventory:
            Product.objects.filter(pk=product.pk).update(reserved=F('reserved') + cart_item.quantity)
            reserved_products.append((product.pk, cart_item.quantity))

    order.recalculate_total()
    order.save(update_fields=['total'])
    return OrderCreationResult(order=order, reserved_products=reserved_products)


def release_reservations(reservations: Iterable[tuple[int, int]]) -> None:
    for product_id, quantity in reservations:
        Product.objects.filter(pk=product_id, reserved__gte=quantity).update(reserved=F('reserved') - quantity)


def mark_order_as_paid(order: Order) -> None:
    order.status = Order.Status.PAID
    order.save(update_fields=['status', 'updated_at'])
    for item in order.items.select_related('product'):
        product = item.product
        if not product or not product.track_inventory:
            continue
        Product.objects.filter(pk=product.pk).update(
            quantity=F('quantity') - item.quantity,
            reserved=F('reserved') - item.quantity,
        )


def mark_order_as_failed(order: Order) -> None:
    order.status = Order.Status.FAILED
    order.save(update_fields=['status', 'updated_at'])
    for item in order.items.select_related('product'):
        product = item.product
        if not product or not product.track_inventory:
            continue
        Product.objects.filter(pk=product.pk).update(reserved=F('reserved') - item.quantity)
