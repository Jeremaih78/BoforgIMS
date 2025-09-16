from django.contrib import admin
from .models import Quotation, QuotationItem, Invoice, InvoiceItem, Payment, PriceRule, StockReservation

class QuotationItemInline(admin.TabularInline):
    model = QuotationItem          # <-- required
    extra = 1
    fields = ('product', 'description', 'quantity', 'unit_price','discount_percent','discount_value','tax_rate')

class InvoiceItemInline(admin.TabularInline):
    model = InvoiceItem            # <-- required
    extra = 1
    fields = ('product', 'description', 'quantity', 'unit_price','discount_percent','discount_value','tax_rate')

@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):
    inlines = [QuotationItemInline]
    list_display = ('number', 'customer', 'date', 'status')
    search_fields = ('number', 'customer__name')

@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    inlines = [InvoiceItemInline]
    list_display = ('number', 'customer', 'date', 'due_date', 'status')
    search_fields = ('number', 'customer__name')

@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('invoice', 'amount', 'date', 'method')
    search_fields = ('invoice__number',)

@admin.register(PriceRule)
class PriceRuleAdmin(admin.ModelAdmin):
    list_display = ('name','scope','value_type','value','is_active','start_at','end_at')
    list_filter = ('scope','value_type','is_active')

@admin.register(StockReservation)
class StockReservationAdmin(admin.ModelAdmin):
    list_display = ('invoice','product','quantity','created_at')
