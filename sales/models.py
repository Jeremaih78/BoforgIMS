from django.db import models
from django.utils import timezone
from inventory.models import Product, Category
from customers.models import Customer

def next_number(model, prefix):
    last = model.objects.order_by('-id').first()
    n = (last.id + 1) if last else 1
    return f"{prefix}{n:05d}"

class Quotation(models.Model):
    DRAFT='DRAFT'; SENT='SENT'; CONVERTED='CONVERTED'
    STATUS_CHOICES=[(DRAFT,'Draft'),(SENT,'Sent'),(CONVERTED,'Converted')]
    number = models.CharField(max_length=20, unique=True, default='')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    status = models.CharField(max_length=12, choices=STATUS_CHOICES, default=DRAFT)
    notes = models.TextField(blank=True, null=True)
    @property
    def total(self):
        return sum([i.total for i in self.items.all()])
    def save(self,*a,**k):
        if not self.number: self.number = next_number(Quotation, 'Q-')
        super().save(*a,**k)
    def __str__(self): return self.number

class QuotationItem(models.Model):
    quotation = models.ForeignKey(Quotation, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=200, blank=True, null=True)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    @property
    def total(self):
        price = self.unit_price
        if self.discount_percent:
            price = price * (1 - (self.discount_percent / 100))
        price = price - self.discount_value
        if price < 0: price = 0
        total = self.quantity * price
        if self.tax_rate:
            total = total * (1 + (self.tax_rate / 100))
        return total

class Invoice(models.Model):
    PENDING='PENDING'; PAID='PAID'; OVERDUE='OVERDUE'
    STATUS_CHOICES=[(PENDING,'Pending'),(PAID,'Paid'),(OVERDUE,'Overdue')]
    number = models.CharField(max_length=20, unique=True, default='')
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE)
    date = models.DateField(default=timezone.now)
    due_date = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=PENDING)
    quotation = models.ForeignKey(Quotation, null=True, blank=True, on_delete=models.SET_NULL)
    notes = models.TextField(blank=True, null=True)
    @property
    def total(self): return sum([i.total for i in self.items.all()])
    def save(self,*a,**k):
        if not self.number: self.number = next_number(Invoice, 'INV-')
        super().save(*a,**k)
    def __str__(self): return self.number

class InvoiceItem(models.Model):
    invoice = models.ForeignKey(Invoice, related_name='items', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, on_delete=models.SET_NULL, null=True, blank=True)
    description = models.CharField(max_length=200, blank=True, null=True)
    quantity = models.IntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_value = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_rate = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    @property
    def total(self):
        price = self.unit_price
        if self.discount_percent:
            price = price * (1 - (self.discount_percent / 100))
        price = price - self.discount_value
        if price < 0: price = 0
        total = self.quantity * price
        if self.tax_rate:
            total = total * (1 + (self.tax_rate / 100))
        return total

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
    DISCOUNT='DISCOUNT'; PROMOTION='PROMOTION'
    TYPE_CHOICES=[(DISCOUNT,'Discount'),(PROMOTION,'Promotion')]
    PRODUCT='PRODUCT'; CATEGORY='CATEGORY'; CART='CART'
    SCOPE_CHOICES=[(PRODUCT,'Product'),(CATEGORY,'Category'),(CART,'Cart')]
    PERCENT='PERCENT'; FIXED='FIXED'
    VALUE_TYPE_CHOICES=[(PERCENT,'Percent'),(FIXED,'Fixed')]
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
    def __str__(self): return self.name
