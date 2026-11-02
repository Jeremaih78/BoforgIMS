"""Atomic money capture and balanced postings for the main IMS workspace."""
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import IntegrityError, transaction
from django.utils import timezone

from core.permissions import allowed
from accounting.models import (Account, APPayment, AuditLog, BankAccount, Currency,
    ExpenseCategory, FiscalPeriod, JournalEntry, JournalLine, MoneyMovement,
    NumberSequence, SupplierBill, SupplierBillLine, SupplierPaymentAllocation)

ZERO = Decimal('0.00')
MAX_MONEY = Decimal('999999999999.99')


def money(value):
    try:
        result = Decimal(str(value)).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)
        if not result.is_finite() or abs(result) > MAX_MONEY:
            raise InvalidOperation
        return result
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('Enter an amount within the supported money limit.') from exc


def book_rate(currency, value):
    try:
        rate = Decimal(str(value)).quantize(Decimal('.000001'))
        if not rate.is_finite() or rate <= 0 or rate > Decimal('999999999999.999999'):
            raise InvalidOperation
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError('Enter a positive exchange rate.') from exc
    if currency.code == getattr(settings, 'BASE_CURRENCY_CODE', 'USD') and rate != 1:
        raise ValidationError('Use an exchange rate of 1 for the base currency.')
    return rate


def check_posting_date(day, company_id=None):
    if day > timezone.localdate():
        raise ValidationError('Actual money movements cannot be dated in the future.')
    if FiscalPeriod.objects.filter(company_id=company_id, is_closed=True, start_date__lte=day, end_date__gte=day).exists():
        raise ValidationError('This accounting period is closed. Choose an open posting date.')


def gl_account(code, company_id=None):
    try:
        return Account.objects.get(code=code, company_id=company_id, is_active=True)
    except Account.DoesNotExist as exc:
        raise ValidationError(f'Accounting account {code} is not configured or is inactive.') from exc


def resolve_bank(currency, bank=None, company_id=None, lock=True):
    if currency.company_id != company_id:
        raise ValidationError('Choose a currency belonging to this company.')
    if bank is None:
        bank = BankAccount.objects.filter(company_id=company_id, currency=currency, is_active=True, account__is_active=True).order_by('pk').first()
        if bank is None and company_id is None and currency.code == getattr(settings,'BASE_CURRENCY_CODE','USD'):
            from .posting import _default_bank
            bank = _default_bank()
    if bank is None:
        raise ValidationError(f'Choose or create a {currency.code} money account first.')
    query = BankAccount.objects.select_related('currency','account')
    if lock:
        query = query.select_for_update(of=('self',))
    bank = query.get(pk=getattr(bank,'pk',bank))
    if not bank.is_active or not bank.account.is_active or bank.account.type != Account.ASSET:
        raise ValidationError('Choose an active cash, bank or mobile-money account.')
    if bank.company_id != company_id or bank.account.company_id != company_id or bank.currency_id != currency.pk:
        raise ValidationError('The money account must match this transaction’s company and currency.')
    return bank


@transaction.atomic
def post_entry(*, day, currency, rate, memo, source, source_id=None, user=None, company_id=None, lines):
    check_posting_date(day, company_id)
    if currency.company_id != company_id:
        raise ValidationError('The journal currency belongs to a different company.')
    rate = book_rate(currency, rate)
    entry = JournalEntry.objects.create(date=day, currency=currency, fx_rate=rate,
        memo=memo[:255], source=source, source_id=source_id, created_by=user, company_id=company_id)
    rows = []
    for line in lines:
        account, debit, credit, *base = line
        if not account.is_active or account.company_id != company_id:
            raise ValidationError('A posting account is inactive or belongs to another company.')
        debit, credit = money(debit), money(credit)
        if debit < 0 or credit < 0 or (debit and credit):
            raise ValidationError('Each journal line must contain one positive side.')
        debit_base, credit_base = (money(base[0]), money(base[1])) if base else (money(debit*rate),money(credit*rate))
        rows.append(JournalLine(entry=entry,account=account,debit=debit,credit=credit,debit_base=debit_base,credit_base=credit_base))
    if sum(row.debit for row in rows) != sum(row.credit for row in rows):
        raise ValidationError('The transaction does not balance in its currency.')
    JournalLine.objects.bulk_create(rows)
    entry.is_posted=True
    entry.posted_at=timezone.now()
    entry.clean()
    entry.save(update_fields=['is_posted','posted_at'])
    return entry


def _require(user, permission):
    if not allowed(user, permission):
        raise PermissionDenied


def _match(record, values):
    for field, value in values.items():
        if getattr(record, field) != value:
            raise ValidationError('This save key was already used for different details. Reload the form before saving.')
    return record


