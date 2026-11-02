"""Read-only money workspace. Balances come from posted journal lines."""
from decimal import Decimal
from django.db.models import Q, Sum, Value, DecimalField
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.conf import settings
from accounting.models import BankAccount, JournalEntry, JournalLine, Expense, APPayment, MoneyMovement
from sales.models import Payment

ZERO=Decimal('0.00')


def money_workspace(*, start, end, account_id=None, currency=None, query='', kind=''):
    banks=list(BankAccount.objects.filter(company=None).select_related('currency','account').order_by('name'))
    grouped={}
    for row in JournalLine.objects.filter(entry__company=None,entry__is_posted=True,entry__date__lte=end,account_id__in=[b.account_id for b in banks]).values('account_id','entry__currency_id').annotate(debit=Sum('debit'),credit=Sum('credit')):
        grouped[(row['account_id'],row['entry__currency_id'])]=row['debit']-row['credit']
    counts={}
    for bank in banks:
        counts[(bank.account_id,bank.currency_id)]=counts.get((bank.account_id,bank.currency_id),0)+1
    for bank in banks:
        bank.balance=grouped.get((bank.account_id,bank.currency_id),ZERO)
        bank.shared=counts[(bank.account_id,bank.currency_id)]>1
    selected=[bank for bank in banks if (not account_id or str(bank.pk)==str(account_id)) and (not currency or bank.currency.code==currency)]
    pairs=Q(pk__in=[])
    for bank in selected:
        pairs |= Q(account_id=bank.account_id,entry__currency_id=bank.currency_id)
    lines=JournalLine.objects.filter(pairs,entry__company=None,entry__is_posted=True,entry__date__range=[start,end])
    entries=JournalEntry.objects.filter(pk__in=lines.values('entry_id')).select_related('currency','created_by','money_movement__source_account','money_movement__destination_account').order_by('-date','-pk')
    if query:
        entries=entries.filter(Q(memo__icontains=query)|Q(number__icontains=query))
    if kind:
        entries=entries.filter(source=kind)
    amount=DecimalField(max_digits=22,decimal_places=6)
    entries=entries.annotate(money_in=Coalesce(Sum('lines__debit',filter=Q(lines__in=lines)),Value(ZERO),output_field=amount),money_out=Coalesce(Sum('lines__credit',filter=Q(lines__in=lines)),Value(ZERO),output_field=amount))
    summary={}
    seen=set()
    for bank in selected:
        key=(bank.account_id,bank.currency_id)
        if key in seen: continue
        seen.add(key)
        data=summary.setdefault(bank.currency.code,dict(currency=bank.currency.code,balance=ZERO,money_in=ZERO,money_out=ZERO,transfers=ZERO))
        data['balance']+=bank.balance
    for row in entries.values('currency__code','source').annotate(total_in=Sum('lines__debit',filter=Q(lines__in=lines)),total_out=Sum('lines__credit',filter=Q(lines__in=lines))):
        data=summary.setdefault(row['currency__code'],dict(currency=row['currency__code'],balance=ZERO,money_in=ZERO,money_out=ZERO,transfers=ZERO))
        # Both sides of an internal transfer are activity, never external cashflow.
        if row['source']=='MONEY':
            pass
        else:
            data['money_in']+=row['total_in'] or ZERO;data['money_out']+=row['total_out'] or ZERO
    # Funding and withdrawals are external movements; transfers remain separate.
    movements=MoneyMovement.objects.filter(entry__in=entries).select_related('entry__currency')
    for movement in movements:
        data=summary[movement.entry.currency.code]
        if movement.kind==MoneyMovement.TRANSFER: data['transfers']+=movement.amount
        elif movement.kind==MoneyMovement.IN: data['money_in']+=movement.amount
        else: data['money_out']+=movement.amount
    missing=(Expense.objects.filter(company=None,posted=True,date__range=[start,end]).exclude(pk__in=JournalEntry.objects.filter(source='EXPENSE',is_posted=True).values('source_id')).count()
        +Payment.objects.filter(date__range=[start,end],entry=None).exclude(invoice_id__in=JournalEntry.objects.filter(source='PAYMENT',is_posted=True).values('source_id')).count()
        +APPayment.objects.filter(company=None,date__range=[start,end],entry=None).count())
    return {'money_accounts':banks,'selected_accounts':selected,'entries':entries,'money_summary':list(summary.values()),'unposted_count':missing}
