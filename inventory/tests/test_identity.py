from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, Client
from django.urls import reverse

from accounting.models import Account, Currency
from customers.models import Customer
from inventory.models import (Product, ProductUnit, Supplier, Shipment, ShipmentItem,
    StockMovement, UnitSale, ProductBarcode, ScanRequest)
from inventory.services import receive_shipment
from inventory.services.identity import assign_barcode, resolve, adopt_existing, transition_unit, correct_serial
from inventory.services.stocktake import start_stocktake, scan_stocktake, submit_stocktake, approve_stocktake
from inventory.services.after_sales import open_case, update_case
from sales.models import Invoice, Quotation
from sales.services import StockService
from sales.services.scanning import scan_document


class IdentityFixture:
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('identity-manager', '', 'test-password')
        self.customer = Customer.objects.create(name='Machine customer')
        self.product = Product.objects.create(name='Heat press', sku='HP-TEST', price=100, quantity=3, avg_cost=40, warranty_days=365)
        adopt_existing(product_id=self.product.pk, actor=self.user, note='Physical count verified', location='A1')
        self.product.refresh_from_db()
        self.units = list(self.product.units.order_by('pk'))
        self.invoice = Invoice.objects.create(customer=self.customer, created_by=self.user)

    def scan(self, code, invoice=None, key=None):
        return scan_document(document_id=(invoice or self.invoice).pk, kind='invoice', code=code, key=key or uuid4(), actor=self.user)


