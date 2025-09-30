from django.contrib import admin

from .models import (
    Quotation,
    Invoice,
    DocumentLine,
    Payment,
    PriceRule,
    StockReservation,
)


class DocumentLineInline(admin.TabularInline):
    model = DocumentLine
    extra = 1
    fields = ('product', 'description', 'quantity', 'unit_price', 'tax_rate_percent', 'line_total')
    readonly_fields = ('line_total',)


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    inlines = [DocumentLineInline]
    list_display = ('number', 'customer', 'date', 'status', 'total')
    search_fields = ('number', 'customer__name')


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    inlines = [DocumentLineInline]
    list_display = ('number', 'customer', 'date', 'due_date', 'status', 'total')
    search_fields = ('number', 'customer__name')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'date', 'method')
    search_fields = ('invoice__number',)


@admin.register(PriceRule)
class PriceRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'scope', 'value_type', 'value', 'is_active', 'start_at', 'end_at')
    list_filter = ('scope', 'value_type', 'is_active')


@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'product', 'quantity', 'created_at')
