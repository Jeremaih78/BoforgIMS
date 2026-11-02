from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db.models import Avg, Count, F, Q, Max
from django.utils import timezone

from accounting.models import APPayment, SupplierBill
from customers.models import Customer
from inventory.models import Supplier
from sales.models import Invoice, Payment

from credit_control.models import PaymentDispute, PromiseToPay
from .balances import get_customer_outstanding, get_supplier_outstanding, customer_balance_map, supplier_balance_map


ZERO = Decimal("0.00")
BUCKETS = [
    ("current", -999999, 0),
    ("1_30", 1, 30),
    ("31_60", 31, 60),
    ("61_90", 61, 90),
    ("91_120", 91, 120),
    ("120_plus", 121, 999999),
]


def _as_of(as_of_date: date | None = None) -> date:
    return as_of_date or timezone.localdate()


def _bucket_for(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    for key, lower, upper in BUCKETS:
        if lower <= days_overdue <= upper:
            return key
    return "120_plus"


def _empty_bucket_map() -> dict:
    return {key: ZERO for key, *_ in BUCKETS}


def _avg_days_to_pay_customer(customer: Customer) -> float:
    rows = Payment.objects.filter(invoice__customer=customer).exclude(invoice__due_date__isnull=True).annotate(
        days_delta=F("date") - F("invoice__due_date")
    )
    if not rows.exists():
        return 0.0
    total = 0.0
    count = 0
    for row in rows:
        delta = row.days_delta
        total += float(delta.days if delta else 0)
        count += 1
    return round(total / count, 2) if count else 0.0


def _avg_days_to_pay_supplier(supplier: Supplier) -> float:
    # Supplier payments are not linked per bill in current schema.
    # Approximation: average difference between payment date and previous due bill date.
    payments = APPayment.objects.filter(supplier=supplier).order_by("date")
    bills = list(SupplierBill.objects.filter(supplier=supplier, due_date__isnull=False).order_by("due_date"))
    if not payments.exists() or not bills:
        return 0.0
    total = 0.0
    count = 0
    for payment in payments:
        matched = None
        for bill in bills:
            if bill.due_date <= payment.date:
                matched = bill
            else:
                break
        if not matched:
            continue
        total += float((payment.date - matched.due_date).days)
        count += 1
    return round(total / count, 2) if count else 0.0


def build_debtor_aging(as_of_date: date | None = None, currency=None) -> list[dict]:
    as_of = _as_of(as_of_date)
    rows = []
    balances=customer_balance_map(as_of,currency)
    ids=list(balances)
    last=dict(Payment.objects.filter(invoice__customer_id__in=ids,date__lte=as_of).values('invoice__customer_id').annotate(last=Max('date')).values_list('invoice__customer_id','last'))
    broken=dict(PromiseToPay.objects.filter(customer_id__in=ids,status=PromiseToPay.Status.BROKEN,promised_date__lte=as_of).values('customer_id').annotate(n=Count('pk')).values_list('customer_id','n'))
    disputes=dict(PaymentDispute.objects.filter(customer_id__in=ids,status__in=['OPEN','UNDER_REVIEW'],opened_date__lte=as_of).values('customer_id').annotate(n=Count('pk')).values_list('customer_id','n'))
    pay_days={}
    for cid,paid,due in Payment.objects.filter(invoice__customer_id__in=ids,date__lte=as_of,invoice__due_date__isnull=False).values_list('invoice__customer_id','date','invoice__due_date'):
        total,count=pay_days.get(cid,(0,0));pay_days[cid]=(total+(paid-due).days,count+1)
    for data in sorted(balances.values(),key=lambda item:item['customer'].name.lower()):
        customer=data['customer']
        if data["outstanding"] <= ZERO:
            continue
        buckets = _empty_bucket_map()
        oldest_unpaid = None
        for item in data["items"]:
            bucket = _bucket_for(item.days_overdue)
            buckets[bucket] += item.outstanding
            if oldest_unpaid is None or item.doc_date < oldest_unpaid:
                oldest_unpaid = item.doc_date
        rows.append(
            {
                "customer": customer,
                "total_outstanding": data["outstanding"],
                "overdue_amount": data["overdue"],
                "buckets": buckets,
                "oldest_unpaid_invoice_date": oldest_unpaid,
                "last_payment_date": last.get(customer.pk),
                "average_days_to_pay": round(pay_days[customer.pk][0]/pay_days[customer.pk][1],2) if customer.pk in pay_days else 0,
                "broken_promises": broken.get(customer.pk,0),
                "disputes": disputes.get(customer.pk,0),
                "items": data['items'], 'currency':data['currency'],
            }
        )
    return rows


def build_creditor_aging(as_of_date: date | None = None, currency=None, company_id=...) -> list[dict]:
    as_of = _as_of(as_of_date)
    rows = []
    balances=supplier_balance_map(as_of,currency,company_id=company_id)
    ids=list(balances)
    last=dict(APPayment.objects.filter(supplier_id__in=ids,date__lte=as_of).values('supplier_id').annotate(last=Max('date')).values_list('supplier_id','last'))
    disputes=dict(PaymentDispute.objects.filter(supplier_id__in=ids,status__in=['OPEN','UNDER_REVIEW'],opened_date__lte=as_of).values('supplier_id').annotate(n=Count('pk')).values_list('supplier_id','n'))
    for data in sorted(balances.values(),key=lambda item:item['supplier'].name.lower()):
        supplier=data['supplier']
        if data["outstanding"] <= ZERO:
            continue
        buckets = _empty_bucket_map()
        oldest_unpaid = None
        for item in data["items"]:
            bucket = _bucket_for(item.days_overdue)
            buckets[bucket] += item.outstanding
            if oldest_unpaid is None or item.doc_date < oldest_unpaid:
                oldest_unpaid = item.doc_date
        rows.append(
            {
                "supplier": supplier,
                "total_outstanding": data["outstanding"],
                "overdue_amount": data["overdue"],
                "buckets": buckets,
                "oldest_unpaid_bill_date": oldest_unpaid,
                "last_payment_date": last.get(supplier.pk),
                "average_days_to_pay": 0,
                "broken_promises": 0,
                "disputes": disputes.get(supplier.pk,0),
                "items": data['items'], 'currency':data['currency'],
            }
        )
    return rows
