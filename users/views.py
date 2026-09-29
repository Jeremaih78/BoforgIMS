from django.shortcuts import render
from core.permissions import ims_permission
from django.contrib.auth.decorators import login_required
from inventory.models import Product
from sales.models import Invoice
from django.utils import timezone
from django.db.models import Sum

try:
    from credit_control.services.workflows import collections_dashboard_data
except Exception:  # pragma: no cover
    collections_dashboard_data = None

@login_required
@ims_permission('sales.view_invoice', staff=True)
def dashboard(request):
    # Sales MTD for quick accounting glance
    today = timezone.localdate()
    month_start = today.replace(day=1)
    sales_mtd = Invoice.objects.filter(date__gte=month_start, date__lte=today).aggregate(s=Sum('lines__line_total'))['s'] or 0
    stats = {
        'products': Product.objects.count(),
        'low_stock': Product.objects.filter(quantity__lte=5).count(),
        'pending_invoices': Invoice.objects.filter(status=Invoice.PENDING).count(),
        'overdue_invoices': Invoice.objects.filter(status=Invoice.OVERDUE).count(),
        'sales_mtd': sales_mtd,
        'overdue_receivables': 0,
        'overdue_payables': 0,
        'cash_pressure': 0,
    }
    if collections_dashboard_data:
        cc = collections_dashboard_data()
        stats['overdue_receivables'] = cc['overdue_receivables']
        stats['overdue_payables'] = cc['overdue_payables']
        stats['cash_pressure'] = cc['net_cash_pressure']
    return render(request,'dashboard.html',{'stats':stats})
