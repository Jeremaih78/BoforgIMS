from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Category,
    Supplier,
    Product,
    StockMovement,
    Combo,
    ComboItem,
    Shipment,
    ShipmentCost,
    ShipmentItem,
    ShipmentEventLog,
    ProductUnit,
)


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    search_fields = ['name', 'email', 'phone']


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name', 'sku', 'price', 'quantity', 'thumbnail', 'is_active')
    search_fields = ('name', 'sku')
    list_filter = ('category', 'supplier', 'is_active')
    prepopulated_fields = {'slug': ('name',)}
    readonly_fields = ('image_preview', 'created_at', 'updated_at')
    fields = (
        ('name', 'sku', 'slug'),
        ('category', 'supplier'),
        ('price', 'currency', 'avg_cost'),
        ('quantity', 'reserved', 'track_inventory', 'tracking_mode'),
        ('reorder_level', 'tax_rate'),
        'description',
        'image',
        'image_preview',
        'is_active',
        ('created_at', 'updated_at'),
    )

    def thumbnail(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="height:40px;" />', obj.image.url)
        if obj.image_url:
            return format_html('<img src="{}" style="height:40px;" />', obj.image_url)
        return '-'

    thumbnail.short_description = 'Image'

    def image_preview(self, obj):
        if obj.image:
            return format_html('<img src="{}" style="max-height:200px;" />', obj.image.url)
        if obj.image_url:
            return format_html('<img src="{}" style="max-height:200px;" />', obj.image_url)
        return 'No image uploaded'


class ComboItemInline(admin.TabularInline):
    model = ComboItem
    extra = 1
    autocomplete_fields = ['product']


@admin.register(Combo)
class ComboAdmin(admin.ModelAdmin):
    list_display = ('name', 'code', 'is_active', '_price')
    list_filter = ('is_active',)
    search_fields = ('name', 'code')
    inlines = [ComboItemInline]

    def _price(self, obj):
        return obj.compute_price()

    _price.short_description = 'Computed Price'



@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product', 'movement_type', 'quantity', 'timestamp', 'user')
    list_filter = ('movement_type',)


class ShipmentItemInline(admin.TabularInline):
    model = ShipmentItem
    extra = 0
    autocomplete_fields = ['product']
    readonly_fields = ('quantity_received', 'landed_unit_cost', 'landed_total_cost', 'last_received_at')


class ShipmentCostInline(admin.TabularInline):
    model = ShipmentCost
    extra = 0


@admin.register(Shipment)
class ShipmentAdmin(admin.ModelAdmin):
    list_display = (
        'shipment_code',
        'supplier',
        'status',
        'shipping_method',
        'eta_date',
        'arrival_date',
        'total_cost_base',
    )
    list_filter = ('status', 'shipping_method', 'incoterm')
    search_fields = ('shipment_code', 'supplier__name')
    inlines = [ShipmentItemInline, ShipmentCostInline]
    readonly_fields = (
        'shipment_code',
        'base_currency',
        'created_at',
        'updated_at',
        'received_at',
        'closed_at',
        'landed_cost_allocated_at',
        'total_cost_display',
    )
    fieldsets = (
        ('Shipment', {
            'fields': (
                'shipment_code',
                'supplier',
                ('origin_country', 'destination_country'),
                ('incoterm', 'shipping_method'),
                ('eta_date', 'arrival_date'),
                'status',
                'allocation_basis',
            )
        }),
        ('Financials', {
            'fields': (
                'base_currency',
                'total_cost_display',
                'landed_cost_allocated_at',
            )
        }),
        ('Audit', {
            'fields': (
                ('created_by', 'created_at'),
                ('received_by', 'received_at'),
                ('closed_by', 'closed_at'),
            )
        }),
    )

    def total_cost_display(self, obj):
        return obj.total_cost_base

    total_cost_display.short_description = 'Total Cost (Base)'


@admin.register(ProductUnit)
class ProductUnitAdmin(admin.ModelAdmin):
    list_display = ('serial_number', 'product', 'shipment', 'status', 'landed_cost', 'sale_line')
    search_fields = ('serial_number', 'product__name', 'product__sku', 'shipment__shipment_code')
    list_filter = ('status', 'product__category')
    autocomplete_fields = ['product', 'shipment']
    readonly_fields = ('created_at', 'updated_at', 'sold_at', 'fault_reported_at')


@admin.register(ShipmentEventLog)
class ShipmentEventLogAdmin(admin.ModelAdmin):
    list_display = ('shipment', 'event_type', 'previous_status', 'new_status', 'actor', 'created_at')
    list_filter = ('event_type', 'new_status')
    search_fields = ('shipment__shipment_code', 'actor__username')
