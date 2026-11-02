"""Actionable dashboard data; no synchronizations or writes on page load."""
from datetime import timedelta
import re
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Q, Sum
from django.urls import reverse
from django.utils import timezone

from core.permissions import allowed
from customers.models import Customer
from inventory.models import Product, Shipment
from inventory.selectors import low_stock_products, shipment_followups
from sales.models import Invoice, Payment, Quotation
from sales.workspace import VALID_STATUSES, ZERO, overdue_invoices, unpaid_invoices, with_invoice_balances


def dashboard_permissions(user):
    grants = {
        'products': ('inventory.view_product', True),
        'shipments': ('inventory.view_shipment', False),
        'customers': ('customers.view_customer', True),
        'quotes': ('sales.view_quotation', True),
        'payments': ('sales.view_payment', True),
        'new_invoice': ('sales.add_invoice', True),
        'new_quote': ('sales.add_quotation', True),
        'new_customer': ('customers.add_customer', True),
        'stock_movement': ('inventory.add_stockmovement', False),
        'scan': ('inventory.view_productunit', True),
        'accounting': ('accounting.view_journalentry', False),
        'collections': ('credit_control.view_debtoraccount', False),
    }
    return {key: allowed(user, permission, staff=staff) for key, (permission, staff) in grants.items()}


def sales_url(**params):
    return reverse('ims:sales:sales_home') + '?' + urlencode(params)


def dashboard_search(user, query, permissions=None):
    query = query.strip()[:100]
    if not query:
        return []
    grants = permissions or dashboard_permissions(user)
    results = []
    phone_pattern = None
    digits = re.sub(r'[^0-9]', '', query)
    if len(digits) >= 3 and re.fullmatch(r'[0-9\s+().-]+', query):
        phone_pattern = r'\D*'.join(digits)
    if grants['products']:
        products = Product.objects.filter(is_active=True).filter(Q(name__icontains=query) | Q(sku__icontains=query) | Q(barcodes__code__iexact=query)).distinct().order_by('name')[:4]
        results.extend({'kind': 'Product', 'label': p.name, 'detail': f'{p.sku} · {p.currency} {p.price:,.2f}', 'url': reverse('ims:inventory:product_identity', args=[p.pk])} for p in products)
    if grants['customers']:
        customer_match = Q(name__icontains=query) | Q(phone__icontains=query) | Q(email__icontains=query)
        if phone_pattern:
            customer_match |= Q(phone__iregex=phone_pattern)
        customers = Customer.objects.filter(customer_match).order_by('name')[:4]
        results.extend({'kind': 'Customer', 'label': c.name, 'detail': c.phone or c.email or '', 'url': reverse('ims:customers:customer_list') + '?' + urlencode({'q': c.phone or c.name})} for c in customers)
    invoice_match = Q(number__icontains=query) | Q(customer__name__icontains=query) | Q(customer__phone__icontains=query) | Q(reference__icontains=query)
    if phone_pattern:
        invoice_match |= Q(customer__phone__iregex=phone_pattern)
    invoices = Invoice.objects.filter(invoice_match).select_related('customer').order_by('-date', '-pk')[:4]
    results.extend({'kind': 'Invoice', 'label': inv.number, 'detail': inv.customer.name, 'url': reverse('ims:sales:invoice_edit', args=[inv.pk])} for inv in invoices)
    if grants['quotes']:
        quotes = Quotation.objects.filter(Q(number__icontains=query) | Q(customer__name__icontains=query) | Q(reference__icontains=query)).select_related('customer').order_by('-date', '-pk')[:4]
        results.extend({'kind': 'Quotation', 'label': q.number, 'detail': q.customer.name, 'url': reverse('ims:sales:quotation_edit', args=[q.pk])} for q in quotes)
    return results


