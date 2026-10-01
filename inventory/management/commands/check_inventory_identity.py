"""Read-only identity preflight, compatible with the schema before migration 0016."""
import json

from django.core.management.base import BaseCommand, CommandError
from django.db import connection
from django.db.models import Count, Q
from django.db.models.functions import Upper, Trim

from inventory.models import Product, ProductUnit, ProductBarcode


class Command(BaseCommand):
    help = 'Report identity conflicts and legacy unit-count gaps; never modifies stock or serials.'

    def add_arguments(self, parser):
        parser.add_argument('--fail', action='store_true', help='Exit nonzero when review findings exist.')

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            tables = connection.introspection.table_names(cursor)
            columns = {c.name for c in connection.introspection.get_table_description(cursor, ProductUnit._meta.db_table)}
        serials = ProductUnit.objects.annotate(canonical=Upper(Trim('serial_number'))).exclude(canonical__isnull=True).exclude(canonical='')
        report = {'duplicate_manufacturer_serials': list(serials.values('canonical').annotate(count=Count('pk')).filter(count__gt=1))}
        namespace = {}
        def register(code, owner):
            if code and code.strip():
                namespace.setdefault(code.strip().upper(), set()).add(owner)
        for row in Product.objects.values('pk', 'sku').iterator():
            register(row['sku'], f"product:{row['pk']}")
        for row in ProductUnit.objects.values('pk', 'serial_number').iterator():
            register(row['serial_number'], f"unit:{row['pk']}")
            register(f"BF-U-{row['pk']:09d}", f"unit:{row['pk']}")
        if ProductBarcode._meta.db_table in tables:
            for row in ProductBarcode.objects.values('product_id', 'code').iterator():
                register(row['code'], f"product:{row['product_id']}")
        report['ambiguous_identifiers'] = [{'code': code, 'owners': sorted(owners)} for code, owners in namespace.items() if len(owners) > 1]
        counts = dict(ProductUnit.objects.filter(status__in=['AVAILABLE', 'RESERVED']).values('product_id').annotate(n=Count('pk')).values_list('product_id', 'n'))
        report['serialized_stock_gaps'] = [dict(row, saleable_units=counts.get(row['pk'], 0)) for row in Product.objects.filter(tracking_mode='SERIAL').values('pk', 'sku', 'quantity') if row['quantity'] != counts.get(row['pk'], 0)]
        report['sold_units_without_sale_date'] = list(ProductUnit.objects.filter(status='SOLD', sold_at__isnull=True).values_list('pk', flat=True))
        report['reserved_units_without_assignment'] = list(ProductUnit.objects.filter(status='RESERVED', sale_line__isnull=True).filter(**({'order_item__isnull': True} if 'order_item_id' in columns else {})).values_list('pk', flat=True))
        if 'unit_id' in columns:
            report['missing_unit_ids'] = list(ProductUnit.objects.filter(Q(unit_id__isnull=True) | Q(unit_id='')).values_list('pk', flat=True))
        findings = any(bool(v) for v in report.values())
        report['note'] = 'Read-only findings. Reconcile with source records and a physical count; do not auto-repair history.'
        self.stdout.write(json.dumps(report, indent=2))
        if findings and options['fail']:
            raise CommandError('Inventory identity findings require review.')
