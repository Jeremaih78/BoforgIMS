from __future__ import annotations

from datetime import date, timedelta
from django.shortcuts import render, redirect
from django.db.models import Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.utils.dateparse import parse_date

from sales.models import Invoice
from inventory.models import Product
from .models import JournalLine
from .models import Expense
from .forms import ExpenseForm
from .services.posting import post_expense
from django.core.paginator import Paginator


@login_required
def accounting_dashboard(request):
    today = timezone.now().date()
    start_month = today.replace(day=1)
    # Simple KPI placeholders: compute inventory value from avg_cost and qty
    # Compute inventory valuation as sum(qty * avg_cost)
    inv_value = 0
    for p in Product.objects.all():
        inv_value += (p.quantity or 0) * (p.avg_cost or 0)
    context = {
        'inv_value': inv_value,
    }
    return render(request, 'accounting/dashboard.html', context)


@login_required
def expense_list(request):
    q_from_raw = request.GET.get('from')
    q_to_raw = request.GET.get('to')

    def clean_date_param(val):
        if not val:
            return None
        if isinstance(val, str) and val.lower() == 'none':
            return None
        return parse_date(val) or None

    q_from = clean_date_param(q_from_raw)
    q_to = clean_date_param(q_to_raw)

    qs = Expense.objects.all().order_by('-date')
    if q_from:
        qs = qs.filter(date__gte=q_from)
    if q_to:
        qs = qs.filter(date__lte=q_to)
    paginator = Paginator(qs, 25)
    page = request.GET.get('page')
    expenses = paginator.get_page(page)
    return render(request, 'accounting/expense_list.html', {'expenses': expenses, 'q_from': q_from, 'q_to': q_to})


@login_required
def expense_create(request):
    form = ExpenseForm(request.POST or None, request.FILES or None)
    if request.method == 'POST' and form.is_valid():
        exp = form.save()
        try:
            post_expense(exp.id)
            exp.posted = True
            exp.save(update_fields=['posted'])
        except Exception:
            pass
        return redirect('expense_list')
    return render(request, 'accounting/expense_form.html', {'form': form})


@login_required
def inventory_valuation(request):
    products = Product.objects.all().order_by('name')
    rows = []
    grand = 0
    for p in products:
        qty = p.quantity or 0
        cost = p.avg_cost or 0
        total = qty * cost
        rows.append({'product': p, 'qty': qty, 'avg_cost': cost, 'total': total})
        grand += total
    return render(request, 'accounting/inventory_valuation.html', {'rows': rows, 'grand': grand})


@login_required
def inventory_valuation_pdf(request):
    from sales.pdf_utils import render_pdf_from_html
    products = Product.objects.all().order_by('name')
    rows = []
    grand = 0
    for p in products:
        qty = p.quantity or 0
        cost = p.avg_cost or 0
        total = qty * cost
        rows.append({'product': p, 'qty': qty, 'avg_cost': cost, 'total': total})
        grand += total
    html = render(request, 'accounting/pdf_inventory_valuation.html', {'rows': rows, 'grand': grand}).content.decode('utf-8')
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    from django.http import HttpResponse
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="inventory_valuation.pdf"'
    return resp


@login_required
def sales_summary(request):
    period = request.GET.get('period', 'monthly')
    qs = Invoice.objects.all()
    if period == 'daily':
        qs = qs.annotate(p=TruncDay('date'))
    elif period == 'weekly':
        qs = qs.annotate(p=TruncWeek('date'))
    elif period == 'yearly':
        qs = qs.annotate(p=TruncYear('date'))
    else:
        qs = qs.annotate(p=TruncMonth('date'))
    data = qs.values('p').annotate(total=Sum('items__unit_price') + Sum('items__quantity')*0).order_by('p')
    # Above is a lightweight sum; for accuracy, sum invoice totals in Python
    sums = {}
    for inv in Invoice.objects.order_by('date'):
        if period == 'daily':
            key = inv.date
        elif period == 'weekly':
            key = inv.date - timedelta(days=inv.date.weekday())
        elif period == 'yearly':
            key = inv.date.replace(month=1, day=1)
        else:
            key = inv.date.replace(day=1)
        sums.setdefault(key, 0)
        sums[key] += inv.total
    rows = sorted([{'period': k, 'total': v} for k, v in sums.items()], key=lambda x: x['period'])
    return render(request, 'accounting/sales_summary.html', {'rows': rows, 'period': period})


@login_required
def sales_summary_pdf(request):
    from sales.pdf_utils import render_pdf_from_html
    period = request.GET.get('period', 'monthly')
    # Build rows same as HTML
    sums = {}
    for inv in Invoice.objects.order_by('date'):
        if period == 'daily':
            key = inv.date
        elif period == 'weekly':
            key = inv.date - timedelta(days=inv.date.weekday())
        elif period == 'yearly':
            key = inv.date.replace(month=1, day=1)
        else:
            key = inv.date.replace(day=1)
        sums.setdefault(key, 0)
        sums[key] += inv.total
    rows = sorted([{'period': k, 'total': v} for k, v in sums.items()], key=lambda x: x['period'])
    html = render(request, 'accounting/pdf_sales_summary.html', {'rows': rows, 'period': period}).content.decode('utf-8')
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    from django.http import HttpResponse
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="sales_summary_{period}.pdf"'
    return resp


@login_required
def expenses_report(request):
    # simple by-category totals within date range
    q_from_raw = request.GET.get('from')
    q_to_raw = request.GET.get('to')
    def clean_date_param(val):
        if not val:
            return None
        if isinstance(val, str) and val.lower() == 'none':
            return None
        return parse_date(val) or None
    q_from = clean_date_param(q_from_raw)
    q_to = clean_date_param(q_to_raw)
    qs = Expense.objects.all()
    if q_from:
        qs = qs.filter(date__gte=q_from)
    if q_to:
        qs = qs.filter(date__lte=q_to)
    rows = qs.values('category__name').annotate(total=Sum('amount')).order_by('category__name')
    grand = qs.aggregate(s=Sum('amount'))['s'] or 0
    return render(request, 'accounting/expenses_report.html', {'rows': rows, 'grand': grand, 'q_from': q_from, 'q_to': q_to})


@login_required
def expenses_report_pdf(request):
    from sales.pdf_utils import render_pdf_from_html
    q_from_raw = request.GET.get('from')
    q_to_raw = request.GET.get('to')
    def clean_date_param(val):
        if not val:
            return None
        if isinstance(val, str) and val.lower() == 'none':
            return None
        return parse_date(val) or None
    q_from = clean_date_param(q_from_raw)
    q_to = clean_date_param(q_to_raw)
    qs = Expense.objects.all()
    if q_from:
        qs = qs.filter(date__gte=q_from)
    if q_to:
        qs = qs.filter(date__lte=q_to)
    rows = qs.values('category__name').annotate(total=Sum('amount')).order_by('category__name')
    grand = qs.aggregate(s=Sum('amount'))['s'] or 0
    html = render(request, 'accounting/pdf_expenses_report.html', {'rows': rows, 'grand': grand, 'q_from': q_from, 'q_to': q_to}).content.decode('utf-8')
    pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
    from django.http import HttpResponse
    resp = HttpResponse(pdf, content_type='application/pdf')
    resp['Content-Disposition'] = 'attachment; filename="expenses_report.pdf"'
    return resp
