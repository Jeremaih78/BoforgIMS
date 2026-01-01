from __future__ import annotations

from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class Company(models.Model):
    VALID = "VALID"; EXPIRED = "EXPIRED"; INVALID = "INVALID"
    LICENSE_STATUS_CHOICES = [(VALID, "Valid"), (EXPIRED, "Expired"), (INVALID, "Invalid")]

    name = models.CharField(max_length=150, unique=True)
    logo = models.FileField(upload_to="company/", blank=True, null=True)
    brand_primary_color = models.CharField(max_length=20, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    license_key = models.CharField(max_length=200, blank=True, null=True)
    license_status = models.CharField(max_length=10, choices=LICENSE_STATUS_CHOICES, default=VALID)
    base_currency = models.ForeignKey('Currency', on_delete=models.SET_NULL, null=True, blank=True, related_name='as_base_for_companies')

    def __str__(self):
        return self.name

class Currency(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, related_name='currencies', null=True, blank=True)
    code = models.CharField(max_length=3)
    name = models.CharField(max_length=50)
    symbol = models.CharField(max_length=5, blank=True, null=True)
    is_base = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if self.is_base:
            Currency.objects.exclude(pk=self.pk).update(is_base=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.code


class ExchangeRate(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.CASCADE, related_name="rates")
    rate = models.DecimalField(max_digits=18, decimal_places=6)
    date = models.DateField()

    class Meta:
        unique_together = ("currency", "date")
        ordering = ["-date", "-id"]


class TaxRate(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    rate = models.DecimalField(max_digits=7, decimal_places=4)
    is_default = models.BooleanField(default=False)
    is_claimable = models.BooleanField(default=True)

    def save(self, *args, **kwargs):
        if self.is_default:
            TaxRate.objects.exclude(pk=self.pk).update(is_default=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.name} {self.rate}%"


class FiscalPeriod(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=50, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()
    is_closed = models.BooleanField(default=False)
    locked_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return self.name


class Account(models.Model):
    ASSET = "ASSET"
    LIABILITY = "LIABILITY"
    EQUITY = "EQUITY"
    REVENUE = "REVENUE"
    EXPENSE = "EXPENSE"
    TYPE_CHOICES = [
        (ASSET, "Asset"),
        (LIABILITY, "Liability"),
        (EQUITY, "Equity"),
        (REVENUE, "Revenue"),
        (EXPENSE, "Expense"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    code = models.CharField(max_length=20)
    name = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=TYPE_CHOICES)
    is_active = models.BooleanField(default=True)
    parent = models.ForeignKey("self", on_delete=models.SET_NULL, null=True, blank=True, related_name="children")

    class Meta:
        ordering = ["code"]
        unique_together = (("company", "code"),)

    def __str__(self):
        return f"{self.code} {self.name}"


class NumberSequence(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    key = models.CharField(max_length=30)
    prefix = models.CharField(max_length=10, default="")
    next_number = models.IntegerField(default=1)

    def next(self) -> str:
        value = f"{self.prefix}{self.next_number:05d}"
        self.next_number += 1
        self.save(update_fields=["next_number"])
        return value


class JournalEntry(models.Model):
    SOURCE_CHOICES = [
        ("INVOICE", "Invoice"),
        ("PAYMENT", "Payment"),
        ("BILL", "Supplier Bill"),
        ("EXPENSE", "Expense"),
        ("SHIPMENT", "Shipment"),
        ("ADJUSTMENT", "Adjustment"),
    ]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    number = models.CharField(max_length=20, unique=True, blank=True, default="")
    date = models.DateField(default=timezone.now)
    memo = models.CharField(max_length=255, blank=True, null=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1.0"))
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    posted_at = models.DateTimeField(blank=True, null=True)
    is_posted = models.BooleanField(default=False)
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, default="ADJUSTMENT")
    source_id = models.IntegerField(blank=True, null=True)

    def clean(self):
        if self.is_posted:
            totals = self.lines.aggregate(d=models.Sum("debit_base"), c=models.Sum("credit_base"))
            d = totals.get("d") or Decimal("0")
            c = totals.get("c") or Decimal("0")
            if d.quantize(Decimal("0.01")) != c.quantize(Decimal("0.01")):
                raise ValidationError("Posted journal must be balanced (base currency)")

    def save(self, *args, **kwargs):
        if not self.number:
            seq, _ = NumberSequence.objects.get_or_create(company=self.company, key="JE", defaults={"prefix": "JE-"})
            self.number = seq.next()
        super().save(*args, **kwargs)

    def __str__(self):
        return self.number


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    description = models.CharField(max_length=255, blank=True, null=True)
    debit = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    credit = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    # base currency amounts
    debit_base = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    credit_base = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))

    # optional dimensions
    department = models.CharField(max_length=100, blank=True, null=True)
    product = models.ForeignKey("inventory.Product", on_delete=models.SET_NULL, null=True, blank=True)
    customer = models.ForeignKey("customers.Customer", on_delete=models.SET_NULL, null=True, blank=True)
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["account",]),
        ]


class BankAccount(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)

    def __str__(self):
        return self.name


class ExpenseCategory(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    name = models.CharField(max_length=100)
    default_account = models.ForeignKey(Account, on_delete=models.PROTECT)
    default_tax = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True)
    notes = models.TextField(blank=True, null=True)

    class Meta:
        unique_together = (("company", "name"),)

    def __str__(self):
        return self.name


class Expense(models.Model):
    DRAFT = "DRAFT"; POSTED = "POSTED"; VOID = "VOID"
    STATUS_CHOICES = [(DRAFT, "Draft"), (POSTED, "Posted"), (VOID, "Void")]

    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    doc_no = models.CharField(max_length=20, unique=True, blank=True, default="")
    date = models.DateField(default=timezone.now)
    payee = models.CharField(max_length=150)
    category = models.ForeignKey(ExpenseCategory, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    tax = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1.0"))
    notes = models.TextField(blank=True, null=True)
    attachment = models.FileField(upload_to="expenses/", blank=True, null=True)
    posted = models.BooleanField(default=False)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)

    def save(self, *args, **kwargs):
        if not self.doc_no:
            seq, _ = NumberSequence.objects.get_or_create(company=self.company, key="EXP", defaults={"prefix": "EXP-"})
            self.doc_no = seq.next()
        super().save(*args, **kwargs)


class SupplierBill(models.Model):
    DRAFT = "DRAFT"; POSTED = "POSTED"; PARTIAL = "PARTIAL"; PAID = "PAID"
    STATUS_CHOICES = [(DRAFT, "Draft"), (POSTED, "Posted"), (PARTIAL, "Partial"), (PAID, "Paid")]
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    doc_no = models.CharField(max_length=20, unique=True, blank=True, default="")
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.PROTECT)
    date = models.DateField(default=timezone.now)
    due_date = models.DateField(blank=True, null=True)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1.0"))
    subtotal = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    tax = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    total = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=DRAFT)

    def save(self, *args, **kwargs):
        if not self.doc_no:
            seq, _ = NumberSequence.objects.get_or_create(company=self.company, key="BILL", defaults={"prefix": "BILL-"})
            self.doc_no = seq.next()
        super().save(*args, **kwargs)


