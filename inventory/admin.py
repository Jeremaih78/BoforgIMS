from django.contrib import admin
from .models import Category, Supplier, Product, StockMovement

@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    search_fields = ['name']

@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    search_fields = ['name','email','phone']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ('name','sku','category','supplier','price','quantity','is_active')
    search_fields = ('name','sku')
    list_filter = ('category','supplier','is_active')

@admin.register(StockMovement)
class StockMovementAdmin(admin.ModelAdmin):
    list_display = ('product','movement_type','quantity','timestamp','user')
    list_filter = ('movement_type',)
