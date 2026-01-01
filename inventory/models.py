from decimal import Decimal

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import Sum
from django.templatetags.static import static
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify


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
    TRACK_SERIAL = 'SERIAL'
    TRACK_QUANTITY = 'QUANTITY'
    TRACKING_CHOICES = [
        (TRACK_QUANTITY, 'Quantity'),
        (TRACK_SERIAL, 'Serial Numbers'),
    ]

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
    tracking_mode = models.CharField(max_length=10, choices=TRACKING_CHOICES, default=TRACK_QUANTITY)
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
        if not self.tracking_mode:
            self.tracking_mode = self.TRACK_QUANTITY
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

    @property
    def is_serial_tracked(self) -> bool:
        return self.tracking_mode == self.TRACK_SERIAL


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


class Shipment(models.Model):
    INCOTERM_FOB = 'FOB'
    INCOTERM_CIF = 'CIF'
    INCOTERM_EXW = 'EXW'
    INCOTERM_CHOICES = [
        (INCOTERM_FOB, 'FOB'),
        (INCOTERM_CIF, 'CIF'),
        (INCOTERM_EXW, 'EXW'),
    ]

    METHOD_SEA = 'SEA'
    METHOD_AIR = 'AIR'
    METHOD_ROAD = 'ROAD'
    SHIPPING_METHOD_CHOICES = [
        (METHOD_SEA, 'Sea'),
        (METHOD_AIR, 'Air'),
        (METHOD_ROAD, 'Road'),
    ]

    STATUS_CREATED = 'CREATED'
    STATUS_IN_TRANSIT = 'IN_TRANSIT'
    STATUS_ARRIVED = 'ARRIVED'
    STATUS_CLEARED = 'CLEARED'
    STATUS_RECEIVED = 'RECEIVED'
    STATUS_CLOSED = 'CLOSED'
    STATUS_CHOICES = [
        (STATUS_CREATED, 'Created'),
        (STATUS_IN_TRANSIT, 'In transit'),
        (STATUS_ARRIVED, 'Arrived'),
        (STATUS_CLEARED, 'Cleared'),
        (STATUS_RECEIVED, 'Received'),
        (STATUS_CLOSED, 'Closed'),
    ]

    COST_BASIS_VALUE = 'VALUE'
    COST_BASIS_QUANTITY = 'QUANTITY'
    COST_BASIS_CHOICES = [
        (COST_BASIS_VALUE, 'Value'),
        (COST_BASIS_QUANTITY, 'Quantity'),
    ]

    ALLOWED_TRANSITIONS = {
        STATUS_CREATED: {STATUS_IN_TRANSIT},
        STATUS_IN_TRANSIT: {STATUS_ARRIVED},
        STATUS_ARRIVED: {STATUS_CLEARED, STATUS_RECEIVED},
        STATUS_CLEARED: {STATUS_RECEIVED},
        STATUS_RECEIVED: {STATUS_CLOSED},
        STATUS_CLOSED: set(),
    }

    shipment_code = models.CharField(max_length=32, unique=True, db_index=True)
    supplier = models.ForeignKey(Supplier, related_name='shipments', on_delete=models.PROTECT)
    origin_country = models.CharField(max_length=80)
    destination_country = models.CharField(max_length=80)
    incoterm = models.CharField(max_length=5, choices=INCOTERM_CHOICES)
    shipping_method = models.CharField(max_length=10, choices=SHIPPING_METHOD_CHOICES)
    eta_date = models.DateField(blank=True, null=True)
    arrival_date = models.DateField(blank=True, null=True)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_CREATED)
    allocation_basis = models.CharField(max_length=10, choices=COST_BASIS_CHOICES, default=COST_BASIS_VALUE)
    base_currency = models.CharField(max_length=5, default=default_currency_code)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='shipments_created', on_delete=models.SET_NULL, null=True, blank=True)
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='shipments_received', on_delete=models.SET_NULL, null=True, blank=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='shipments_closed', on_delete=models.SET_NULL, null=True, blank=True)
    received_at = models.DateTimeField(blank=True, null=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    landed_cost_allocated_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return self.shipment_code

    def save(self, *args, **kwargs):
        if not self.shipment_code:
            self.shipment_code = self._generate_code()
        if not self.base_currency:
            self.base_currency = default_currency_code()
        super().save(*args, **kwargs)

    def _generate_code(self) -> str:
        year_part = timezone.now().strftime('%Y')
        prefix = f"SHP-{year_part}-"
        last = Shipment.objects.filter(shipment_code__startswith=prefix).order_by('shipment_code').last()
        if last:
            try:
                seq = int(last.shipment_code.split('-')[-1]) + 1
            except ValueError:
                seq = 1
        else:
            seq = 1
        return f"{prefix}{seq:03d}"

    def allowed_next_statuses(self):
        return self.ALLOWED_TRANSITIONS.get(self.status, set())

    def can_transition(self, new_status: str) -> bool:
        if new_status == self.status:
            return True
        return new_status in self.allowed_next_statuses()

    def require_status(self, allowed_statuses: set[str]):
        if self.status not in allowed_statuses:
            raise ValidationError(f"Shipment {self.shipment_code} is not in a state that allows this action.")

    @property
    def total_cost_base(self) -> Decimal:
        return self.costs.aggregate(total=Sum('amount_base'))['total'] or Decimal('0.00')

    @property
    def total_item_value(self) -> Decimal:
        total = Decimal('0.00')
        for item in self.items.all():
            total += item.expected_value
        return total

    @property
    def total_quantity_expected(self) -> int:
        return sum(item.quantity_expected for item in self.items.all())

    @property
    def total_quantity_received(self) -> int:
        return sum(item.quantity_received for item in self.items.all())

    @property
    def is_fully_received(self) -> bool:
        items = self.items.all()
        if not items.exists():
            return False
        return all(item.quantity_received >= item.quantity_expected for item in items)

    @property
    def are_costs_allocated(self) -> bool:
        return not self.costs.filter(allocated=False).exists()

    def ensure_can_close(self):
        if not self.are_costs_allocated:
            raise ValidationError("All shipment costs must be allocated before closing the shipment.")
        if not self.is_fully_received:
            raise ValidationError("Shipment cannot be closed until all items are received.")

    def transition_status(self, new_status: str, actor=None, note: str = ''):
        if new_status == self.status:
            return self
        if not self.can_transition(new_status):
            raise ValidationError(f"Cannot move shipment from {self.status} to {new_status}.")
        if new_status == self.STATUS_RECEIVED and not self.is_fully_received:
            raise ValidationError("Cannot mark shipment as received until all items are received.")
        if new_status == self.STATUS_CLOSED:
            self.ensure_can_close()
        previous = self.status
        timestamp_fields = []
        if new_status == self.STATUS_RECEIVED:
            self.received_at = timezone.now()
            timestamp_fields.append('received_at')
            if actor and not self.received_by:
                self.received_by = actor
                timestamp_fields.append('received_by')
        if new_status == self.STATUS_CLOSED:
            self.closed_at = timezone.now()
            timestamp_fields.append('closed_at')
            if actor and not self.closed_by:
                self.closed_by = actor
                timestamp_fields.append('closed_by')
        self.status = new_status
        fields = ['status', 'updated_at'] + timestamp_fields
        self.save(update_fields=list(set(fields)))
        ShipmentEventLog.objects.create(
            shipment=self,
            previous_status=previous,
            new_status=new_status,
            actor=actor,
            note=note or '',
        )
        return self


class ShipmentCost(models.Model):
    TYPE_FREIGHT = 'FREIGHT'
    TYPE_DUTY = 'DUTY'
    TYPE_VAT = 'VAT'
    TYPE_CLEARING = 'CLEARING'
    TYPE_INSURANCE = 'INSURANCE'
    TYPE_OTHER = 'OTHER'
    TYPE_CHOICES = [
        (TYPE_FREIGHT, 'Freight'),
        (TYPE_DUTY, 'Duty'),
        (TYPE_VAT, 'VAT'),
        (TYPE_CLEARING, 'Clearing'),
        (TYPE_INSURANCE, 'Insurance'),
        (TYPE_OTHER, 'Other'),
    ]

    shipment = models.ForeignKey(Shipment, related_name='costs', on_delete=models.CASCADE)
    cost_type = models.CharField(max_length=15, choices=TYPE_CHOICES)
    description = models.CharField(max_length=255, blank=True)
    amount = models.DecimalField(max_digits=14, decimal_places=2, validators=[MinValueValidator(Decimal('0.00'))])
    currency = models.CharField(max_length=5, default=default_currency_code)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal('1.0'), validators=[MinValueValidator(Decimal('0.000001'))])
    amount_base = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('0.00'), editable=False)
    allocated = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['shipment', '-created_at']

    def save(self, *args, **kwargs):
        if not self.currency:
            self.currency = default_currency_code()
        amount = Decimal(str(self.amount or 0))
        fx = Decimal(str(self.fx_rate or 0))
        self.amount_base = (amount * fx).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.shipment.shipment_code} {self.cost_type} {self.amount} {self.currency}"


