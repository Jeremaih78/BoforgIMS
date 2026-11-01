"""Read-only invoice balances shared by the dashboard and its drill-downs."""
from decimal import Decimal

from django.conf import settings
from django.db.models import Case, CharField, Count, DecimalField, F, Min, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Coalesce, Greatest

from .models import DocumentLine, Invoice, Payment

VALID_STATUSES = [Invoice.PENDING, Invoice.CONFIRMED, Invoice.PAID, Invoice.OVERDUE, 'SENT', 'UNPAID', 'PARTIALLY_PAID']
ZERO = Decimal('0.00')


def with_invoice_balances(queryset=None):
    """Separate subqueries avoid multiplying totals when there are several payments."""
    money = DecimalField(max_digits=20, decimal_places=2)
    lines = DocumentLine.objects.filter(invoice_id=OuterRef('pk')).order_by().values('invoice_id')
    payments = Payment.objects.filter(invoice_id=OuterRef('pk')).order_by().values('invoice_id')
    base_currency = getattr(settings, 'BASE_CURRENCY_CODE', 'USD')
    qs = Invoice.objects.all() if queryset is None else queryset
    qs = qs.annotate(
        workspace_total=Coalesce(Subquery(lines.annotate(value=Sum('line_total')).values('value')), Value(ZERO), output_field=money),
        workspace_paid=Coalesce(Subquery(payments.annotate(value=Sum('amount')).values('value')), Value(ZERO), output_field=money),
        workspace_currency_count=Coalesce(Subquery(lines.annotate(value=Count('product__currency', distinct=True)).values('value')), Value(0)),
        workspace_single_currency=Coalesce(Subquery(lines.annotate(value=Min('product__currency')).values('value')), Value(base_currency), output_field=CharField()),
    )
    return qs.annotate(
        workspace_balance=Greatest(F('workspace_total') - F('workspace_paid'), Value(ZERO), output_field=money),
        workspace_currency=Case(When(workspace_currency_count__gt=1, then=Value('MIXED')), default=F('workspace_single_currency'), output_field=CharField()),
    )


def unpaid_invoices(queryset):
    # Historical PAID markers remain authoritative even when older payments were not imported.
    return queryset.filter(status__in=VALID_STATUSES, workspace_balance__gt=0).exclude(status=Invoice.PAID)


def overdue_invoices(queryset, today):
    return unpaid_invoices(queryset).filter(Q(due_date__lt=today) | Q(status=Invoice.OVERDUE))