def dashboard_context(user, params):
    today = timezone.localdate()
    grants = dashboard_permissions(user)
    period_key = params.get('period', 'month')
    periods = {'today': (today, 'Today'), 'week': (today - timedelta(days=6), 'Last 7 days'), 'month': (today.replace(day=1), 'This month')}
    if period_key not in periods:
        period_key = 'month'
    start, period_label = periods[period_key]
    base_currency = getattr(settings, 'BASE_CURRENCY_CODE', 'USD')
    currencies = sorted({base_currency, *Product.objects.exclude(currency='').values_list('currency', flat=True).distinct()})
    currency = params.get('currency', base_currency)
    if currency not in currencies:
        currency = base_currency
    all_invoices = with_invoice_balances().filter(status__in=VALID_STATUSES, date__lte=today)
    invoices = all_invoices.filter(workspace_currency=currency)
    period_invoices = invoices.filter(date__gte=start)
    open_invoices = unpaid_invoices(invoices)
    late_invoices = overdue_invoices(invoices, today)
    totals = period_invoices.aggregate(invoiced=Sum('workspace_subtotal'), count=Count('pk'))
    unpaid = open_invoices.aggregate(amount=Sum('workspace_balance'), count=Count('pk'))
    overdue = late_invoices.aggregate(amount=Sum('workspace_balance'), count=Count('pk'))
    due_today = open_invoices.filter(due_date=today).count()
    received = None
    if grants['payments']:
        received = Payment.objects.filter(invoice_id__in=invoices.values('pk'), date__range=(start, today)).aggregate(amount=Sum('amount'))['amount'] or ZERO
    low = low_stock_products() if grants['products'] else Product.objects.none()
    low_count = low.count()
    shipment_rows, shipment_count = [], 0
    if grants['shipments']:
        shipments = shipment_followups(today=today).select_related('supplier')
        shipment_count = shipments.count()
        shipment_rows = list(shipments.order_by('eta_date', 'pk')[:4])
        for shipment in shipment_rows:
            shipment.dashboard_late = shipment.status in [Shipment.STATUS_CREATED, Shipment.STATUS_IN_TRANSIT] and bool(shipment.eta_date and shipment.eta_date < today)
    drafts = Quotation.objects.none()
    draft_count = 0
    if grants['quotes']:
        drafts = Quotation.objects.filter(status=Quotation.DRAFT).annotate(converted=Exists(Invoice.objects.filter(quotation_id=OuterRef('pk')))).filter(converted=False)
        draft_count = drafts.count()
    recent = list(period_invoices.select_related('customer').order_by('-date', '-pk')[:6])
    for invoice in recent:
        invoice.dashboard_status = 'Paid' if invoice.status == Invoice.PAID or (invoice.workspace_total > 0 and invoice.workspace_balance == 0) else ('Overdue' if invoice.workspace_balance > 0 and (invoice.status == Invoice.OVERDUE or bool(invoice.due_date and invoice.due_date < today)) else ('Part paid' if invoice.workspace_paid > 0 else 'Unpaid'))
    return {
        'today': today, 'updated_at': timezone.localtime(), 'dashboard_permissions': grants,
        'period': period_key, 'period_label': period_label, 'period_start': start,
        'currency': currency, 'currencies': currencies,
        'invoiced_amount': totals['invoiced'] or ZERO, 'invoice_count': totals['count'], 'received_amount': received,
        'open_amount': unpaid['amount'] or ZERO, 'open_count': unpaid['count'],
        'overdue_amount': overdue['amount'] or ZERO, 'overdue_count': overdue['count'], 'due_today_count': due_today,
        'low_stock_count': low_count, 'low_products': list(low.order_by('available_for_reorder', 'name')[:5]) if grants['products'] else [],
        'stockout_count': low.filter(available_for_reorder=0).count() if grants['products'] else 0,
        'shipment_count': shipment_count, 'shipment_rows': shipment_rows,
        'draft_count': draft_count, 'draft_quotes': list(drafts.select_related('customer').order_by('-date', '-pk')[:4]),
        'recent_invoices': recent, 'overdue_rows': list(late_invoices.select_related('customer').order_by('due_date', 'pk')[:5]),
        'mixed_currency_count': all_invoices.filter(workspace_currency='MIXED').count(),
        'attention_count': overdue['count'] + low_count + shipment_count,
        'invoiced_url': sales_url(currency=currency, filter='issued', date_from=start.isoformat(), date_to=today.isoformat()),
        'received_url': sales_url(currency=currency, filter='received', date_from=start.isoformat(), date_to=today.isoformat()),
        'unpaid_url': sales_url(currency=currency, filter='unpaid'),
        'overdue_url': sales_url(currency=currency, filter='overdue'),
        'due_today_url': sales_url(currency=currency, filter='due_today'),
    }
