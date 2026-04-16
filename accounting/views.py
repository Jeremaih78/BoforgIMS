from __future__ import annotations

import csv
import json
from datetime import date, datetime, timedelta
from django.shortcuts import render, redirect
from django.db.models import Sum
from django.db.models.functions import TruncDay, TruncWeek, TruncMonth, TruncYear
from django.contrib.auth.decorators import login_required, permission_required
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.http import HttpResponse

from sales.models import Invoice
from customers.models import Customer
from inventory.models import Category, Product, Supplier
from .models import JournalLine
from .models import Account, Expense, ExpenseCategory
from .forms import ExpenseForm
from .services.posting import post_expense
from .services.finance_dashboard import (
    Period,
    cashflow_trend,
    current_period,
    dashboard_payload,
    dashboard_summary,
    expense_trend,
    expenses_by_account,
    expenses_by_category,
    expenses_by_payee,
    get_cash_in,
    get_cash_out,
    get_closing_cash_balance,
    get_gross_profit,
    get_net_cashflow,
    get_net_profit,
    get_opening_cash_balance,
    get_total_cogs,
    get_total_expenses,
    get_total_revenue,
    profitability_by_product,
    revenue_by_customer,
    revenue_by_product,
    revenue_trend,
    top_expenses,
)
from django.core.paginator import Paginator


@login_required
def accounting_dashboard(request):
    period, group_by, filters = _finance_controls(request)
    today = current_period('today')
    week = current_period('this_week')
    month = current_period('this_month')
    year = current_period('this_year')
    payload = dashboard_payload(period, group_by, filters)
    context = {
        **_finance_filter_context(period, group_by, filters, request),
        **payload,
        'today_summary': dashboard_summary(today, filters),
        'week_summary': dashboard_summary(week, filters),
        'month_summary': dashboard_summary(month, filters),
        'year_summary': dashboard_summary(year, filters),
        'chart_json': _chart_json(payload),
        'page_title': 'Financial Command Center',
    }
    return render(request, 'accounting/finance_dashboard.html', context)


def _finance_controls(request):
    preset = request.GET.get('period', 'this_month')
    q_from = _clean_date(request.GET.get('from'))
    q_to = _clean_date(request.GET.get('to'))
    period = current_period(preset, q_from, q_to)
    group_by = request.GET.get('group_by', 'month')
    if group_by not in {'day', 'week', 'month', 'year'}:
        group_by = 'month'
    filters = {
        'account': request.GET.get('account') or None,
        'expense_category': request.GET.get('expense_category') or None,
        'customer': request.GET.get('customer') or None,
        'supplier': request.GET.get('supplier') or None,
        'product': request.GET.get('product') or None,
        'category': request.GET.get('category') or None,
        'payee': request.GET.get('payee') or None,
    }
    return period, group_by, filters


def _clean_date(value):
    if not value or str(value).lower() == 'none':
        return None
    return parse_date(value)


def _finance_filter_context(period, group_by, filters, request):
    return {
        'period': period,
        'group_by': group_by,
        'filters': filters,
        'querystring': request.GET.urlencode(),
        'accounts': Account.objects.filter(is_active=True).order_by('code'),
        'expense_categories': ExpenseCategory.objects.order_by('name'),
        'customers': Customer.objects.order_by('name'),
        'suppliers': Supplier.objects.order_by('name'),
        'products': Product.objects.order_by('name'),
        'product_categories': Category.objects.order_by('name'),
        'period_presets': [
            ('today', 'Today'),
            ('yesterday', 'Yesterday'),
            ('this_week', 'This Week'),
            ('last_week', 'Last Week'),
            ('this_month', 'This Month'),
            ('last_month', 'Last Month'),
            ('this_year', 'This Year'),
            ('last_year', 'Last Year'),
        ],
        'group_options': [('day', 'Daily'), ('week', 'Weekly'), ('month', 'Monthly'), ('year', 'Yearly')],
    }


def _chart_json(payload):
    def labels(rows):
        return [str(row['period']) for row in rows]

    return json.dumps({
        'cashflow': {
            'labels': labels(payload['cashflow_trend']),
            'in': [float(row['cash_in']) for row in payload['cashflow_trend']],
            'out': [float(row['cash_out']) for row in payload['cashflow_trend']],
            'net': [float(row['net']) for row in payload['cashflow_trend']],
            'balance': [float(row['balance']) for row in payload['cashflow_trend']],
        },
        'revenue': {
            'labels': labels(payload['revenue_trend']),
            'values': [float(row['total']) for row in payload['revenue_trend']],
        },
        'expenses': {
            'labels': labels(payload['expense_trend']),
            'values': [float(row['total']) for row in payload['expense_trend']],
        },
        'expense_categories': {
            'labels': [row['category__name'] or 'Uncategorised' for row in payload['expenses_by_category']],
            'values': [float(row['total']) for row in payload['expenses_by_category']],
        },
    }, default=str)


@login_required
def finance_cashflow(request):
    period, group_by, filters = _finance_controls(request)
    rows = cashflow_trend(period, group_by, filters)
    context = {
        **_finance_filter_context(period, group_by, filters, request),
        'summary': {
            'opening_balance': get_opening_cash_balance(period.start, filters),
            'cash_in': get_cash_in(period, filters),
            'cash_out': get_cash_out(period, filters),
            'net_cashflow': get_net_cashflow(period, filters),
            'closing_balance': get_closing_cash_balance(period.end, filters),
        },
        'rows': rows,
        'chart_json': json.dumps({
            'labels': [str(row['period']) for row in rows],
            'in': [float(row['cash_in']) for row in rows],
            'out': [float(row['cash_out']) for row in rows],
            'net': [float(row['net']) for row in rows],
            'balance': [float(row['balance']) for row in rows],
        }),
        'page_title': 'Cashflow Radar',
    }
    return render(request, 'accounting/finance_cashflow.html', context)


