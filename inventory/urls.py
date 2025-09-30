from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views
from .api import ProductViewSet, ComboViewSet

app_name = 'inventory'


router = DefaultRouter()
router.register('api/products', ProductViewSet, basename='product-api')
router.register('api/combos', ComboViewSet, basename='combo')

urlpatterns = [
    path('', views.product_list, name='product_list'),
    path('new/', views.product_create, name='product_create'),
    path('<int:pk>/edit/', views.product_edit, name='product_edit'),
    path('<int:pk>/delete/', views.product_delete, name='product_delete'),
    path('movement/new/', views.movement_create, name='movement_create'),
]

urlpatterns += router.urls
