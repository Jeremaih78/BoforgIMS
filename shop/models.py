from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from inventory.models import Product


def default_currency():
    return getattr(settings, 'BASE_CURRENCY_CODE', 'USD')


class Cart(models.Model):
    session_key = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-updated_at',)

    def __str__(self):
        return f'Cart {self.session_key}'

    @property
    def item_count(self):
        return self.items.aggregate(total=models.Sum('quantity'))['total'] or 0

    @property
    def subtotal(self):
        total = self.items.aggregate(
            total=models.Sum(models.F('quantity') * models.F('product__price'), output_field=models.DecimalField(max_digits=12, decimal_places=2))
        )['total']
        return total or Decimal('0.00')


class CartItem(models.Model):
    cart = models.ForeignKey(Cart, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    added_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('cart', 'product')
        ordering = ('-updated_at',)

    def __str__(self):
        return f'{self.product} x {self.quantity}'

    @property
    def line_total(self):
        return (self.product.price or Decimal('0.00')) * self.quantity


class Order(models.Model):
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        SHIPPED = 'shipped', 'Shipped'

    number = models.CharField(max_length=20, unique=True)
    email = models.EmailField()
    full_name = models.CharField(max_length=150, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=5, default=default_currency)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return self.number

    @property
    def is_paid(self):
        return self.status == self.Status.PAID

    def recalculate_total(self):
        total = self.items.aggregate(
            total=models.Sum(models.F('quantity') * models.F('unit_price'), output_field=models.DecimalField(max_digits=12, decimal_places=2))
        )['total'] or Decimal('0.00')
        self.total = total
        return total


class OrderItem(models.Model):
    order = models.ForeignKey(Order, on_delete=models.CASCADE, related_name='items')
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    product_name = models.CharField(max_length=150)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    quantity = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ('id',)

    def __str__(self):
        return f'{self.product_name} x {self.quantity}'


class Payment(models.Model):
    class Status(models.TextChoices):
        INITIATED = 'initiated', 'Initiated'
        PAID = 'paid', 'Paid'
        FAILED = 'failed', 'Failed'
        CANCELLED = 'cancelled', 'Cancelled'

    order = models.OneToOneField(Order, on_delete=models.CASCADE, related_name='payment')
    provider = models.CharField(max_length=20, default='paynow')
    provider_ref = models.CharField(max_length=100, blank=True)
    poll_url = models.URLField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.INITIATED)
    raw_response = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('-created_at',)

    def __str__(self):
        return f'{self.provider} payment for {self.order.number}'

    @property
    def is_paid(self):
        return self.status == self.Status.PAID
