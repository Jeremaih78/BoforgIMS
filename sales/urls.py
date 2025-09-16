from django.urls import path
from . import views
urlpatterns = [
    path('', views.sales_home, name='sales_home'),
    path('quotation/new/', views.quotation_create, name='quotation_create'),
    path('quotation/<int:pk>/', views.quotation_edit, name='quotation_edit'),
    path('quotation/<int:pk>/pdf/', views.quotation_pdf, name='quotation_pdf'),
    path('quotation/<int:pk>/to-invoice/', views.quotation_to_invoice, name='quotation_to_invoice'),
    path('invoice/new/', views.invoice_create, name='invoice_create'),
    path('invoice/<int:pk>/', views.invoice_edit, name='invoice_edit'),
    path('invoice/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
]
