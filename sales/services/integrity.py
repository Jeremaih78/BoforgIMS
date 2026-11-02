"""Read-only preflight/reconciliation report; never repairs historical records."""
import json

from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q, Sum,OuterRef,Subquery,Exists
from django.db import connection
from decimal import Decimal

from accounting.models import JournalEntry,JournalLine, NumberSequence,Expense,SupplierBill,APPayment
from inventory.models import Product
from sales.models import Payment, StockReservation
from shop.models import OrderItem, Order



def integrity_report():
    duplicates = lambda qs, fields: list(qs.values(*fields).annotate(count=Count('pk')).filter(count__gt=1))
    report = {
        'duplicate_reservations': duplicates(StockReservation.objects.all(), ['invoice_id', 'product_id']),
        'invalid_reservations': list(StockReservation.objects.filter(quantity__lte=0).values('id', 'quantity')),
        'duplicate_number_sequences': duplicates(NumberSequence.objects.all(), ['company_id', 'key']),
        'invalid_stock': list(Product.objects.filter(Q(quantity__lt=0) | Q(reserved__lt=0) | Q(reserved__gt=F('quantity'))).values('id', 'quantity', 'reserved')),
        'nonpositive_payments': list(Payment.objects.filter(amount__lte=0).values('id', 'invoice_id', 'amount')),
        'repeated_cogs': duplicates(JournalEntry.objects.filter(source='INVOICE', is_posted=True,
            lines__account__code='5000').distinct(), ['source_id']),
    }
    ims = dict(StockReservation.objects.values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total'))
    shop = dict(OrderItem.objects.filter(order__status=Order.Status.PENDING).values('product_id').annotate(total=Sum('quantity')).values_list('product_id', 'total'))
    mismatches = []
    for product in Product.objects.filter(track_inventory=True).values('id', 'reserved').iterator():
        expected = ims.get(product['id'], 0) + shop.get(product['id'], 0)
        if expected != product['reserved']:
            mismatches.append({**product, 'expected_from_open_records': expected})
    report['reservation_mismatches'] = mismatches
    payments = dict(Payment.objects.values('invoice_id').annotate(count=Count('pk')).values_list('invoice_id', 'count'))
    journals = dict(JournalEntry.objects.filter(source='PAYMENT', is_posted=True).values('source_id').annotate(count=Count('pk')).values_list('source_id', 'count'))
    report['payment_journal_count_mismatches'] = [
        {'invoice_id': pk, 'payments': count, 'receipt_journals': journals.get(pk, 0)}
        for pk, count in payments.items() if count != journals.get(pk, 0)
    ]
    report['note'] = 'Counts are review indicators, not proof of financial correctness. Legacy receipt journals use invoice IDs. No changes made.'
    journals=JournalEntry.objects.filter(is_posted=True).annotate(native_delta=Sum(F('lines__debit')-F('lines__credit')),base_delta=Sum(F('lines__debit_base')-F('lines__credit_base')),line_count=Count('lines'))
    report['unbalanced_posted_journals']=list(journals.filter(Q(native_delta__gt=Decimal('.005'))|Q(native_delta__lt=Decimal('-.005'))|Q(base_delta__gt=Decimal('.005'))|Q(base_delta__lt=Decimal('-.005'))|Q(line_count=0)).values('id','native_delta','base_delta','line_count'))
    report['missing_expense_journals']=list(Expense.objects.filter(Q(posted=True)|Q(status=Expense.POSTED)).exclude(status=Expense.VOID).exclude(pk__in=JournalEntry.objects.filter(source='EXPENSE',is_posted=True).values('source_id')).values_list('pk',flat=True))
    report['missing_bill_journals']=list(SupplierBill.objects.filter(status__in=[SupplierBill.POSTED,SupplierBill.PARTIAL,SupplierBill.PAID]).exclude(pk__in=JournalEntry.objects.filter(source='BILL',is_posted=True).values('source_id')).values_list('pk',flat=True))
    tables=set(connection.introspection.table_names())
    required={'sales_invoice':{'tax_added'},'accounting_appayment':{'entry_id'},'accounting_supplierpaymentallocation':{'id'},'sales_documentsubmission':{'id'}}
    pending=[]
    for table,fields in required.items():
        with connection.cursor() as cursor:
            actual={column.name for column in connection.introspection.get_table_description(cursor,table)} if table in tables else set()
        pending.extend(f'{table}.{field}' for field in sorted(fields-actual))
    report['schema_pending']=pending
    if not pending:
        from sales.workspace import with_invoice_balances
        ar=JournalLine.objects.filter(entry__is_posted=True,entry__company=None,entry__source='INVOICE',entry__source_id=OuterRef('pk'),account__code='1200').order_by().values('entry__source_id').annotate(total=Sum(F('debit')-F('credit'))).values('total')
        invoices=with_invoice_balances().annotate(posted_receivable=Subquery(ar[:1]))
        report['invoice_ledger_total_mismatches']=list(invoices.filter(posted_receivable__isnull=False).exclude(posted_receivable=F('workspace_total')).values('pk','workspace_total','posted_receivable'))
        report['overpaid_invoices']=list(invoices.filter(workspace_paid__gt=F('workspace_total')).values('pk','workspace_total','workspace_paid'))
        report['missing_supplier_payment_journals']=list(APPayment.objects.filter(entry=None).values_list('pk',flat=True))
    report['note']='Read-only reconciliation. Findings identify records for review, not permission to rewrite financial history. Receipt journal source IDs are legacy invoice IDs.'
    return report
