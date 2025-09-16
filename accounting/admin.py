from django.contrib import admin
from .models import (
    Currency, ExchangeRate, TaxRate, FiscalPeriod, Account, NumberSequence,
    JournalEntry, JournalLine, BankAccount, ExpenseCategory, Expense,
    SupplierBill, SupplierBillLine, ARPayment, APPayment, AuditLog,
)


@admin.register(Currency)
class CurrencyAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "is_base")
    list_filter = ("is_base",)


@admin.register(ExchangeRate)
class ExchangeRateAdmin(admin.ModelAdmin):
    list_display = ("currency", "date", "rate")
    list_filter = ("currency",)


@admin.register(TaxRate)
class TaxRateAdmin(admin.ModelAdmin):
    list_display = ("name", "rate", "is_default")
    list_filter = ("is_default",)


@admin.register(FiscalPeriod)
class FiscalPeriodAdmin(admin.ModelAdmin):
    list_display = ("name", "start_date", "end_date", "is_closed")
    list_filter = ("is_closed",)


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ("code", "name", "type", "is_active", "parent")
    list_filter = ("type", "is_active")
    search_fields = ("code", "name")


@admin.register(NumberSequence)
class NumberSequenceAdmin(admin.ModelAdmin):
    list_display = ("key", "prefix", "next_number")


class JournalLineInline(admin.TabularInline):
    model = JournalLine
    extra = 0


@admin.register(JournalEntry)
class JournalEntryAdmin(admin.ModelAdmin):
    list_display = ("number", "date", "memo", "is_posted", "source", "source_id")
    list_filter = ("is_posted", "source")
    inlines = [JournalLineInline]


@admin.register(BankAccount)
class BankAccountAdmin(admin.ModelAdmin):
    list_display = ("name", "account", "currency")


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "default_account")


@admin.register(Expense)
class ExpenseAdmin(admin.ModelAdmin):
    list_display = ("doc_no", "date", "payee", "category", "amount", "posted")
    list_filter = ("posted", "category")


class SupplierBillLineInline(admin.TabularInline):
    model = SupplierBillLine
    extra = 0


@admin.register(SupplierBill)
class SupplierBillAdmin(admin.ModelAdmin):
    list_display = ("doc_no", "supplier", "date", "due_date", "total", "status")
    list_filter = ("status",)
    inlines = [SupplierBillLineInline]


@admin.register(ARPayment)
class ARPaymentAdmin(admin.ModelAdmin):
    list_display = ("receipt_no", "customer", "date", "amount", "bank")


@admin.register(APPayment)
class APPaymentAdmin(admin.ModelAdmin):
    list_display = ("payment_no", "supplier", "date", "amount", "bank")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("model", "object_id", "action", "user", "at")

