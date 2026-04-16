from django.urls import path
from . import views

app_name = 'accounting'


urlpatterns = [
    path('', views.accounting_dashboard, name='accounting_dashboard'),
    path('finance/cashflow/', views.finance_cashflow, name='finance_cashflow'),
    path('finance/revenue/', views.finance_revenue, name='finance_revenue'),
    path('finance/expenses/', views.finance_expenses, name='finance_expenses'),
    path('finance/profitability/', views.finance_profitability, name='finance_profitability'),
    path('finance/export/<str:report_name>/<str:fmt>/', views.finance_export, name='finance_export'),
    # Expenses
    path('expenses/', views.expense_list, name='expense_list'),
    path('expenses/new/', views.expense_create, name='expense_create'),
    path('reports/inventory-valuation/', views.inventory_valuation, name='inventory_valuation'),
    path('reports/inventory-valuation/pdf/', views.inventory_valuation_pdf, name='inventory_valuation_pdf'),
    path('reports/sales-summary/', views.sales_summary, name='sales_summary'),
    path('reports/sales-summary/pdf/', views.sales_summary_pdf, name='sales_summary_pdf'),
    path('reports/expenses/', views.expenses_report, name='expenses_report'),
    path('reports/expenses/pdf/', views.expenses_report_pdf, name='expenses_report_pdf'),
]
