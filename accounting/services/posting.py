from __future__ import annotations

from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from django.conf import settings

from accounting.models import JournalEntry, JournalLine, Account, Currency, BankAccount
from sales.models import Invoice
from accounting.models import Expense, TaxRate


def _base_currency() -> Currency:
    code = getattr(settings, "BASE_CURRENCY_CODE", "USD")
    return Currency.objects.get_or_create(code=code, defaults={"name": code, "is_base": True})[0]


def _get_account(code: str) -> Account:
    return Account.objects.get(code=code)


def _default_bank() -> BankAccount:
    cur = _base_currency()
    bank_gl = _get_account("1100")
    return BankAccount.objects.get_or_create(name="Default Bank", account=bank_gl, currency=cur)[0]


def _split_invoice_amounts(inv: Invoice) -> tuple[Decimal, Decimal, Decimal]:
    """Return (net, tax, gross) for invoice."""
    gross = Decimal(inv.total)
    net = Decimal("0")
    tax = Decimal("0")
    for it in inv.items.all():
        # replicate item.total math to split tax
        price = it.unit_price
        if it.discount_percent:
            price = price * (1 - (it.discount_percent / 100))
        price = price - it.discount_value
        if price < 0:
            price = Decimal("0")
        line_net = it.quantity * price
        line_tax = Decimal("0")
        if it.tax_rate:
            line_tax = line_net * (it.tax_rate / 100)
        net += line_net
        tax += line_tax
    # guard floating diff
    if (net + tax).quantize(Decimal("0.01")) != gross.quantize(Decimal("0.01")):
        gross = (net + tax)
    return (net, tax, gross)


@transaction.atomic
def post_sales_invoice(invoice_id: int) -> JournalEntry:
    inv = Invoice.objects.select_related("customer").prefetch_related("items").get(pk=invoice_id)
    cur = _base_currency()
    net, tax, gross = _split_invoice_amounts(inv)

    # If already posted revenue JE for this invoice, skip
    existing = JournalEntry.objects.filter(source="INVOICE", source_id=inv.id, is_posted=True).first()
    if existing:
        return existing

    entry = JournalEntry.objects.create(
        date=inv.date,
        memo=f"Invoice {inv.number}",
        currency=cur,
        fx_rate=Decimal("1.0"),
        is_posted=True,
        posted_at=timezone.now(),
        source="INVOICE",
        source_id=inv.id,
    )
    ar = _get_account("1200")
    sales = _get_account("4000")
    vat_out = _get_account("2100")  # VAT payable

    # Dr A/R gross
    JournalLine.objects.create(entry=entry, account=ar, debit=gross, credit=Decimal("0"), debit_base=gross)
    # Cr Sales net
    if net:
        JournalLine.objects.create(entry=entry, account=sales, debit=Decimal("0"), credit=net, credit_base=net)
    # Cr VAT Output tax
    if tax:
        JournalLine.objects.create(entry=entry, account=vat_out, debit=Decimal("0"), credit=tax, credit_base=tax)

    entry.clean()
    entry.save()
    return entry


@transaction.atomic
def post_ar_receipt(invoice_id: int, amount: Decimal) -> JournalEntry:
    inv = Invoice.objects.select_related("customer").get(pk=invoice_id)
    cur = _base_currency()
    entry = JournalEntry.objects.create(
        date=timezone.now().date(),
        memo=f"Receipt for {inv.number}",
        currency=cur,
        fx_rate=Decimal("1.0"),
        is_posted=True,
        posted_at=timezone.now(),
        source="PAYMENT",
        source_id=inv.id,
    )
    bank = _default_bank().account
    ar = _get_account("1200")
    # Dr Bank, Cr A/R
    JournalLine.objects.create(entry=entry, account=bank, debit=amount, credit=Decimal("0"), debit_base=amount)
    JournalLine.objects.create(entry=entry, account=ar, debit=Decimal("0"), credit=amount, credit_base=amount)
    entry.clean(); entry.save()
    return entry


@transaction.atomic
def post_cogs_for_invoice(invoice_id: int) -> JournalEntry | None:
    """Post COGS at average cost. Uses product.price as proxy if avg_cost not maintained yet."""
    from inventory.models import Product
    inv = Invoice.objects.prefetch_related("items__product").get(pk=invoice_id)
    cur = _base_currency()
    total_cogs = Decimal("0")
    for it in inv.items.all():
        p = it.product
        if not p:
            continue
        # Placeholder average cost: if Product has avg_cost use it; else use price
        avg_cost = getattr(p, "avg_cost", None)
        if not avg_cost or avg_cost == 0:
            avg_cost = p.price
        total_cogs += (avg_cost * it.quantity)
    if total_cogs == 0:
        return None
    entry = JournalEntry.objects.create(
        date=inv.date,
        memo=f"COGS for {inv.number}",
        currency=cur,
        fx_rate=Decimal("1.0"),
        is_posted=True,
        posted_at=timezone.now(),
        source="INVOICE",
        source_id=inv.id,
    )
    cogs = _get_account("5000")
    inventory = _get_account("1300")
    JournalLine.objects.create(entry=entry, account=cogs, debit=total_cogs, credit=Decimal("0"), debit_base=total_cogs)
    JournalLine.objects.create(entry=entry, account=inventory, debit=Decimal("0"), credit=total_cogs, credit_base=total_cogs)
    entry.clean(); entry.save()
    return entry


@transaction.atomic
def post_expense(expense_id: int) -> JournalEntry:
    exp = Expense.objects.select_related('category', 'tax').get(pk=expense_id)
    cur = _base_currency()
    # Avoid double-posting for same expense
    existing = JournalEntry.objects.filter(source="EXPENSE", source_id=exp.id, is_posted=True).first()
    if existing:
        return existing

    rate = Decimal("0")
    if exp.tax:
        rate = Decimal(exp.tax.rate) / Decimal("100")
    gross = Decimal(exp.amount)
    # Assume entered amount is gross; split into net + tax
    net = (gross / (Decimal("1") + rate)) if rate > 0 else gross
    tax_amt = gross - net

    entry = JournalEntry.objects.create(
        date=exp.date,
        memo=f"Expense {exp.doc_no} - {exp.payee}",
        currency=cur,
        fx_rate=Decimal("1.0"),
        is_posted=True,
        posted_at=timezone.now(),
        source="EXPENSE",
        source_id=exp.id,
    )

    expense_acct = exp.category.default_account
    bank = _default_bank().account
    vat_input = _get_account("1410") if rate > 0 else None

    # Dr Expense (net)
    JournalLine.objects.create(entry=entry, account=expense_acct, debit=net, credit=Decimal("0"), debit_base=net)
    # Dr VAT Input (tax)
    if tax_amt and vat_input:
        JournalLine.objects.create(entry=entry, account=vat_input, debit=tax_amt, credit=Decimal("0"), debit_base=tax_amt)
    # Cr Bank (gross)
    JournalLine.objects.create(entry=entry, account=bank, debit=Decimal("0"), credit=gross, credit_base=gross)

    entry.clean(); entry.save()
    return entry
