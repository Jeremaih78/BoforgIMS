"""Read-only preflight/reconciliation report; never repairs historical records."""
import json

from django.core.management.base import BaseCommand
from django.db.models import Count, F, Q, Sum

from accounting.models import JournalEntry, NumberSequence
from inventory.models import Product
from sales.models import Payment, StockReservation
from shop.models import OrderItem, Order


class Command(BaseCommand):
    help = 'Report stock, reservation, sequence and ledger anomalies without changing any records.'

    def handle(self, *args, **options):
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
        self.stdout.write(json.dumps(report, indent=2, default=str))
