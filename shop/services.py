from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable
from uuid import uuid4

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from inventory.models import Product, ProductUnit

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
    if quantity <= 0:
        raise OrderCreationError('Quantity must be positive.')
    if not product.is_active or not product.is_public or product.price <= 0:
        raise OrderCreationError(f'{product.name} is not available for purchase.')
    if product.track_inventory and product.available_stock < quantity:
        raise OrderCreationError(f'Not enough stock for {product.name}.')


@transaction.atomic
def create_order_from_cart(cart: Cart, *, email: str, full_name: str = '', notes: str = '') -> OrderCreationResult:
    cart = Cart.objects.select_for_update().get(pk=cart.pk)
    from types import SimpleNamespace
    from collections import defaultdict
    from .packages import public_packages
    items = list(cart.items.select_related('product').order_by('product_id'))
    packages = list(cart.packages.select_related('combo').prefetch_related('combo__items__product'))
    from inventory.models import Combo
    list(Combo.objects.select_for_update().filter(pk__in=[p.combo_id for p in packages]).order_by('pk'))
    allowed = {p.pk: p for p in public_packages([p.combo_id for p in packages], limit=None)}
    discounts = []
    for package in packages:
        if package.combo_id not in allowed:
            raise OrderCreationError('A package is no longer available. Please review your cart.')
        combo = allowed[package.combo_id]
        for component in combo.items.all():
            items.append(SimpleNamespace(product_id=component.product_id, quantity=component.quantity * package.quantity))
        discounts.append((combo, package.quantity))
    products = {p.pk: p for p in Product.objects.select_for_update().filter(
        pk__in=[item.product_id for item in items]).order_by('pk')}
    demand = defaultdict(int)
    for item in items:
        item.product = products[item.product_id]
        demand[item.product_id] += item.quantity
    if not items:
        raise OrderCreationError('Your cart is empty.')
    if len({p.currency for p in products.values()}) != 1:
        raise OrderCreationError('Please order products in one currency at a time.')
    for product_id, quantity in demand.items():
        ensure_product_available(products[product_id], quantity)

    reserved_products = []
    order = Order.objects.create(
        number=uuid4().hex[:20],
        email=email,
        full_name=full_name.strip(),
        notes=notes.strip(),
        total=Decimal('0.00'),
        currency=next(iter(products.values())).currency,
    )

    order.number = f"BOF{timezone.localdate():%y%m%d}-{order.pk:04d}"
    order.save(update_fields=['number'])

    for cart_item in items:
        product = cart_item.product
        if not product:
            continue
        ensure_product_available(product, cart_item.quantity)
        line_total = (product.price or Decimal('0.00')) * cart_item.quantity
        order_item = OrderItem.objects.create(
            order=order,
            product=product,
            product_name=product.name,
            unit_price=product.price,
            quantity=cart_item.quantity,
            line_total=line_total,
        )
        if product.is_serial_tracked:
            from inventory.services.identity import check_consistency, event
            check_consistency(product)
            units = list(ProductUnit.objects.select_for_update().filter(product=product, status='AVAILABLE', sale_line__isnull=True, order_item__isnull=True).order_by('pk')[:cart_item.quantity])
            if len(units) != cart_item.quantity:
                raise OrderCreationError('Serialized stock requires reconciliation before online checkout.')
            for unit in units:
                unit.status = 'RESERVED'
                unit.order_item = order_item
                unit.save(update_fields=['status', 'order_item', 'updated_at'])
                event(unit, kind='SHOP_RESERVED', actor=None, note=order.number, previous='AVAILABLE')
        if product.track_inventory:
            Product.objects.filter(pk=product.pk).update(reserved=F('reserved') + cart_item.quantity)
            reserved_products.append((product.pk, cart_item.quantity))

    for combo, quantity in discounts:
        # Reuse the IMS discount rule, with freshly locked component prices.
        for component in combo.items.all():
            component.product = products[component.product_id]
        discount = (combo.components_total() - combo.compute_price()) * quantity
        if discount > 0:
            OrderItem.objects.create(order=order, product=None, product_name=f'{combo.name} package discount',
                                     unit_price=-discount, quantity=1, line_total=-discount)
    order.recalculate_total()
    order.save(update_fields=['total'])
    cart.items.all().delete()
    cart.packages.all().delete()
    return OrderCreationResult(order=order, reserved_products=reserved_products)


def release_reservations(reservations: Iterable[tuple[int, int]]) -> None:
    for product_id, quantity in reservations:
        Product.objects.filter(pk=product_id, reserved__gte=quantity).update(reserved=F('reserved') - quantity)


@transaction.atomic
def mark_order_as_paid(order: Order) -> None:
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status in (Order.Status.PAID, Order.Status.SHIPPED):
        order.status = locked.status
        return
    for item in locked.items.select_related('product').order_by('product_id'):
        if not item.product or not item.product.track_inventory:
            continue
        product = Product.objects.select_for_update().get(pk=item.product_id)
        # A failed order has already released its reservation. A late verified
        # payment must acquire available stock again, or require staff review.
        release = item.quantity if locked.status == Order.Status.PENDING else 0
        available = product.quantity - product.reserved + release
        if available < item.quantity or product.reserved < release:
            raise OrderCreationError('Verified payment requires stock reconciliation.')
        from inventory.services.identity import stock_move, check_consistency
        check_consistency(product)
        if product.is_serial_tracked:
            units = list(ProductUnit.objects.select_for_update().filter(order_item=item, status='RESERVED').order_by('pk'))
            if len(units) != item.quantity:
                raise OrderCreationError('Verified payment needs staff review: exact units are no longer reserved.')
            for unit in units:
                unit.mark_sold(order_item=item)
        product.reserved -= release
        product.save(update_fields=['reserved'])
        stock_move(product, 'OUT', item.quantity, None, f'Shop {locked.number}')
        check_consistency(product)
    locked.status = Order.Status.PAID
    locked.save(update_fields=['status', 'updated_at'])
    order.status = locked.status


@transaction.atomic
def mark_order_as_failed(order: Order) -> None:
    locked = Order.objects.select_for_update().get(pk=order.pk)
    if locked.status != Order.Status.PENDING:
        order.status = locked.status
        return
    for item in locked.items.select_related('product').order_by('product_id'):
        if not item.product or not item.product.track_inventory:
            continue
        product = Product.objects.select_for_update().get(pk=item.product_id)
        if product.reserved < item.quantity:
            raise OrderCreationError('Reservation requires staff reconciliation.')
        product.reserved -= item.quantity
        product.save(update_fields=['reserved'])
        from inventory.services.identity import event
        for unit in ProductUnit.objects.select_for_update().filter(order_item=item, status='RESERVED').order_by('pk'):
            unit.order_item = None
            unit.status = 'AVAILABLE'
            unit.save(update_fields=['order_item', 'status', 'updated_at'])
            event(unit, kind='SHOP_RELEASED', actor=None, note=locked.number, previous='RESERVED')
    locked.status = Order.Status.FAILED
    locked.save(update_fields=['status', 'updated_at'])
    order.status = locked.status
