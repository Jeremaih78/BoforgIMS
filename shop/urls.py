from django.urls import path

from . import views

app_name = 'shop'

urlpatterns = [
    path('', views.catalog, name='catalog'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),
    path('cart/', views.cart_detail, name='cart_detail'),
    path('cart/add/', views.cart_add, name='cart_add'),
    path('cart/remove/', views.cart_remove, name='cart_remove'),
    path('checkout/', views.checkout, name='checkout'),
    path('checkout/complete/', views.checkout_complete, name='checkout_complete'),
    path('paynow/initiate/', views.paynow_initiate, name='paynow_initiate'),
    path('paynow/return/', views.paynow_return, name='paynow_return'),
    path('paynow/result/', views.paynow_result, name='paynow_result'),
    path('healthz/', views.healthcheck, name='healthcheck'),
]
