from django.contrib import admin
from django.utils.html import format_html

from .models import Category, Supplier, Product, StockMovement, Combo, ComboItem


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
        ('quantity', 'reserved', 'track_inventory'),
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
