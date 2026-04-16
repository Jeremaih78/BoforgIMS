from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from django.db.models import Sum

from accounting.models import APPayment, SupplierBill
from sales.models import Invoice, Payment


ZERO = Decimal("0.00")


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


def _as_of(as_of_date: date | None) -> date:
    return as_of_date or date.today()


def invoice_total(invoice: Invoice) -> Decimal:
    total = invoice.lines.aggregate(total=Sum("line_total")).get("total") or ZERO
    return Decimal(total).quantize(Decimal("0.01"))


def invoice_paid(invoice: Invoice, as_of_date: date | None = None) -> Decimal:
    qs = Payment.objects.filter(invoice=invoice)
    if as_of_date:
        qs = qs.filter(date__lte=as_of_date)
    total = qs.aggregate(total=Sum("amount")).get("total") or ZERO
    return Decimal(total).quantize(Decimal("0.01"))


def invoice_outstanding_balance(invoice: Invoice, as_of_date: date | None = None) -> Decimal:
    outstanding = invoice_total(invoice) - invoice_paid(invoice, as_of_date=as_of_date)
    return max(outstanding, ZERO)


def customer_total_outstanding(customer, as_of_date: date | None = None) -> Decimal:
    total = ZERO
    invoices = Invoice.objects.filter(customer=customer).prefetch_related("lines", "payments")
    if as_of_date:
        invoices = invoices.filter(date__lte=as_of_date)
    for invoice in invoices:
        total += invoice_outstanding_balance(invoice, as_of_date=as_of_date)
    return total.quantize(Decimal("0.01"))


def _build_supplier_bill_rows(supplier, as_of_date: date | None = None) -> list[dict]:
    bills = SupplierBill.objects.filter(supplier=supplier).order_by("date", "id")
    if as_of_date:
        bills = bills.filter(date__lte=as_of_date)
    rows = []
    for bill in bills:
        total = Decimal(bill.total or ZERO).quantize(Decimal("0.01"))
        rows.append({"bill": bill, "total": total, "paid": ZERO})

    payments = APPayment.objects.filter(supplier=supplier).order_by("date", "id")
    if as_of_date:
        payments = payments.filter(date__lte=as_of_date)

    for payment in payments:
        amount_left = Decimal(payment.amount or ZERO)
        if amount_left <= ZERO:
            continue
        for row in rows:
            remaining = row["total"] - row["paid"]
            if remaining <= ZERO:
                continue
            allocation = min(remaining, amount_left)
            row["paid"] += allocation
            amount_left -= allocation
            if amount_left <= ZERO:
                break

    return rows


def bill_paid_fifo(bill: SupplierBill, as_of_date: date | None = None) -> Decimal:
    rows = _build_supplier_bill_rows(bill.supplier, as_of_date=as_of_date)
    for row in rows:
        if row["bill"].id == bill.id:
            return Decimal(row["paid"]).quantize(Decimal("0.01"))
    return ZERO


def bill_outstanding_balance(bill: SupplierBill, as_of_date: date | None = None) -> Decimal:
    total = Decimal(bill.total or ZERO).quantize(Decimal("0.01"))
    paid = bill_paid_fifo(bill, as_of_date=as_of_date)
    return max(total - paid, ZERO)


def supplier_total_outstanding(supplier, as_of_date: date | None = None) -> Decimal:
    total = ZERO
    rows = _build_supplier_bill_rows(supplier, as_of_date=as_of_date)
    for row in rows:
        total += max(row["total"] - row["paid"], ZERO)
    return total.quantize(Decimal("0.01"))


def get_customer_outstanding(customer, as_of_date: date | None = None) -> dict:
    as_of = _as_of(as_of_date)
    outstanding = customer_total_outstanding(customer, as_of_date=as_of)
    overdue = ZERO
    oldest = None
    items: list[OutstandingItem] = []

    invoices = Invoice.objects.filter(customer=customer).order_by("date", "id")
    for inv in invoices:
        balance = invoice_outstanding_balance(inv, as_of_date=as_of)
        if balance <= ZERO:
            continue
        days_overdue = 0
        if inv.due_date and inv.due_date < as_of:
            days_overdue = (as_of - inv.due_date).days
            overdue += balance
        if oldest is None or inv.date < oldest:
            oldest = inv.date
        items.append(
            OutstandingItem(
                obj_id=inv.id,
                number=inv.number,
                doc_date=inv.date,
                due_date=inv.due_date,
                total=invoice_total(inv),
                paid=invoice_paid(inv, as_of_date=as_of),
                outstanding=balance,
                days_overdue=days_overdue,
            )
        )

    return {
        "as_of_date": as_of,
        "outstanding": outstanding,
        "overdue": overdue.quantize(Decimal("0.01")),
        "oldest_doc_date": oldest,
        "items": items,
    }


def get_supplier_outstanding(supplier, as_of_date: date | None = None) -> dict:
    as_of = _as_of(as_of_date)
    rows = _build_supplier_bill_rows(supplier, as_of_date=as_of)
    outstanding = ZERO
    overdue = ZERO
    oldest = None
    items: list[OutstandingItem] = []

    for row in rows:
        bill = row["bill"]
        open_bal = max(row["total"] - row["paid"], ZERO)
        if open_bal <= ZERO:
            continue
        outstanding += open_bal
        days_overdue = 0
        if bill.due_date and bill.due_date < as_of:
            days_overdue = (as_of - bill.due_date).days
            overdue += open_bal
        if oldest is None or bill.date < oldest:
            oldest = bill.date
        items.append(
            OutstandingItem(
                obj_id=bill.id,
                number=bill.doc_no,
                doc_date=bill.date,
                due_date=bill.due_date,
                total=row["total"],
                paid=row["paid"],
                outstanding=open_bal,
                days_overdue=days_overdue,
            )
        )

    return {
        "as_of_date": as_of,
        "outstanding": outstanding.quantize(Decimal("0.01")),
        "overdue": overdue.quantize(Decimal("0.01")),
        "oldest_doc_date": oldest,
        "items": items,
    }
