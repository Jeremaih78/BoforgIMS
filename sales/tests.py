from decimal import Decimal

from django.test import TestCase
from django.contrib.auth import get_user_model
from django.utils import timezone

from inventory.models import Product, Category
from customers.models import Customer
from .models import Invoice, InvoiceItem
from .services import StockService


class PricingAndStockTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Default")
        self.product = Product.objects.create(
            name="Widget",
            sku="W-1",
            category=self.cat,
            price=Decimal('100.00'),
            quantity=10,
            tax_rate=Decimal('10.00'),
        )
        self.customer_user = get_user_model().objects.create_user(username="u", password="p")
        self.customer = Customer.objects.create(name="Acme")

    def test_discount_math(self):
        inv = Invoice.objects.create(customer=self.customer)
        item = InvoiceItem.objects.create(
            invoice=inv,
            product=self.product,
            quantity=2,
            unit_price=Decimal('100.00'),
            discount_percent=Decimal('10.00'),
            discount_value=Decimal('5.00'),
            tax_rate=Decimal('10.00'),
        )
        self.assertEqual(round(item.total, 2), Decimal('187.00'))

    def test_reserve_and_finalize_stock(self):
        inv = Invoice.objects.create(customer=self.customer)
        InvoiceItem.objects.create(
            invoice=inv,
            product=self.product,
            quantity=3,
            unit_price=self.product.price,
        )
        # reserve
        StockService.reserve_stock(inv)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(self.product.reserved, 3)

        # finalize
        StockService.finalize_sale(inv)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 7)
        self.assertEqual(self.product.reserved, 0)
