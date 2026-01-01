from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import Account, Currency
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
