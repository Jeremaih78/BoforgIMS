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
    return Currency.objects.get_or_create(company=None, code=code, defaults={"name": code, "is_base": True})[0]


def _get_account(code: str) -> Account:
    return Account.objects.get(company=None, code=code)


def _default_bank() -> BankAccount:
    cur = _base_currency()
    bank_gl = _get_account("1100")
    return BankAccount.objects.get_or_create(name="Default Bank", account=bank_gl, currency=cur)[0]


def _split_invoice_amounts(inv: Invoice) -> tuple[Decimal, Decimal, Decimal]:
    """Return (net, tax, gross) for invoice."""
    return (inv.subtotal, inv.tax_total, inv.total)


@transaction.atomic
def post_sales_invoice(invoice_id: int, currency=None, fx_rate=Decimal('1'), user=None) -> JournalEntry:
    inv = Invoice.objects.select_for_update().select_related("customer").prefetch_related("lines").get(pk=invoice_id)
    cur = currency or _base_currency()
    net, tax, gross = _split_invoice_amounts(inv)

    # If already posted revenue JE for this invoice, skip
    existing = JournalEntry.objects.filter(source="INVOICE", source_id=inv.id, is_posted=True, lines__account__code="4000").first()
    if existing:
        return existing

    ar = _get_account("1200")
    sales = _get_account("4000")
    vat_out = _get_account("2100")  # VAT payable

    from .money import money, post_entry
    gross_base=money(gross*fx_rate); net_base=money(net*fx_rate)
    lines=[(ar,gross,Decimal('0'),gross_base,Decimal('0'))]
    if net:
        lines.append((sales,Decimal('0'),net,Decimal('0'),net_base))
    if tax:
        lines.append((vat_out,Decimal('0'),tax,Decimal('0'),gross_base-net_base))
    return post_entry(day=inv.date,currency=cur,rate=fx_rate,memo=f'Invoice {inv.number}',source='INVOICE',source_id=inv.pk,user=user,lines=lines)


@transaction.atomic
def post_ar_receipt(invoice_id: int, amount: Decimal, payment_date=None, bank=None, currency=None, fx_rate=Decimal('1'), user=None) -> JournalEntry:
    inv = Invoice.objects.select_for_update().select_related("customer").get(pk=invoice_id)
    cur = currency or _base_currency()
    bank = bank or _default_bank()
    ar = _get_account("1200")
    from .money import post_entry
    return post_entry(day=payment_date or timezone.localdate(),currency=cur,rate=fx_rate,memo=f'Receipt for {inv.number} - {inv.customer.name}',source='PAYMENT',source_id=inv.pk,user=user,
        lines=[(bank.account,amount,Decimal('0')),(ar,Decimal('0'),amount)])


@transaction.atomic
def post_cogs_for_invoice(invoice_id: int) -> JournalEntry | None:
    """Post inventory COGS from the saved cost snapshot or maintained average cost."""
    from inventory.models import Product
    inv = Invoice.objects.select_for_update().prefetch_related("lines__product").get(pk=invoice_id)
    cur = _base_currency()
    existing = JournalEntry.objects.filter(source='INVOICE', source_id=inv.id,
        is_posted=True, lines__account__code='5000').first()
    if existing:
        return existing
    total_cogs = Decimal("0")
    product_ids = list(inv.lines.exclude(product=None).values_list('product_id', flat=True))
    locked_products = {p.pk: p for p in Product.objects.select_for_update().filter(pk__in=product_ids).order_by('pk')}
    for it in inv.items.all():
        p = locked_products.get(it.product_id)
        if not p or not p.track_inventory:
            continue
        # Missing costs remain zero and are surfaced for review; selling price is not a cost.
        avg_cost = getattr(p, "avg_cost", None)
        if not avg_cost:
            avg_cost = Decimal('0')
        if it.cost_unit_snapshot is None:
            it.cost_unit_snapshot = avg_cost
            it.save(update_fields=['cost_unit_snapshot'])
        total_cogs += (it.cost_unit_snapshot * it.quantity)
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
    exp = Expense.objects.select_for_update(of=('self',)).select_related('category', 'tax').get(pk=expense_id)
    # Avoid double-posting for same expense
    existing = JournalEntry.objects.filter(source="EXPENSE", source_id=exp.id, is_posted=True).first()
    if existing:
        return existing

    from .money import money, resolve_bank, book_rate, post_entry, gl_account
    bank=resolve_bank(exp.currency,exp.bank,exp.company_id)
    fx=book_rate(exp.currency,exp.fx_rate)
    if exp.tax and (exp.tax.company_id!=exp.company_id or not exp.tax.rate.is_finite() or exp.tax.rate<0):
        from django.core.exceptions import ValidationError
        raise ValidationError('Choose a valid tax rate for this company.')
    rate = Decimal("0")
    if exp.tax and exp.tax.is_claimable:
        rate = Decimal(exp.tax.rate) / Decimal("100")
    gross = money(exp.amount)
    if gross <= 0 or exp.category.default_account.type != Account.EXPENSE or exp.category.company_id != exp.company_id:
        from django.core.exceptions import ValidationError
        raise ValidationError('Choose a positive amount and an expense category for this company.')
    # Assume entered amount is gross; split into net + tax
    net = money(gross / (Decimal("1") + rate)) if rate > 0 else gross
    tax_amt = gross - net

    expense_acct = exp.category.default_account
    vat_input = gl_account('1410', exp.company_id) if rate > 0 else None

    gross_base=money(gross*fx);net_base=money(net*fx)
    lines=[(expense_acct,net,Decimal('0'),net_base,Decimal('0'))]
    if tax_amt and vat_input:
        lines.append((vat_input,tax_amt,Decimal('0'),gross_base-net_base,Decimal('0')))
    lines.append((bank.account,Decimal('0'),gross,Decimal('0'),gross_base))
    entry=post_entry(day=exp.date,currency=exp.currency,rate=fx,memo=f'Expense {exp.doc_no} - {exp.payee}',
        source='EXPENSE',source_id=exp.pk,user=exp.created_by,company_id=exp.company_id,lines=lines)
    Expense.objects.filter(pk=exp.pk).update(bank=bank,posted=True,status=Expense.POSTED)
    return entry