def record_movement(*, user, submission_key, kind, date, amount, fx_rate=1,
                    source_account=None, destination_account=None, counter_account=None, reference='', notes=''):
    _require(user,'accounting.add_moneymovement')
    amount=money(amount)
    values=dict(kind=kind,date=date,amount=amount,fx_rate=Decimal(str(fx_rate)),
        source_account_id=getattr(source_account,'pk',None),destination_account_id=getattr(destination_account,'pk',None),
        counter_account_id=getattr(counter_account,'pk',None),reference=reference.strip(),notes=notes.strip(),created_by_id=user.pk)
    existing=MoneyMovement.objects.filter(submission_key=submission_key).first()
    if existing:
        return _match(existing,values)
    try:
        with transaction.atomic():
            banks={bank.pk:bank for bank in BankAccount.objects.select_for_update(of=('self',)).select_related('currency','account').filter(pk__in=[v for v in (values['source_account_id'],values['destination_account_id']) if v]).order_by('pk')}
            source=banks.get(values['source_account_id']); destination=banks.get(values['destination_account_id'])
            bank=source or destination
            if amount <= 0 or not bank or kind not in dict(MoneyMovement.KIND_CHOICES):
                raise ValidationError('Choose the movement type, account and a positive amount.')
            company_id=bank.company_id
            currency=bank.currency
            rate=book_rate(currency,fx_rate)
            for candidate in banks.values():
                resolve_bank(currency,candidate,company_id,lock=False)
            if kind == MoneyMovement.TRANSFER:
                if not source or not destination or source.account_id == destination.account_id or counter_account:
                    raise ValidationError('Choose two different money accounts in the same currency.')
                lines=[(destination.account,amount,ZERO),(source.account,ZERO,amount)]
            else:
                if not counter_account or counter_account.company_id != company_id or not counter_account.is_active or counter_account.type not in {Account.EQUITY,Account.LIABILITY}:
                    raise ValidationError('Choose an active owner-equity or loan account for funding/withdrawals.')
                if kind == MoneyMovement.IN and destination and not source:
                    lines=[(destination.account,amount,ZERO),(counter_account,ZERO,amount)]
                elif kind == MoneyMovement.OUT and source and not destination:
                    lines=[(counter_account,amount,ZERO),(source.account,ZERO,amount)]
                else:
                    raise ValidationError('Choose the receiving or paying account for this movement.')
            entry=post_entry(day=date,currency=currency,rate=rate,memo=reference or dict(MoneyMovement.KIND_CHOICES)[kind],
                source='MONEY',user=user,company_id=company_id,lines=lines)
            record=MoneyMovement.objects.create(submission_key=submission_key,entry=entry,**values)
            entry.source_id=record.pk; entry.save(update_fields=['source_id'])
            AuditLog.objects.create(model='MoneyMovement',object_id=record.pk,action='POSTED',user=user,diff={'journal':entry.number})
            return record
    except IntegrityError:
        existing=MoneyMovement.objects.filter(submission_key=submission_key).first()
        if existing:
            return _match(existing,values)
        raise


@transaction.atomic
def create_money_account(*, user, name, currency, kind='BANK', opening_balance=0, date=None, fx_rate=1):
    _require(user,'accounting.add_bankaccount')
    if currency.company_id is not None or kind not in {'BANK','CASH','MOBILE'}:
        raise ValidationError('Choose a currency for the main workspace.')
    sequence,_=NumberSequence.objects.get_or_create(company=None,key='MONEY_ACCOUNT',defaults={'prefix':'MA-'})
    sequence=NumberSequence.objects.select_for_update().get(pk=sequence.pk)
    if BankAccount.objects.filter(company=None,name__iexact=name.strip()).exists():
        raise ValidationError('A money account with this name already exists.')
    account=Account.objects.create(code=sequence.next(),name=name.strip(),type=Account.ASSET)
    bank=BankAccount.objects.create(name=name.strip(),currency=currency,account=account,kind=kind)
    amount=money(opening_balance)
    if amount < 0:
        raise ValidationError('Enter a non-negative opening balance.')
    if amount:
        _require(user,'accounting.add_moneymovement')
        from uuid import uuid4
        record_movement(user=user,submission_key=uuid4(),kind=MoneyMovement.IN,date=date or timezone.localdate(),
            amount=amount,fx_rate=fx_rate,destination_account=bank,counter_account=gl_account('3000'),reference='Opening balance')
    return bank


