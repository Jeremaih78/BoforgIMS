from django.shortcuts import render
from django.contrib.auth.decorators import login_required
from inventory.models import Product
from sales.models import Invoice
from django.utils import timezone
from django.db.models import Sum

@login_required
def dashboard(request):
    # Sales MTD for quick accounting glance
    today = timezone.now().date()
    month_start = today.replace(day=1)
    sales_mtd = Invoice.objects.filter(date__gte=month_start).aggregate(s=Sum('items__unit_price'))['s'] or 0
    stats = {
        'products': Product.objects.count(),
        'low_stock': Product.objects.filter(quantity__lte=5).count(),
        'pending_invoices': Invoice.objects.filter(status=Invoice.PENDING).count(),
        'overdue_invoices': Invoice.objects.filter(status=Invoice.OVERDUE).count(),
        'sales_mtd': sales_mtd,
    }
    return render(request,'dashboard.html',{'stats':stats})
