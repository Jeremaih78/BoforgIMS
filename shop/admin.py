from django.contrib import admin

from .models import Cart, CartItem, Order, OrderItem, Payment


class CartItemInline(admin.TabularInline):
    model = CartItem
    extra = 0
    readonly_fields = ('product', 'quantity', 'added_at', 'updated_at')


@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ('session_key', 'item_count', 'created_at', 'updated_at')
    search_fields = ('session_key',)
    inlines = [CartItemInline]


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product', 'product_name', 'unit_price', 'quantity', 'line_total')


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('number', 'email', 'status', 'total', 'currency', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('number', 'email')
    inlines = [OrderItemInline]
    readonly_fields = ('created_at', 'updated_at', 'total')


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ('order', 'provider', 'status', 'provider_ref', 'created_at')
    list_filter = ('provider', 'status')
    search_fields = ('order__number', 'provider_ref')


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('cart', 'product', 'quantity', 'added_at')
    search_fields = ('cart__session_key', 'product__name', 'product__sku')
    list_filter = ('added_at',)


@admin.register(OrderItem)
class OrderItemAdmin(admin.ModelAdmin):
    list_display = ('order', 'product_name', 'quantity', 'unit_price', 'line_total')
    search_fields = ('order__number', 'product_name', 'order__email')

