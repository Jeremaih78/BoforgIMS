import os
import shutil
import tempfile
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounting.models import Account, Currency
from inventory.forms import ShipmentCostForm
from inventory.models import Product, Supplier, Shipment, ShipmentItem, ShipmentCost, StockMovement, ProductUnit
from inventory.services import allocate_landed_costs, receive_shipment, ShipmentServiceError


class ShipmentServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('ops@example.com', 'ops@example.com', 'pass1234')
        self.supplier = Supplier.objects.create(name='Test Supplier')
        self.currency, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'is_base': True})
        Account.objects.get_or_create(code='1300', defaults={'name': 'Inventory', 'type': Account.ASSET})
        Account.objects.get_or_create(code='2000', defaults={'name': 'Accounts Payable', 'type': Account.LIABILITY})
        self.product = Product.objects.create(
            name='Serial Laptop',
            sku='LAP-001',
            price=Decimal('1000.00'),
            tracking_mode=Product.TRACK_SERIAL,
        )
        self.shipment = Shipment.objects.create(
            supplier=self.supplier,
            origin_country='CN',
            destination_country='ZW',
            incoterm=Shipment.INCOTERM_FOB,
            shipping_method=Shipment.METHOD_SEA,
            status=Shipment.STATUS_ARRIVED,
            created_by=self.user,
        )
        self.item = ShipmentItem.objects.create(
            shipment=self.shipment,
            product=self.product,
            quantity_expected=1,
            unit_purchase_price=Decimal('500.00'),
            tracking_mode=Product.TRACK_SERIAL,
        )
        ShipmentCost.objects.create(
            shipment=self.shipment,
            cost_type=ShipmentCost.TYPE_FREIGHT,
            amount=Decimal('100.00'),
            currency='USD',
        )

    def test_allocate_landed_costs_by_value(self):
        allocate_landed_costs(self.shipment)
        self.item.refresh_from_db()
        self.assertGreater(self.item.landed_unit_cost, Decimal('500.00'))
        self.assertTrue(self.shipment.are_costs_allocated)

    def test_receive_shipment_requires_serials(self):
        with self.assertRaises(ShipmentServiceError):
            receive_shipment(
                shipment_id=self.shipment.id,
                receipts=[{'item_id': self.item.id, 'quantity': 1, 'serials': []}],
                received_by=self.user,
            )

    def test_receive_shipment_creates_stock_movement_and_units(self):
        serial = 'SN12345'
        receive_shipment(
            shipment_id=self.shipment.id,
            receipts=[{'item_id': self.item.id, 'quantity': 1, 'serials': [serial]}],
            received_by=self.user,
        )
        self.shipment.refresh_from_db()
        self.assertEqual(self.shipment.status, Shipment.STATUS_RECEIVED)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 1)
        unit = ProductUnit.objects.get(serial_number=serial)
        self.assertEqual(unit.status, ProductUnit.STATUS_AVAILABLE)
        self.assertGreater(unit.landed_cost, Decimal('500.00'))


class ShipmentCostAttachmentTests(TestCase):
    def setUp(self):
        super().setUp()
        self.media_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: shutil.rmtree(self.media_dir, ignore_errors=True))
        override = self.settings(MEDIA_ROOT=self.media_dir)
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)

        self.user = get_user_model().objects.create_user('ops@example.com', 'ops@example.com', 'pass1234')
        self.supplier = Supplier.objects.create(name='Test Supplier')
        self.currency, _ = Currency.objects.get_or_create(code='USD', defaults={'name': 'US Dollar', 'is_base': True})
        Account.objects.get_or_create(code='1300', defaults={'name': 'Inventory', 'type': Account.ASSET})
        Account.objects.get_or_create(code='2000', defaults={'name': 'Accounts Payable', 'type': Account.LIABILITY})
        self.product = Product.objects.create(
            name='Widget',
            sku='WID-001',
            price=Decimal('100.00'),
            tracking_mode=Product.TRACK_QUANTITY,
        )
        self.shipment = Shipment.objects.create(
            supplier=self.supplier,
            origin_country='CN',
            destination_country='ZW',
            incoterm=Shipment.INCOTERM_FOB,
            shipping_method=Shipment.METHOD_AIR,
            status=Shipment.STATUS_ARRIVED,
            created_by=self.user,
        )

    def test_cost_upload_persists_document(self):
        self.client.login(username='ops@example.com', password='pass1234')
        upload = SimpleUploadedFile('duty.pdf', b'%PDF-1.4 test', content_type='application/pdf')
        response = self.client.post(
            reverse('ims:inventory:shipment_detail', args=[self.shipment.id]),
            data={
                'cost-cost_type': ShipmentCost.TYPE_DUTY,
                'cost-description': 'ZIMRA duty',
                'cost-amount': '75.00',
                'cost-currency': 'USD',
                'cost-fx_rate': '1.0',
                'cost-supporting_document': upload,
                'add_cost': '1',
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.shipment.refresh_from_db()
        cost = self.shipment.costs.latest('id')
        self.assertTrue(cost.supporting_document)
        self.assertIn('shipment_costs', cost.supporting_document.name)
        self.assertTrue(os.path.exists(cost.supporting_document.path))

    def test_cost_form_rejects_large_document(self):
        big_file = SimpleUploadedFile('huge.pdf', b'a' * (10 * 1024 * 1024 + 1), content_type='application/pdf')
        form = ShipmentCostForm(
            data={
                'cost_type': ShipmentCost.TYPE_FREIGHT,
                'description': 'Large file',
                'amount': '50.00',
                'currency': 'USD',
                'fx_rate': '1.0',
            },
            files={'supporting_document': big_file},
        )
        self.assertFalse(form.is_valid())
        self.assertIn('supporting_document', form.errors)