@login_required
def finance_expenses(request):
    period, group_by, filters = _finance_controls(request)
    rows = top_expenses(period, filters)
    context = {
        **_finance_filter_context(period, group_by, filters, request),
        'summary': {'expenses': get_total_expenses(period, filters)},
        'trend': expense_trend(period, group_by, filters),
        'by_category': expenses_by_category(period, filters),
        'by_account': expenses_by_account(period, filters),
        'by_payee': expenses_by_payee(period, filters),
        'rows': rows,
        'chart_json': json.dumps({
            'trend_labels': [str(row['period']) for row in expense_trend(period, group_by, filters)],
            'trend_values': [float(row['total']) for row in expense_trend(period, group_by, filters)],
            'category_labels': [row['category__name'] or 'Uncategorised' for row in expenses_by_category(period, filters)[:8]],
            'category_values': [float(row['total']) for row in expenses_by_category(period, filters)[:8]],
        }, default=str),
        'page_title': 'Expense Analytics',
    }
    return render(request, 'accounting/finance_expenses.html', context)


@login_required
def finance_revenue(request):
    period, group_by, filters = _finance_controls(request)
    basis = request.GET.get('basis', 'accrual')
    if basis not in {'accrual', 'cash'}:
        basis = 'accrual'
    trend = revenue_trend(period, group_by, filters)
    context = {
        **_finance_filter_context(period, group_by, filters, request),
        'basis': basis,
        'summary': {'revenue': get_total_revenue(period, basis, filters), 'cash_received': get_cash_in(period, filters)},
        'trend': trend,
        'by_customer': revenue_by_customer(period, filters),
        'by_product': revenue_by_product(period, filters),
        'chart_json': json.dumps({
            'trend_labels': [str(row['period']) for row in trend],
            'trend_values': [float(row['total']) for row in trend],
            'customer_labels': [row['invoice__customer__name'] or 'Unknown' for row in revenue_by_customer(period, filters)],
            'customer_values': [float(row['total']) for row in revenue_by_customer(period, filters)],
        }, default=str),
        'page_title': 'Revenue Analytics',
    }
    return render(request, 'accounting/finance_revenue.html', context)


@login_required
def finance_profitability(request):
    period, group_by, filters = _finance_controls(request)
    revenue = get_total_revenue(period, 'accrual', filters)
    cogs = get_total_cogs(period, filters)
    gross_profit = get_gross_profit(period, filters)
    expenses = get_total_expenses(period, filters)
    net_profit = get_net_profit(period, filters)
    product_rows = profitability_by_product(period, filters)
    context = {
        **_finance_filter_context(period, group_by, filters, request),
        'summary': {
            'revenue': revenue,
            'cogs': cogs,
            'gross_profit': gross_profit,
            'expenses': expenses,
            'net_profit': net_profit,
        },
        'product_rows': product_rows,
        'chart_json': json.dumps({
            'labels': ['Revenue', 'COGS', 'Operating Expenses', 'Net Profit'],
            'values': [float(revenue), float(cogs), float(expenses), float(net_profit)],
        }),
        'page_title': 'Profit Intelligence',
    }
    return render(request, 'accounting/finance_profitability.html', context)


@login_required
def finance_export(request, report_name, fmt):
    period, group_by, filters = _finance_controls(request)
    rows, headers = _report_rows(report_name, period, group_by, filters)
    if fmt == 'pdf':
        from sales.pdf_utils import render_pdf_from_html
        html = render(request, 'accounting/finance_report_pdf.html', {'rows': rows, 'headers': headers, 'report_name': report_name, 'period': period}).content.decode('utf-8')
        pdf = render_pdf_from_html(html, base_url=request.build_absolute_uri())
        response = HttpResponse(pdf, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{report_name}.pdf"'
        return response
    if fmt == 'xlsx':
        response = HttpResponse(content_type='application/vnd.ms-excel')
        response['Content-Disposition'] = f'attachment; filename="{report_name}.xls"'
        writer = csv.writer(response, delimiter='\t')
    else:
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = f'attachment; filename="{report_name}.csv"'
        writer = csv.writer(response)
    writer.writerow(headers)
    for row in rows:
        writer.writerow(row)
    return response


def _report_rows(report_name, period, group_by, filters):
    if report_name == 'cashflow':
        rows = cashflow_trend(period, group_by, filters)
        return [[r['period'], r['cash_in'], r['cash_out'], r['net'], r['balance']] for r in rows], ['Period', 'Cash In', 'Cash Out', 'Net', 'Balance']
    if report_name == 'expenses':
        rows = expenses_by_category(period, filters)
        return [[r['category__name'], r['total']] for r in rows], ['Category', 'Total']
    if report_name == 'revenue':
        rows = revenue_by_customer(period, filters)
        return [[r['invoice__customer__name'], r['total']] for r in rows], ['Customer', 'Revenue']
    if report_name == 'profitability':
        rows = profitability_by_product(period, filters)
        return [[r['product__name'], r['revenue'], r['cogs'], r['gross_profit'], r['gross_margin']] for r in rows], ['Product', 'Revenue', 'COGS', 'Gross Profit', 'Gross Margin %']
    summary = dashboard_summary(period, filters)
    return [[key, value] for key, value in summary.items() if key != 'period'], ['Metric', 'Value']


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
        return redirect('ims:accounting:expense_list')
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
    data = qs.values('p').annotate(total=Sum('lines__line_total')).order_by('p')
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
