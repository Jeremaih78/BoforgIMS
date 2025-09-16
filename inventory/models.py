from django.db import models
from decimal import Decimal
from django.contrib.auth import get_user_model

class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    def __str__(self): return self.name

class Supplier(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)
    def __str__(self): return self.name

class Product(models.Model):
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    avg_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    quantity = models.IntegerField(default=0)
    reserved = models.IntegerField(default=0)
    track_inventory = models.BooleanField(default=True)
    reorder_level = models.IntegerField(default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    def __str__(self): return f"{self.name} ({self.sku})"

class StockMovement(models.Model):
    IN = 'IN'
    OUT = 'OUT'
    MOVEMENT_CHOICES = [(IN, 'In'), (OUT, 'Out')]
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='movements')
    movement_type = models.CharField(max_length=3, choices=MOVEMENT_CHOICES)
    quantity = models.IntegerField()
    unit_cost = models.DecimalField(max_digits=12, decimal_places=4, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=255, blank=True, null=True)
    user = models.ForeignKey(get_user_model(), on_delete=models.SET_NULL, null=True, blank=True)

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        # Adjust stock
        p = self.product
        qty = int(self.quantity)
        if self.movement_type == self.IN:
            # Update moving average cost if unit_cost provided
            if self.unit_cost is not None:
                old_qty = p.quantity or 0
                old_avg = p.avg_cost or 0
                new_qty = old_qty + qty
                if new_qty > 0:
                    total_cost = (Decimal(str(old_avg)) * Decimal(str(old_qty))) + (Decimal(str(self.unit_cost)) * Decimal(str(qty)))
                    p.avg_cost = (total_cost / Decimal(str(new_qty))).quantize(Decimal('0.0001'))
            p.quantity = (p.quantity or 0) + qty
        else:
            p.quantity = (p.quantity or 0) - qty
        p.save()