class ShipmentItem(models.Model):
    shipment = models.ForeignKey(Shipment, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, related_name='shipment_items', on_delete=models.PROTECT)
    quantity_expected = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    quantity_received = models.PositiveIntegerField(default=0)
    unit_purchase_price = models.DecimalField(max_digits=12, decimal_places=4, validators=[MinValueValidator(Decimal('0.0000'))])
    hs_code = models.CharField(max_length=50, blank=True)
    tracking_mode = models.CharField(max_length=10, choices=Product.TRACKING_CHOICES, default=Product.TRACK_QUANTITY)
    landed_unit_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    landed_total_cost = models.DecimalField(max_digits=14, decimal_places=4, default=Decimal('0.0000'))
    last_received_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['shipment', 'product__name']
        unique_together = (('shipment', 'product', 'hs_code'),)

    def __str__(self):
        return f"{self.product} ({self.quantity_expected})"

    @property
    def expected_value(self) -> Decimal:
        return (Decimal(str(self.quantity_expected)) * Decimal(str(self.unit_purchase_price))).quantize(Decimal('0.0001'))

    @property
    def received_value(self) -> Decimal:
        return (Decimal(str(self.quantity_received)) * Decimal(str(self.unit_purchase_price))).quantize(Decimal('0.0001'))

    @property
    def requires_serials(self) -> bool:
        return self.tracking_mode == Product.TRACK_SERIAL

    def clean(self):
        super().clean()
        if self.quantity_received > self.quantity_expected:
            raise ValidationError("Quantity received cannot exceed quantity expected.")

    def apply_landed_cost(self, allocation_amount: Decimal):
        """Persist landed cost for this item and sync linked serial units."""
        allocation_amount = Decimal(str(allocation_amount or 0))
        realized_qty = self.quantity_received or self.quantity_expected or 1
        purchase_total = Decimal(str(realized_qty)) * Decimal(str(self.unit_purchase_price))
        landed_total = (purchase_total + allocation_amount).quantize(Decimal('0.0001'))
        landed_unit = (landed_total / Decimal(str(realized_qty))).quantize(Decimal('0.0001'))
        self.landed_total_cost = landed_total
        self.landed_unit_cost = landed_unit
        self.save(update_fields=['landed_total_cost', 'landed_unit_cost'])
        if self.requires_serials:
            ProductUnit.objects.filter(shipment_item=self).update(landed_cost=self.landed_unit_cost, purchase_price=self.unit_purchase_price)