class SupplierBillLine(models.Model):
    bill = models.ForeignKey(SupplierBill, on_delete=models.CASCADE, related_name="lines")
    description = models.CharField(max_length=200)
    qty = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    unit_price = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("0"))
    account = models.ForeignKey(Account, on_delete=models.PROTECT)
    tax = models.ForeignKey(TaxRate, on_delete=models.SET_NULL, null=True, blank=True)
    product = models.ForeignKey("inventory.Product", on_delete=models.SET_NULL, null=True, blank=True)


class ARPayment(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    receipt_no = models.CharField(max_length=20, unique=True, blank=True, default="")
    customer = models.ForeignKey("customers.Customer", on_delete=models.PROTECT)
    date = models.DateField(default=timezone.now)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1.0"))
    bank = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    method = models.CharField(max_length=50, default="Cash")
    notes = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.receipt_no:
            seq, _ = NumberSequence.objects.get_or_create(company=self.company, key="ARPAY", defaults={"prefix": "RCPT-"})
            self.receipt_no = seq.next()
        super().save(*args, **kwargs)


class APPayment(models.Model):
    company = models.ForeignKey(Company, on_delete=models.CASCADE, null=True, blank=True)
    payment_no = models.CharField(max_length=20, unique=True, blank=True, default="")
    supplier = models.ForeignKey("inventory.Supplier", on_delete=models.PROTECT)
    date = models.DateField(default=timezone.now)
    currency = models.ForeignKey(Currency, on_delete=models.PROTECT)
    fx_rate = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1.0"))
    bank = models.ForeignKey(BankAccount, on_delete=models.PROTECT)
    amount = models.DecimalField(max_digits=18, decimal_places=6)
    method = models.CharField(max_length=50, default="Cash")
    notes = models.CharField(max_length=255, blank=True, null=True)

    def save(self, *args, **kwargs):
        if not self.payment_no:
            seq, _ = NumberSequence.objects.get_or_create(company=self.company, key="APPAY", defaults={"prefix": "PAY-"})
            self.payment_no = seq.next()
        super().save(*args, **kwargs)


class AuditLog(models.Model):
    model = models.CharField(max_length=100)
    object_id = models.IntegerField()
    action = models.CharField(max_length=30)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    at = models.DateTimeField(auto_now_add=True)
    diff = models.JSONField(default=dict)