class IdentityTests(IdentityFixture, TestCase):
    def test_print_all_matching_includes_other_pages_and_respects_filters(self):
        from io import BytesIO
        from pypdf import PdfReader
        self.client.force_login(self.user)
        for _ in range(31):
            ProductUnit.objects.create(product=self.product, location='A1')
        elsewhere = ProductUnit.objects.create(product=self.product, location='B2')
        sold = ProductUnit.objects.create(product=self.product, location='A1', status='SOLD')
        response = self.client.post(reverse('ims:inventory:labels'), {
            'kind':'unit', 'all_matching':'1', 'q':self.product.sku,
            'location':'A1', 'status':'AVAILABLE', 'layout':'a4',
        })
        self.assertEqual(response.status_code, 200)
        pdf = PdfReader(BytesIO(response.content))
        self.assertEqual(len(pdf.pages), 3)
        text = '\n'.join(page.extract_text() for page in pdf.pages)
        expected = self.product.units.filter(location='A1', status='AVAILABLE')
        self.assertEqual(expected.count(), 34)
        for unit in expected:
            self.assertIn(unit.unit_id, text)
            self.assertTrue(unit.events.filter(event='LABEL_EXPORTED').exists())
        self.assertNotIn(elsewhere.unit_id, text)
        self.assertNotIn(sold.unit_id, text)
        self.assertIn('Cut on the dashed outlines', text)

    def test_printing_empty_selection_and_empty_filter_are_rejected(self):
        self.client.force_login(self.user)
        url = reverse('ims:inventory:labels')
        self.assertEqual(self.client.post(url, {'kind':'unit'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'kind':'unit','all_matching':'1','q':'no-match'}).status_code, 400)
        self.assertEqual(self.client.post(url, {'kind':'product','all_matching':'1'}).status_code, 400)

    def test_aliases_preserve_zeroes_and_resolve_both_identities(self):
        alias = assign_barcode(product_id=self.product.pk, code=' 0012345678905 ', actor=self.user)
        internal = assign_barcode(product_id=self.product.pk, actor=self.user, internal=True)
        self.assertEqual(alias.code, '0012345678905')
        for code in (alias.code, internal.code, self.product.sku.lower()):
            self.assertEqual(resolve(code), (self.product, None))
        self.assertEqual(resolve(self.units[0].unit_id)[1], self.units[0])
        self.assertEqual(assign_barcode(product_id=self.product.pk, actor=self.user, internal=True), internal)
        self.assertFalse(StockMovement.objects.exists())  # Adoption never doubles stock.

    def test_collisions_unknown_and_malicious_scans_do_not_mutate(self):
        other = Product.objects.create(name='Other', sku='OTHER')
        assign_barcode(product_id=self.product.pk, code='00111', actor=self.user)
        for code in ('00111', self.units[0].unit_id, self.product.sku):
            with self.assertRaises(ValidationError):
                assign_barcode(product_id=other.pk, code=code, actor=self.user)
        for code in ('UNKNOWN', '<script>alert(1)</script>', 'https://example.com', 'x' * 121):
            with self.assertRaises(ValidationError):
                self.scan(code)
        self.assertFalse(self.invoice.lines.exists())

    def test_serial_correction_audited_id_immutable_and_legacy_case_lookup(self):
        unit = self.units[0]
        correct_serial(unit_id=unit.pk, serial='sn-001', actor=self.user, note='Read manufacturer plate')
        self.assertEqual(resolve('SN-001')[1], unit)
        self.assertTrue(unit.events.filter(event='SERIAL_CORRECTED').exists())
        with self.assertRaises(ValidationError):
            correct_serial(unit_id=self.units[1].pk, serial='SN-001', actor=self.user, note='Duplicate')
        unit.refresh_from_db()
        unit.unit_id = 'BF-U-999999999'
        with self.assertRaises(ValidationError):
            unit.save()
        with self.assertRaises(ValidationError):
            unit.delete()

    def test_serial_scan_retry_duplicate_and_other_invoice(self):
        key = uuid4()
        first = self.scan(self.units[0].unit_id, key=key)
        self.assertEqual(self.scan(self.units[0].unit_id, key=key), first)
        self.assertTrue(self.scan(self.units[0].unit_id)['duplicate'])
        self.assertEqual(self.invoice.lines.get().quantity, 1)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (3, 1))
        other = Invoice.objects.create(customer=self.customer)
        with self.assertRaises(ValidationError):
            self.scan(self.units[0].unit_id, invoice=other)
        self.assertFalse(other.lines.exists())

    def test_product_scan_requires_unit_and_quote_does_not_reserve(self):
        result = self.scan(self.product.sku)
        self.assertTrue(result['needs_unit'])
        self.assertEqual(len(result['units']), 3)
        self.assertFalse(self.invoice.lines.exists())
        quote = Quotation.objects.create(customer=self.customer)
        scan_document(document_id=quote.pk, kind='quotation', code=self.units[0].unit_id, key=uuid4(), actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 0)
        self.assertEqual(quote.lines.get().product, self.product)

    def test_quantity_scan_increment_retry_and_insufficient_stock_rollback(self):
        product = Product.objects.create(name='Ink', sku='INK', quantity=2, price=10)
        key = uuid4()
        self.scan(product.sku, key=key)
        self.scan(product.sku, key=key)
        self.scan(product.sku)
        with self.assertRaises(ValueError):
            self.scan(product.sku)
        self.assertEqual(self.invoice.lines.get().quantity, 2)
        product.refresh_from_db()
        self.assertEqual(product.reserved, 2)

    def test_combo_component_scan_fills_existing_line_and_preserves_discount(self):
        from inventory.models import Combo, ComboItem
        from inventory.services.combos import add_combo_to_invoice
        combo = Combo.objects.create(name='Machine bundle', code='machine-test', discount_type='percent', discount_value=10)
        ComboItem.objects.create(combo=combo, product=self.product, quantity=1)
        add_combo_to_invoice(self.invoice, combo.pk, quantity=1)
        before = self.invoice.total
        self.scan(self.units[0].unit_id)
        self.assertEqual(self.invoice.total, before)
        self.assertEqual(self.invoice.lines.filter(product=self.product).get().quantity, 1)

    def test_sale_return_repair_restock_resale_keeps_history(self):
        unit = self.units[0]
        self.scan(unit.unit_id)
        StockService.finalize_sale(self.invoice)
        StockService.finalize_sale(self.invoice)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (2, 0))
        self.assertEqual(unit.sales_history.count(), 1)
        self.assertIsNotNone(unit.sales_history.get().warranty_expires)
        case = open_case(unit_id=unit.pk, problem='Heating fault', actor=self.user)
        with self.assertRaises(ValidationError):
            transition_unit(unit_id=unit.pk, action='restock', note='Too soon', actor=self.user)
        update_case(case_id=case.pk, action='repair', inspection='Replaced fuse', resolution='', actor=self.user)
        update_case(case_id=case.pk, action='close', inspection='Tested', resolution='Working', actor=self.user)
        transition_unit(unit_id=unit.pk, action='restock', note='Inspection passed', actor=self.user)
        other = Invoice.objects.create(customer=self.customer, created_by=self.user)
        self.scan(unit.unit_id, invoice=other)
        StockService.finalize_sale(other)
        self.assertEqual(unit.sales_history.count(), 2)
        self.assertEqual(set(unit.sales_history.values_list('invoice_id', flat=True)), {self.invoice.pk, other.pk})

    def test_replacement_consumes_available_unit_once(self):
        self.scan(self.units[0].unit_id)
        StockService.finalize_sale(self.invoice)
        case = open_case(unit_id=self.units[0].pk, problem='Warranty failure', actor=self.user)
        update_case(case_id=case.pk, action='close', inspection='Confirmed', resolution='Replaced', replacement=self.units[1], actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 1)
        self.assertEqual(self.units[1].sales_history.get().warranty_expires, self.units[0].sales_history.get().warranty_expires)
        with self.assertRaises(ValidationError):
            update_case(case_id=case.pk, action='close', inspection='', resolution='Again', replacement=self.units[2], actor=self.user)
        reopened = open_case(unit_id=self.units[0].pk, problem='Additional inspection', actor=self.user)
        with self.assertRaises(ValidationError):
            update_case(case_id=reopened.pk, action='close', inspection='Same fault', resolution='Duplicate replacement', replacement=self.units[2], actor=self.user)
        with self.assertRaises(ValidationError):
            transition_unit(unit_id=self.units[0].pk, action='customer_return', actor=self.user, note='Cannot hand out both machines')

    def test_release_and_manual_adjustment_protection(self):
        self.scan(self.units[0].unit_id)
        StockService.release_reservation(self.invoice)
        self.units[0].refresh_from_db()
        self.assertEqual(self.units[0].status, 'AVAILABLE')
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(product=self.product, movement_type='IN', quantity=1, unit_cost=40)

    def test_stocktake_counts_unique_units_and_approval_is_separate(self):
        session = start_stocktake(name='Warehouse', actor=self.user)
        for _ in range(2):
            scan_stocktake(session_id=session.pk, code=self.units[0].unit_id, key=uuid4(), actor=self.user)
        self.assertEqual(session.lines.get(unit=self.units[0]).counted, 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 3)
        submit_stocktake(session_id=session.pk)
        decisions = {str(l.pk): 'ADJUST' for l in session.lines.all()}
        approve_stocktake(session_id=session.pk, decisions=decisions, reason='Verified missing machines', actor=self.user)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 1)
        self.assertEqual(self.product.units.filter(status='WRITTEN_OFF').count(), 2)
        with self.assertRaises(ValidationError):
            approve_stocktake(session_id=session.pk, decisions=decisions, reason='Retry', actor=self.user)

    def test_stocktake_rejects_stale_snapshot(self):
        session = start_stocktake(name='Warehouse', actor=self.user)
        self.scan(self.units[0].unit_id)
        submit_stocktake(session_id=session.pk)
        with self.assertRaises(ValidationError):
            approve_stocktake(session_id=session.pk, decisions={str(l.pk): 'KEEP' for l in session.lines.all()}, reason='Review', actor=self.user)

    def test_bulk_count_is_audited_and_does_not_change_stock_before_approval(self):
        from inventory.services.stocktake import set_quantity_count
        ink = Product.objects.create(name='Ink', sku='BULK-INK', quantity=100, avg_cost=5)
        session = start_stocktake(name='Bulk count', actor=self.user)
        line = session.lines.get(product=ink)
        set_quantity_count(session_id=session.pk, line_id=line.pk, count='80', reason='4 sealed boxes of 20', actor=self.user)
        ink.refresh_from_db()
        self.assertEqual(ink.quantity, 100)
        self.assertIn('4 sealed boxes', session.scans.get().note)
        with self.assertRaises(ValidationError):
            set_quantity_count(session_id=session.pk, line_id=session.lines.filter(unit__isnull=False).first().pk, count='3', reason='Invalid bulk machine count', actor=self.user)
        submit_stocktake(session_id=session.pk)
        decisions = {str(row.pk): ('ADJUST' if row.pk == line.pk else 'KEEP') for row in session.lines.all()}
        approve_stocktake(session_id=session.pk, decisions=decisions, reason='Verified', actor=self.user)
        ink.refresh_from_db()
        self.assertEqual(ink.quantity, 80)

    def test_shop_reserves_exact_units_releases_failure_and_finalizes_once(self):
        from shop.models import Cart, CartItem
        from shop.services import create_order_from_cart, mark_order_as_failed, mark_order_as_paid
        cart = Cart.objects.create(session_key='identity-cart')
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        order = create_order_from_cart(cart, email='qa@example.com', full_name='Online customer').order
        self.assertEqual(self.product.units.filter(status='RESERVED', order_item__order=order).count(), 2)
        mark_order_as_failed(order)
        self.assertEqual(self.product.units.filter(status='AVAILABLE').count(), 3)
        CartItem.objects.create(cart=cart, product=self.product, quantity=2)
        second = create_order_from_cart(cart, email='qa@example.com', full_name='Online customer').order
        mark_order_as_paid(second)
        mark_order_as_paid(second)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (1, 0))
        self.assertEqual(UnitSale.objects.filter(order_item__order=second).count(), 2)

    def test_invoice_print_preserves_unit_identity_after_restock(self):
        from django.template.loader import render_to_string
        self.scan(self.units[0].unit_id)
        StockService.finalize_sale(self.invoice)
        transition_unit(unit_id=self.units[0].pk, action='return', actor=self.user, note='Customer return')
        transition_unit(unit_id=self.units[0].pk, action='restock', actor=self.user, note='Inspection passed')
        html = render_to_string('sales/pdf_invoice.html', {'inv':self.invoice, 'lines':self.invoice.lines.all()})
        self.assertIn(self.units[0].unit_id, html)

    def test_unknown_scan_offers_permission_checked_assignment(self):
        self.client.force_login(self.user)
        response = self.client.post(reverse('ims:inventory:scan_lookup'), {'code':'NEW-FACTORY-CODE'})
        self.assertEqual(response.status_code, 400)
        self.assertIn('assign_url', response.json())
        self.assertEqual(self.client.get(response.json()['assign_url']).status_code, 200)
        assigned = self.client.post(reverse('ims:inventory:assign_barcode'), {'code':'NEW-FACTORY-CODE', 'product':self.product.pk})
        self.assertEqual(assigned.status_code, 302)
        self.assertEqual(resolve('NEW-FACTORY-CODE'), (self.product, None))
        staff = get_user_model().objects.create_user('scan-staff')
        staff.groups.add(Group.objects.get_or_create(name='Staff')[0])
        self.client.force_login(staff)
        self.assertNotIn('assign_url', self.client.post(reverse('ims:inventory:scan_lookup'), {'code':'ANOTHER-UNKNOWN'}).json())
        self.assertEqual(self.client.post(reverse('ims:inventory:assign_barcode'), {'code':'UNAUTHORIZED','product':self.product.pk}).status_code, 403)

    def test_legacy_backfill_preserves_stock_serial_and_sale(self):
        from importlib import import_module
        from django.db import connection
        from django.db.migrations.executor import MigrationExecutor
        from django.utils import timezone
        apps = MigrationExecutor(connection).loader.project_state([('inventory','0016_product_identity_enforced_product_warranty_days_and_more')]).apps
        Unit = apps.get_model('inventory','ProductUnit')
        legacy_product = Product.objects.create(name='Legacy cutter',sku='LEGACY-CUT',quantity=0,tracking_mode='SERIAL')
        line = self.invoice.lines.create(product=legacy_product,description='Legacy cutter',quantity=1,unit_price=300,line_total=300)
        unit = Unit.objects.create(product_id=legacy_product.pk,serial_number=' Original-Serial ',status='SOLD',sale_line_id=line.pk,sold_at=timezone.now(),landed_cost=100)
        backfill = import_module('inventory.migrations.0017_productunit_unit_manufacturer_serial_normalized_unique_and_more').backfill_identities
        backfill(apps,None)
        unit.refresh_from_db();legacy_product.refresh_from_db()
        self.assertEqual(unit.unit_id,f'BF-U-{unit.pk:09d}')
        self.assertEqual(unit.serial_number,' Original-Serial ')
        self.assertEqual(legacy_product.quantity,0)
        self.assertEqual(UnitSale.objects.get(unit_id=unit.pk).invoice_id,self.invoice.pk)
        self.assertEqual(resolve('original-serial')[1].pk,unit.pk)

    def test_screen_permissions_csrf_labels_and_pages(self):
        staff = get_user_model().objects.create_user('counter', password='test')
        staff.groups.add(Group.objects.get_or_create(name='Staff')[0])
        self.client.force_login(staff)
        label_url = reverse('ims:inventory:labels')
        self.assertEqual(self.client.post(label_url, {'ids': [self.units[0].pk]}).status_code, 403)
        self.assertEqual(self.client.post(reverse('ims:inventory:unit_passport', args=[self.units[0].unit_id]), {'action':'write_off','note':'x'}).status_code, 403)
        self.client.force_login(self.user)
        for name, args in [('identity_home', []), ('product_identity', [self.product.pk]), ('unit_passport', [self.units[0].unit_id]), ('stocktakes', []), ('scanner_test', [])]:
            self.assertEqual(self.client.get(reverse('ims:inventory:' + name, args=args)).status_code, 200)
        response = self.client.post(label_url, {'ids': [self.units[0].pk], 'layout':'thermal'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertTrue(self.units[0].events.filter(event='LABEL_EXPORTED').exists())
        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        self.assertEqual(csrf_client.post(label_url, {'ids':[self.units[0].pk]}).status_code, 403)


class ReceiptIdentityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('receiver')
        Currency.objects.get_or_create(code='USD', defaults={'name':'US Dollar', 'is_base':True})
        for code, name, kind in [('1300','Inventory',Account.ASSET),('2000','Payables',Account.LIABILITY)]:
            Account.objects.get_or_create(code=code, defaults={'name':name,'type':kind})
        self.product = Product.objects.create(name='Cutter', sku='CUT', tracking_mode='SERIAL')
        self.shipment = Shipment.objects.create(supplier=Supplier.objects.create(name='Factory'), status='ARRIVED', created_by=self.user)
        self.item = ShipmentItem.objects.create(shipment=self.shipment, product=self.product, quantity_expected=3, unit_purchase_price=50, tracking_mode='SERIAL')

    def receive(self, serials):
        return receive_shipment(shipment_id=self.shipment.pk, receipts=[{'item_id':self.item.pk,'quantity':3,'serials':serials,'generate_ids':True}], received_by=self.user)

    def test_optional_serials_generate_exact_units_and_receipt_cannot_double(self):
        self.receive(['FACTORY-001'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 3)
        self.assertTrue(self.product.identity_enforced)
        self.assertEqual(self.product.units.filter(serial_number__isnull=True).count(), 2)
        self.assertEqual(len(set(self.product.units.values_list('unit_id', flat=True))), 3)
        with self.assertRaises(ValidationError):
            self.receive([])
        self.assertEqual(self.product.movements.count(), 1)

    def test_duplicate_serial_and_accounting_failure_roll_back_receipt(self):
        with self.assertRaises(ValidationError):
            self.receive(['same', 'SAME'])
        with patch('inventory.services.shipments._post_inventory_receipt_journal', side_effect=ValueError('accounting unavailable')):
            with self.assertRaises(ValueError):
                self.receive([])
        self.product.refresh_from_db()
        self.item.refresh_from_db()
        self.assertEqual(self.product.quantity, 0)
        self.assertEqual(self.item.quantity_received, 0)
        self.assertFalse(self.product.units.exists())
        self.assertFalse(self.product.movements.exists())


class ConcurrentScanTests(IdentityFixture, TransactionTestCase):
    def worker(self, invoice_id, code, key):
        close_old_connections()
        try:
            scan_document(document_id=invoice_id, kind='invoice', code=code, key=key, actor=get_user_model().objects.get(pk=self.user.pk))
            return 'saved'
        except (ValidationError, ValueError):
            return 'rejected'
        finally:
            close_old_connections()

    def test_two_invoices_cannot_reserve_same_physical_unit(self):
        other = Invoice.objects.create(customer=self.customer)
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda pk: self.worker(pk, self.units[0].unit_id, uuid4()), [self.invoice.pk, other.pk]))
        self.assertCountEqual(results, ['saved', 'rejected'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 1)

    def test_concurrent_same_request_increments_quantity_once(self):
        product = Product.objects.create(name='Ink', sku='INK', quantity=5, price=10)
        key = uuid4()
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.worker(self.invoice.pk, product.sku, key), range(2)))
        self.assertEqual(results, ['saved', 'saved'])
        self.assertEqual(self.invoice.lines.get().quantity, 1)
        self.assertEqual(ScanRequest.objects.filter(key=key).count(), 1)
