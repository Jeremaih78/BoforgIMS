from django.urls import path

from . import views
from django.contrib.sitemaps.views import index, sitemap
from .seo import ProductSitemap, CategorySitemap
sitemaps = {'products': ProductSitemap, 'categories': CategorySitemap}

app_name = 'shop'

urlpatterns = [
    path('sitemap.xml', index, {'sitemaps': sitemaps, 'sitemap_url_name': 'shop:sitemap_section'}, name='sitemap'),
    path('sitemap-<section>.xml', sitemap, {'sitemaps': sitemaps}, name='sitemap_section'),
    path('robots.txt', views.robots, name='robots'),
    path('', views.catalog, name='catalog'),
    path('products/<slug:slug>/', views.product_detail, name='product_detail'),
    path('packages/add/', views.package_add, name='package_add'),
    path('packages/remove/', views.package_remove, name='package_remove'),
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
