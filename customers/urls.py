from django.urls import path
from . import views

app_name = 'customers'

urlpatterns = [
    path('', views.customer_list, name='customer_list'),
    path('new/', views.customer_create, name='customer_create'),
    path('<int:pk>/edit/', views.customer_edit, name='customer_edit'),
]
