from django.urls import path
from . import views, scan_views
from .api import InvoiceLineSerialAPIView

app_name = 'sales'

urlpatterns = [
    path('<str:kind>/<int:pk>/scan/', scan_views.scan, name='document_scan'),
    path('', views.sales_home, name='sales_home'),
    path('api/products/search/', views.product_search, name='product_search'),
    path('quotation/new/', views.quotation_create, name='quotation_create'),
    path('quotation/<int:pk>/', views.quotation_edit, name='quotation_edit'),
    path('quotation/<int:pk>/pdf/', views.quotation_pdf, name='quotation_pdf'),
    path('quotation/<int:pk>/to-invoice/', views.quotation_to_invoice, name='quotation_to_invoice'),
    path('quotation/<int:pk>/line/<int:line_id>/delete/', views.quotation_line_delete, name='quotation_line_delete'),
    path('invoice/new/', views.invoice_create, name='invoice_create'),
    path('invoice/<int:pk>/', views.invoice_edit, name='invoice_edit'),
    path('invoice/<int:pk>/pdf/', views.invoice_pdf, name='invoice_pdf'),
    path('invoice/<int:pk>/line/<int:line_id>/delete/', views.invoice_line_delete, name='invoice_line_delete'),
    path('invoice/line/<int:line_id>/serials/', views.invoice_line_serials, name='invoice_line_serials'),
    path('api/invoice-lines/<int:line_id>/serials/', InvoiceLineSerialAPIView.as_view(), name='invoice_line_serials_api'),
]
