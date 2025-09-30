from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import models
from django.urls import reverse
from django.utils.text import slugify
from django.templatetags.static import static
from django.utils import timezone


def product_image_path(instance, filename):
    return f"products/{instance.sku or 'no-sku'}/{filename}"


def _generate_unique_slug(instance, value, slug_field_name='slug', max_length=160):
    base_slug = slugify(value)[:max_length] or 'item'
    slug = base_slug
    model_class = instance.__class__
    counter = 1
    existing_qs = model_class.objects.filter(**{slug_field_name: slug})
    if instance.pk:
        existing_qs = existing_qs.exclude(pk=instance.pk)
    while existing_qs.exists():
        slug = f'{base_slug}-{counter}'[:max_length]
        counter += 1
        existing_qs = model_class.objects.filter(**{slug_field_name: slug})
        if instance.pk:
            existing_qs = existing_qs.exclude(pk=instance.pk)
    return slug


def default_currency_code():
    return getattr(settings, 'BASE_CURRENCY_CODE', 'USD')


class Category(models.Model):
    name = models.CharField(max_length=100, unique=True)
    slug = models.SlugField(max_length=120, unique=True, blank=True, null=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug and self.name:
            self.slug = _generate_unique_slug(self, self.name, max_length=120)
        super().save(*args, **kwargs)


class Supplier(models.Model):
    name = models.CharField(max_length=150)
    email = models.EmailField(blank=True, null=True)
    phone = models.CharField(max_length=50, blank=True, null=True)
    address = models.TextField(blank=True, null=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name


class Product(models.Model):
    name = models.CharField(max_length=150)
    sku = models.CharField(max_length=50, unique=True)
    slug = models.SlugField(max_length=160, unique=True, blank=True, null=True)
    category = models.ForeignKey(Category, on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey(Supplier, on_delete=models.SET_NULL, null=True, blank=True)
    price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    currency = models.CharField(max_length=5, default=default_currency_code)
    avg_cost = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    quantity = models.IntegerField(default=0)
    reserved = models.IntegerField(default=0)
    track_inventory = models.BooleanField(default=True)
    reorder_level = models.IntegerField(default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    description = models.TextField(blank=True, null=True)
    image = models.ImageField(upload_to=product_image_path, blank=True, null=True)
    image_url = models.URLField(blank=True)  # Deprecated: retained temporarily for migration fallback
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ('name',)
        indexes = [
            models.Index(fields=['slug']),
            models.Index(fields=['is_active', 'updated_at']),
        ]

    def __str__(self):
        return f'{self.name} ({self.sku})'

    def save(self, *args, **kwargs):
        if not self.slug and (self.name or self.sku):
            base = self.name or self.sku
            identifier = f'{base}-{self.sku}' if self.sku and self.sku not in base else base
            self.slug = _generate_unique_slug(self, identifier)
        if not self.currency:
            self.currency = default_currency_code()
        super().save(*args, **kwargs)

    def get_absolute_url(self):
        return reverse('shop:product_detail', args=[self.slug])

    @property
    def available_stock(self):
        return max((self.quantity or 0) - (self.reserved or 0), 0)

    def get_primary_image_url(self):
        if self.image:
            try:
                return self.image.url
            except ValueError:
                pass
        if self.image_url:
            return self.image_url
        return static('shop/placeholder-product.jpg')


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
        product = self.product
        qty = int(self.quantity)
        if self.movement_type == self.IN:
            if self.unit_cost is not None:
                old_qty = product.quantity or 0
                old_avg = product.avg_cost or 0
                new_qty = old_qty + qty
                if new_qty > 0:
                    total_cost = (
                        Decimal(str(old_avg)) * Decimal(str(old_qty))
                    ) + (Decimal(str(self.unit_cost)) * Decimal(str(qty)))
                    product.avg_cost = (total_cost / Decimal(str(new_qty))).quantize(Decimal('0.0001'))
            product.quantity = (product.quantity or 0) + qty
        else:
            product.quantity = (product.quantity or 0) - qty
        product.save()


class Combo(models.Model):
    DISCOUNT_NONE = 'none'
    DISCOUNT_FIXED = 'fixed'
    DISCOUNT_PERCENT = 'percent'
    DISCOUNT_CHOICES = [
        (DISCOUNT_NONE, 'None'),
        (DISCOUNT_FIXED, 'Fixed'),
        (DISCOUNT_PERCENT, 'Percent'),
    ]

    name = models.CharField(max_length=120, unique=True)
    code = models.SlugField(max_length=80, unique=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    discount_type = models.CharField(max_length=10, choices=DISCOUNT_CHOICES, default=DISCOUNT_NONE)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('name',)

    def __str__(self):
        return self.name

    def components_total(self):
        total = Decimal('0.00')
        for item in self.items.select_related('product').all():
            price = Decimal(str(item.product.price or Decimal('0.00')))
            qty = Decimal(str(item.quantity))
            total += price * qty
        return total.quantize(Decimal('0.01'))

    def compute_price(self):
        base = self.components_total()
        if self.discount_type == self.DISCOUNT_FIXED:
            return max(base - Decimal(str(self.discount_value)), Decimal('0.00')).quantize(Decimal('0.01'))
        if self.discount_type == self.DISCOUNT_PERCENT:
            percent = Decimal(str(self.discount_value)) / Decimal('100')
            return max(base - (base * percent), Decimal('0.00')).quantize(Decimal('0.01'))
        return base.quantize(Decimal('0.01'))


class ComboItem(models.Model):
    combo = models.ForeignKey(Combo, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey('inventory.Product', on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = (('combo', 'product'),)

    def __str__(self):
        return f"{self.product} x{self.quantity}"