class ProductUnit(models.Model):
    STATUS_AVAILABLE = 'AVAILABLE'
    STATUS_RESERVED = 'RESERVED'
    STATUS_SOLD = 'SOLD'
    STATUS_FAULTY = 'FAULTY'
    STATUS_RETURNED = 'RETURNED'
    STATUS_CHOICES = [
        (STATUS_AVAILABLE, 'Available'),
        (STATUS_RESERVED, 'Reserved'),
        (STATUS_SOLD, 'Sold'),
        (STATUS_FAULTY, 'Faulty'),
        (STATUS_RETURNED, 'Returned'),
    ]

    serial_number = models.CharField(max_length=120, unique=True)
    product = models.ForeignKey(Product, related_name='units', on_delete=models.PROTECT)
    shipment = models.ForeignKey(Shipment, related_name='units', on_delete=models.PROTECT)
    shipment_item = models.ForeignKey(ShipmentItem, related_name='units', on_delete=models.PROTECT)
    purchase_price = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    landed_cost = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=STATUS_AVAILABLE)
    sale_line = models.ForeignKey('sales.DocumentLine', related_name='product_units', on_delete=models.SET_NULL, null=True, blank=True)
    sold_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='product_units_created', on_delete=models.SET_NULL, null=True, blank=True)
    fault_reported_at = models.DateTimeField(blank=True, null=True)
    fault_notes = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['serial_number']),
            models.Index(fields=['product', 'status']),
        ]

    def __str__(self):
        return f"{self.product.sku} #{self.serial_number}"

    def clean(self):
        super().clean()
        if self.sale_line_id and self.pk:
            original = ProductUnit.objects.filter(pk=self.pk).only('landed_cost').first()
            if original and original.landed_cost != self.landed_cost:
                raise ValidationError("Landed cost cannot be changed once the unit is linked to a sale.")

    @property
    def profit_amount(self) -> Decimal | None:
        if not self.sale_line:
            return None
        return Decimal(str(self.sale_line.line_total)) - Decimal(str(self.landed_cost))

    def mark_sold(self, sale_line, timestamp=None):
        self.sale_line = sale_line
        self.status = self.STATUS_SOLD
        self.sold_at = timestamp or timezone.now()
        self.save(update_fields=['sale_line', 'status', 'sold_at', 'updated_at'])

    def mark_faulty(self, notes='', timestamp=None):
        self.status = self.STATUS_FAULTY
        self.fault_notes = notes or ''
        self.fault_reported_at = timestamp or timezone.now()
        self.save(update_fields=['status', 'fault_notes', 'fault_reported_at', 'updated_at'])


class ShipmentEventLog(models.Model):
    EVENT_STATUS = 'STATUS'
    EVENT_COST = 'COST'
    EVENT_RECEIPT = 'RECEIPT'
    EVENT_CHOICES = [
        (EVENT_STATUS, 'Status'),
        (EVENT_COST, 'Cost Allocation'),
        (EVENT_RECEIPT, 'Receipt'),
    ]

    shipment = models.ForeignKey(Shipment, related_name='events', on_delete=models.CASCADE)
    event_type = models.CharField(max_length=10, choices=EVENT_CHOICES, default=EVENT_STATUS)
    previous_status = models.CharField(max_length=12, choices=Shipment.STATUS_CHOICES, blank=True)
    new_status = models.CharField(max_length=12, choices=Shipment.STATUS_CHOICES, blank=True)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.shipment.shipment_code} {self.event_type} {self.created_at:%Y-%m-%d}"