def record_supplier_bill(*, user, submission_key, supplier, date, due_date, currency, category, amount,
                         fx_rate=1, tax=None, reference=''):
    _require(user,'accounting.add_supplierbill')
    amount=money(amount); rate=book_rate(currency,fx_rate)
    values=dict(supplier_id=supplier.pk,date=date,due_date=due_date,currency_id=currency.pk,total=amount,
        fx_rate=rate,reference=reference.strip(),created_by_id=user.pk)
    def match(bill):
        _match(bill,values)
        line=bill.lines.first()
        if not line or line.account_id != category.default_account_id or line.tax_id != getattr(tax,'pk',None):
            raise ValidationError('This save key belongs to different bill details.')
        return bill
    existing=SupplierBill.objects.filter(submission_key=submission_key).first()
    if existing:
        return match(existing)
    try:
        with transaction.atomic():
            if amount <= 0 or currency.company_id is not None or category.company_id is not None or category.default_account.type != Account.EXPENSE:
                raise ValidationError('Choose a positive amount, main-workspace currency and expense category.')
            if due_date and due_date < date:
                raise ValidationError('The due date cannot be before the bill date.')
            tax_value=Decimal(str(tax.rate)) if tax else ZERO
            if tax and (tax.company_id is not None or not tax_value.is_finite() or tax_value<0):
                raise ValidationError('Choose a tax rate for the main workspace.')
            tax_rate=tax_value/100 if tax and tax.is_claimable else ZERO
            net=money(amount/(1+tax_rate)) if tax_rate else amount
            tax_amount=amount-net
            bill=SupplierBill.objects.create(submission_key=submission_key,subtotal=net,tax=tax_amount,**values)
            SupplierBillLine.objects.create(bill=bill,description=category.name,qty=1,unit_price=net,account=category.default_account,tax=tax)
            gross_base=money(amount*rate); net_base=money(net*rate)
            lines=[(category.default_account,net,ZERO,net_base,ZERO)]
            if tax_amount:
                lines.append((gl_account('1410'),tax_amount,ZERO,gross_base-net_base,ZERO))
            lines.append((gl_account('2000'),ZERO,amount,ZERO,gross_base))
            post_entry(day=date,currency=currency,rate=rate,memo=f'Bill {bill.doc_no} - {supplier.name}',source='BILL',source_id=bill.pk,user=user,lines=lines)
            bill.status=SupplierBill.POSTED; bill.save(update_fields=['status'])
            AuditLog.objects.create(model='SupplierBill',object_id=bill.pk,action='POSTED',user=user)
            return bill
    except IntegrityError:
        existing=SupplierBill.objects.filter(submission_key=submission_key).first()
        if existing:
            return match(existing)
        raise


def record_supplier_payment(*, user, submission_key, bill_id, bank, amount, date, method='Bank', notes=''):
    _require(user,'accounting.add_appayment')
    amount=money(amount)
    existing=APPayment.objects.filter(submission_key=submission_key).first()
    def match(payment):
        allocation=payment.allocations.first()
        if not allocation or allocation.bill_id != bill_id:
            raise ValidationError('This save key belongs to another supplier bill.')
        return _match(payment,dict(bank_id=bank.pk,amount=amount,date=date,method=method,notes=notes or ''))
    if existing:
        return match(existing)
    try:
        with transaction.atomic():
            from inventory.models import Supplier
            bill=SupplierBill.objects.get(pk=bill_id)
            Supplier.objects.select_for_update().get(pk=bill.supplier_id)
            bill=SupplierBill.objects.select_for_update(of=('self',)).select_related('currency').get(pk=bill_id)
            existing=APPayment.objects.filter(submission_key=submission_key).first()
            if existing:
                return match(existing)
            if bill.company_id is not None or bill.status not in {SupplierBill.POSTED,SupplierBill.PARTIAL}:
                raise ValidationError('Choose an unpaid posted bill in the main workspace.')
            if date < bill.date:
                raise ValidationError('The payment date cannot be before the bill date.')
            from credit_control.services.balances import bill_outstanding_balance
            balance=bill_outstanding_balance(bill,as_of_date=timezone.localdate())
            if amount <= 0 or amount > balance:
                raise ValidationError('Enter a positive payment no greater than the remaining bill balance.')
            bank=resolve_bank(bill.currency,bank)
            entry=post_entry(day=date,currency=bill.currency,rate=bill.fx_rate,memo=f'Payment for {bill.doc_no} - {bill.supplier.name}',source='SUPPLIER_PAYMENT',user=user,
                lines=[(gl_account('2000'),amount,ZERO),(bank.account,ZERO,amount)])
            payment=APPayment.objects.create(submission_key=submission_key,supplier=bill.supplier,currency=bill.currency,
                fx_rate=bill.fx_rate,bank=bank,amount=amount,date=date,method=method,notes=notes or '',entry=entry)
            SupplierPaymentAllocation.objects.create(payment=payment,bill=bill,amount=amount)
            entry.source_id=payment.pk;entry.save(update_fields=['source_id'])
            bill.status=SupplierBill.PAID if amount == balance else SupplierBill.PARTIAL
            bill.save(update_fields=['status'])
            AuditLog.objects.create(model='APPayment',object_id=payment.pk,action='POSTED',user=user,diff={'bill':bill.pk,'amount':str(amount)})
            return payment
    except IntegrityError:
        existing=APPayment.objects.filter(submission_key=submission_key).first()
        if existing:
            return match(existing)
        raise
