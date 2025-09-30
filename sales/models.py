from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from customers.models import Customer
from inventory.models import Product, Category


def next_number(model, prefix):
    last = model.objects.order_by('-id').first()
    n = (last.id + 1) if last else 1
    return f"{prefix}{n:05d}"
MONEY_QUANT = Decimal('0.01')


def to_decimal(value):
    return Decimal(str(value or 0)).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)





class Quotation(models.Model):
    DRAFT = 'DRAFT'
    SENT = 'SENT'
    CONVERTED = 'CONVERTED'
    STATUS_CHOICES = [(DRAFT, 'Draft'), (SENT, 'Sent'), (CONVERTED, 'Converted')]

    number = models.CharField(max_length=20, unique=True, default='')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    notes = models.TextField(blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number(Quotation, 'Q-')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number

    @property
    def total(self):
        return sum(line.line_total for line in self.lines.all())

    @property
    def items(self):
        # Backwards compatibility for templates still referencing quotation.items
        return self.lines.all()

    def add_product_line(self, product, qty, unit_price=None, note=None):
        qty_decimal = to_decimal(qty)
        if qty_decimal <= Decimal('0.00'):
            raise ValueError('Quantity must be positive for product lines.')
        base_price = product.price if unit_price is None else unit_price
        unit_decimal = to_decimal(base_price)
        line_total = (unit_decimal * qty_decimal).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        description = note or getattr(product, 'name', '')
        tax_rate = getattr(product, 'tax_rate', Decimal('0'))
        return self.lines.create(
            product=product,
            quantity=qty_decimal,
            unit_price=unit_decimal,
            tax_rate_percent=to_decimal(tax_rate),
            line_total=line_total,
            description=description,
        )

    def add_misc_line(self, description, amount):
        amount_decimal = to_decimal(amount)
        return self.lines.create(
            description=description or 'Adjustment',
            quantity=Decimal('1.00'),
            unit_price=amount_decimal,
            tax_rate_percent=Decimal('0.00'),
            line_total=amount_decimal,
        )

    def append_note(self, message):
        if not message:
            return
        message = message.strip()
        if not message:
            return
        existing = (self.notes or '').strip()
        combined = f"{existing}\n{message}" if existing else message
        self.notes = combined
        self.save(update_fields=['notes'])


class Invoice(models.Model):
    PENDING = 'PENDING'
    PAID = 'PAID'
    OVERDUE = 'OVERDUE'
    CONFIRMED = 'CONFIRMED'
    STATUS_CHOICES = [
        (PENDING, 'Pending'),
        (PAID, 'Paid'),
        (OVERDUE, 'Overdue'),
        (CONFIRMED, 'Confirmed'),
    ]

    number = models.CharField(max_length=20, unique=True, default='')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    quotation = models.ForeignKey(Quotation, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True, null=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    def save(self, *args, **kwargs):
        if not self.number:
            self.number = next_number(Invoice, 'INV-')
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number

    @property
    def total(self):
        return sum(line.line_total for line in self.lines.all())

    @property
    def items(self):
        return self.lines.all()

    def add_product_line(self, product, qty, unit_price=None, note=None):
        qty_decimal = to_decimal(qty)
        if qty_decimal <= Decimal('0.00'):
            raise ValueError('Quantity must be positive for product lines.')
        base_price = product.price if unit_price is None else unit_price
        unit_decimal = to_decimal(base_price)
        line_total = (unit_decimal * qty_decimal).quantize(MONEY_QUANT, rounding=ROUND_HALF_UP)
        description = note or getattr(product, 'name', '')
        tax_rate = getattr(product, 'tax_rate', Decimal('0'))
        return self.lines.create(
            product=product,
            quantity=qty_decimal,
            unit_price=unit_decimal,
            tax_rate_percent=to_decimal(tax_rate),
            line_total=line_total,
            description=description,
        )

    def add_misc_line(self, description, amount):
        amount_decimal = to_decimal(amount)
        return self.lines.create(
            description=description or 'Adjustment',
            quantity=Decimal('1.00'),
            unit_price=amount_decimal,
            tax_rate_percent=Decimal('0.00'),
            line_total=amount_decimal,
        )

    def append_note(self, message):
        if not message:
            return
        message = message.strip()
        if not message:
            return
        existing = (self.notes or '').strip()
        combined = f"{existing}\n{message}" if existing else message
        self.notes = combined
        self.save(update_fields=['notes'])

    def confirm(self, user=None):
        self.status = self.CONFIRMED
        updated_fields = ['status']
        if user and not self.created_by:
            self.created_by = user
            updated_fields.append('created_by')
        self.save(update_fields=updated_fields)
        return self


class DocumentLine(models.Model):
    product = models.ForeignKey("inventory.Product", null=True, blank=True, on_delete=models.PROTECT)
    combo = models.ForeignKey("inventory.Combo", null=True, blank=True, on_delete=models.PROTECT)
    description = models.CharField(max_length=255, blank=True)

    quantity = models.DecimalField(max_digits=12, decimal_places=2, default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_rate_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=12, decimal_places=2)

    quotation = models.ForeignKey("Quotation", null=True, blank=True, related_name="lines", on_delete=models.CASCADE)
    invoice = models.ForeignKey("Invoice", null=True, blank=True, related_name="lines", on_delete=models.CASCADE)

    def clean(self):
        has_product = bool(self.product)
        has_combo = bool(self.combo)
        has_description = bool(self.description)
        if has_product and has_combo:
            raise ValidationError("DocumentLine cannot reference both product and combo.")
        if not has_product and not has_combo and not has_description:
            raise ValidationError("Provide a product or a description for this line.")

    def save(self, *args, **kwargs):
        if self.line_total is None:
            self.line_total = Decimal(self.unit_price or 0) * Decimal(self.quantity or 0)
        self.line_total = Decimal(self.line_total).quantize(Decimal('0.01'))
        super().save(*args, **kwargs)

    def __str__(self):
        target = self.product or self.combo or self.description or 'Line'
        return f"{target} x {self.quantity}"


class Payment(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='payments', on_delete=models.CASCADE)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    date = models.DateField(default=timezone.now)
    method = models.CharField(max_length=50, default='Cash')
    note = models.CharField(max_length=200, blank=True, null=True)


class StockReservation(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='reservations', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.CASCADE)
    quantity = models.IntegerField()
    created_at = models.DateTimeField(auto_now_add=True)


class PriceRule(models.Model):
    DISCOUNT = 'DISCOUNT'
    PROMOTION = 'PROMOTION'
    TYPE_CHOICES = [(DISCOUNT, 'Discount'), (PROMOTION, 'Promotion')]
    PRODUCT = 'PRODUCT'
    CATEGORY = 'CATEGORY'
    CART = 'CART'
    SCOPE_CHOICES = [(PRODUCT, 'Product'), (CATEGORY, 'Category'), (CART, 'Cart')]
    PERCENT = 'PERCENT'
    FIXED = 'FIXED'
    VALUE_TYPE_CHOICES = [(PERCENT, 'Percent'), (FIXED, 'Fixed')]

    name = models.CharField(max_length=150)
    rule_type = models.CharField(max_length=12, choices=TYPE_CHOICES, default=DISCOUNT)
    scope = models.CharField(max_length=12, choices=SCOPE_CHOICES, default=PRODUCT)
    value_type = models.CharField(max_length=10, choices=VALUE_TYPE_CHOICES, default=PERCENT)
    value = models.DecimalField(max_digits=12, decimal_places=2)
    start_at = models.DateTimeField(null=True, blank=True)
    end_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    product = models.ForeignKey(Product, null=True, blank=True, on_delete=models.CASCADE)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.CASCADE)
    min_qty = models.IntegerField(default=1)
    stackable = models.BooleanField(default=False)

    def __str__(self):
        return self.name

