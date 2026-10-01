"""Inventory identities, observations and immutable traceability records."""
from django.conf import settings
from django.db import models
from django.core.exceptions import ValidationError
from django.utils import timezone


class ImmutableRecord(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError('Audit records cannot be edited.')
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Audit records cannot be deleted.')


class ProductBarcode(ImmutableRecord):
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='barcodes')
    code = models.CharField(max_length=120, unique=True)
    kind = models.CharField(max_length=12, choices=[('MANUFACTURER', 'Manufacturer / alternate'), ('INTERNAL', 'Boforg')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.code


class UnitEvent(ImmutableRecord):
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT, related_name='identity_events')
    unit = models.ForeignKey('inventory.ProductUnit', null=True, blank=True, on_delete=models.PROTECT, related_name='events')
    event = models.CharField(max_length=40)
    previous_status = models.CharField(max_length=12, blank=True)
    status = models.CharField(max_length=12, blank=True)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    movement = models.ForeignKey('inventory.StockMovement', null=True, on_delete=models.PROTECT)
    invoice = models.ForeignKey('sales.Invoice', null=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at', '-pk']


class UnitSale(ImmutableRecord):
    unit = models.ForeignKey('inventory.ProductUnit', on_delete=models.PROTECT, related_name='sales_history')
    invoice = models.ForeignKey('sales.Invoice', null=True, on_delete=models.PROTECT)
    line = models.ForeignKey('sales.DocumentLine', null=True, on_delete=models.PROTECT)
    order_item = models.ForeignKey('shop.OrderItem', null=True, on_delete=models.PROTECT)
    customer_name = models.CharField(max_length=255)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4)
    sold_at = models.DateTimeField(default=timezone.now)
    warranty_expires = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ['-sold_at', '-pk']


class ScanRequest(ImmutableRecord):
    key = models.UUIDField(unique=True)
    scope = models.CharField(max_length=100)
    code = models.CharField(max_length=120)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    result = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)


class Stocktake(models.Model):
    name = models.CharField(max_length=150)
    location = models.CharField(max_length=100, blank=True)
    status = models.CharField(max_length=12, default='OPEN', choices=[('OPEN', 'Counting'), ('REVIEW', 'Awaiting approval'), ('CLOSED', 'Approved')])
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True)

    class Meta:
        permissions = [('approve_stocktake', 'Approve inventory reconciliation'), ('manage_unit_lifecycle', 'Manage unit lifecycle and after-sales'), ('print_inventory_labels', 'Print inventory labels')]


class StocktakeLine(models.Model):
    session = models.ForeignKey(Stocktake, on_delete=models.PROTECT, related_name='lines')
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    unit = models.ForeignKey('inventory.ProductUnit', null=True, on_delete=models.PROTECT)
    expected = models.PositiveIntegerField(default=0)
    counted = models.PositiveIntegerField(default=0)
    expected_status = models.CharField(max_length=12, blank=True)
    baseline_quantity = models.IntegerField(default=0)
    baseline_reserved = models.IntegerField(default=0)
    baseline_event_id = models.BigIntegerField(default=0)
    baseline_movement_id = models.BigIntegerField(default=0)
    decision = models.CharField(max_length=12, default='REVIEW', choices=[('REVIEW', 'Review'), ('KEEP', 'Keep records'), ('ADJUST', 'Apply count')])

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['session', 'unit'], condition=models.Q(unit__isnull=False), name='stocktake_unique_unit'),
            models.UniqueConstraint(fields=['session', 'product'], condition=models.Q(unit__isnull=True), name='stocktake_unique_product'),
        ]

    @property
    def variance(self):
        return self.counted - self.expected


class StocktakeScan(ImmutableRecord):
    session = models.ForeignKey(Stocktake, on_delete=models.PROTECT, related_name='scans')
    code = models.CharField(max_length=120)
    outcome = models.CharField(max_length=20)
    note = models.TextField(blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    created_at = models.DateTimeField(auto_now_add=True)


class ServiceCase(models.Model):
    unit = models.ForeignKey('inventory.ProductUnit', on_delete=models.PROTECT, related_name='service_cases')
    sale = models.ForeignKey(UnitSale, null=True, on_delete=models.PROTECT)
    problem = models.TextField()
    inspection = models.TextField(blank=True)
    resolution = models.TextField(blank=True)
    status = models.CharField(max_length=12, default='OPEN', choices=[('OPEN', 'Open'), ('REPAIR', 'Repair'), ('CLOSED', 'Closed')])
    replacement = models.ForeignKey('inventory.ProductUnit', null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name='+')
    technician = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    created_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
