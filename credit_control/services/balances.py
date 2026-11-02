from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from accounting.models import APPayment, SupplierBill
from sales.models import Invoice, Payment
from sales.workspace import VALID_STATUSES, with_invoice_balances

ZERO=Decimal('0.00')


@dataclass
class OutstandingItem:
    obj_id: int
    number: str
    doc_date: date
    due_date: date | None
    total: Decimal
    paid: Decimal
    outstanding: Decimal
    days_overdue: int
    currency: str = ''


def _as_of(value=None):
    return value or timezone.localdate()


def _currency(value=None):
    return value or getattr(settings,'BASE_CURRENCY_CODE','USD')


def invoice_total(invoice):
    lines=list(invoice.lines.all())
    return sum((line.line_total for line in lines),ZERO) + (sum((line.tax_amount for line in lines),ZERO) if invoice.tax_added else ZERO)


def invoice_paid(invoice, as_of_date=None):
    day=_as_of(as_of_date)
    return sum((p.amount for p in invoice.payments.all() if p.date<=day),ZERO)


def open_invoice_queryset(as_of_date=None,currency=None):
    day=_as_of(as_of_date)
    qs=with_invoice_balances(as_of=day).filter(date__lte=day,status__in=VALID_STATUSES,workspace_currency=_currency(currency),workspace_balance__gt=0)
    # A later receipt can explain a currently-paid status in a historical report.
    later=Payment.objects.filter(invoice_id=OuterRef('pk'),date__gt=day,date__lte=timezone.localdate())
    return qs.annotate(later_receipt=Exists(later)).exclude(status=Invoice.PAID,later_receipt=False)


def invoice_outstanding_balance(invoice, as_of_date=None):
    if invoice.date>_as_of(as_of_date) or invoice.status not in VALID_STATUSES:
        return ZERO
    if invoice.status==Invoice.PAID and not invoice.payments.filter(date__gt=_as_of(as_of_date),date__lte=timezone.localdate()).exists():
        return ZERO
    return max(invoice_total(invoice)-invoice_paid(invoice,as_of_date),ZERO)


def _data(day,code):
    return {'as_of_date':day,'currency':code,'outstanding':ZERO,'overdue':ZERO,'oldest_doc_date':None,'items':[]}


def customer_balance_map(as_of_date=None,currency=None,customer_id=None):
    day,code=_as_of(as_of_date),_currency(currency)
    invoices=open_invoice_queryset(day,code).select_related('customer').order_by('date','pk')
    if customer_id is not None:
        invoices=invoices.filter(customer_id=customer_id)
    result={}
    for inv in invoices:
        data=result.setdefault(inv.customer_id,{**_data(day,code),'customer':inv.customer})
        days=max((day-inv.due_date).days,0) if inv.due_date else 0
        balance=inv.workspace_balance
        data['outstanding']+=balance
        if days:
            data['overdue']+=balance
        data['oldest_doc_date']=data['oldest_doc_date'] or inv.date
        data['items'].append(OutstandingItem(inv.pk,inv.number,inv.date,inv.due_date,inv.workspace_total,inv.workspace_paid,balance,days,code))
    return result


def get_customer_outstanding(customer,as_of_date=None,currency=None):
    return customer_balance_map(as_of_date,currency,customer.pk).get(customer.pk,_data(_as_of(as_of_date),_currency(currency)))


def customer_total_outstanding(customer,as_of_date=None,currency=None):
    return get_customer_outstanding(customer,as_of_date,currency)['outstanding']


def _supplier_rows(as_of_date=None,currency=None,supplier_id=None,company_id=...):
    day,code=_as_of(as_of_date),_currency(currency)
    bills=SupplierBill.objects.filter(date__lte=day,currency__code=code,status__in=[SupplierBill.POSTED,SupplierBill.PARTIAL,SupplierBill.PAID]).select_related('supplier','currency').order_by('date','pk')
    payments=APPayment.objects.filter(date__lte=day,currency__code=code).prefetch_related('allocations__bill').order_by('date','pk')
    if supplier_id is not None:
        bills=bills.filter(supplier_id=supplier_id);payments=payments.filter(supplier_id=supplier_id)
    if company_id is not ...:
        bills=bills.filter(company_id=company_id);payments=payments.filter(company_id=company_id)
    rows=[{'bill':b,'total':Decimal(b.total or ZERO).quantize(Decimal('.01')),'paid':ZERO} for b in bills]
    by_id={row['bill'].pk:row for row in rows}
    pools=defaultdict(lambda:ZERO)
    for payment in payments:
        allocated=ZERO
        for allocation in payment.allocations.all():
            allocated+=allocation.amount
            row=by_id.get(allocation.bill_id)
            if row and (row['bill'].supplier_id,row['bill'].company_id,row['bill'].currency_id)==(payment.supplier_id,payment.company_id,payment.currency_id):
                row['paid']+=allocation.amount
        pools[(payment.supplier_id,payment.company_id,payment.currency_id)]+=max(payment.amount-allocated,ZERO)
    for row in rows:
        bill=row['bill'];key=(bill.supplier_id,bill.company_id,bill.currency_id)
        row['paid']=min(row['paid'],row['total'])
        share=min(max(row['total']-row['paid'],ZERO),pools[key])
        row['paid']+=share;pools[key]-=share
        if bill.status==SupplierBill.PAID and (bill.status_changed_at is None or timezone.localtime(bill.status_changed_at).date()<=day):
            row['paid']=row['total']
    return rows


def _build_supplier_bill_rows(supplier,as_of_date=None,currency=None):
    return _supplier_rows(as_of_date,currency,supplier.pk)


def bill_paid_fifo(bill,as_of_date=None):
    rows=_supplier_rows(as_of_date,bill.currency.code,bill.supplier_id,bill.company_id)
    return next((row['paid'] for row in rows if row['bill'].pk==bill.pk),ZERO)


def bill_outstanding_balance(bill,as_of_date=None):
    if bill.date>_as_of(as_of_date) or bill.status==SupplierBill.DRAFT:
        return ZERO
    return max(Decimal(bill.total)-bill_paid_fifo(bill,as_of_date),ZERO)


def supplier_balance_map(as_of_date=None,currency=None,supplier_id=None,company_id=...):
    day,code=_as_of(as_of_date),_currency(currency)
    result={}
    for row in _supplier_rows(day,code,supplier_id,company_id):
        bill=row['bill'];balance=max(row['total']-row['paid'],ZERO)
        if balance<=0:
            continue
        data=result.setdefault(bill.supplier_id,{**_data(day,code),'supplier':bill.supplier})
        days=max((day-bill.due_date).days,0) if bill.due_date else 0
        data['outstanding']+=balance
        if days:
            data['overdue']+=balance
        data['oldest_doc_date']=data['oldest_doc_date'] or bill.date
        data['items'].append(OutstandingItem(bill.pk,bill.doc_no,bill.date,bill.due_date,row['total'],row['paid'],balance,days,code))
    return result


def get_supplier_outstanding(supplier,as_of_date=None,currency=None,company_id=...):
    return supplier_balance_map(as_of_date,currency,supplier.pk,company_id).get(supplier.pk,_data(_as_of(as_of_date),_currency(currency)))


def supplier_total_outstanding(supplier,as_of_date=None,currency=None):
    return get_supplier_outstanding(supplier,as_of_date,currency)['outstanding']
