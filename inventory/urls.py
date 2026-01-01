from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views
from .api import ProductViewSet, ComboViewSet, ShipmentViewSet

app_name = 'inventory'


router = DefaultRouter()
router.register('api/products', ProductViewSet, basename='product-api')
router.register('api/combos', ComboViewSet, basename='combo')
router.register('api/shipments', ShipmentViewSet, basename='shipment')

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('new/', views.product_create, name='product_create'),
    path('<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('movement/new/', views.movement_create, name='movement_create'),
    path('shipments/', views.shipment_list, name='shipment_list'),
    path('shipments/new/', views.shipment_create, name='shipment_create'),
    path('shipments/<int:pk>/', views.shipment_detail, name='shipment_detail'),
    path('shipments/<int:pk>/receive/', views.shipment_receive, name='shipment_receive'),
    path('shipments/dashboard/', views.shipment_dashboard, name='shipment_dashboard'),
]

urlpatterns += router.urls
