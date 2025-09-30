from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from customers.models import Customer
from inventory.models import Category, Product, Combo, ComboItem
from inventory.services.combos import add_combo_to_invoice, add_combo_to_quotation, combo_available_quantity
from sales.models import Quotation, Invoice
from sales.services import StockService


class ComboIntegrationTests(TestCase):
    def setUp(self):
        self.category = Category.objects.create(name='Bundles')
        self.product_a = Product.objects.create(
            name='Printer',
            sku='PRN-001',
            category=self.category,
            price=Decimal('100.00'),
            quantity=20,
        )
        self.product_b = Product.objects.create(
            name='Heat Press',
            sku='PRESS-001',
            category=self.category,
            price=Decimal('250.00'),
            quantity=15,
        )
        self.product_c = Product.objects.create(
            name='Starter Kit',
            sku='KIT-001',
            category=self.category,
            price=Decimal('60.00'),
            quantity=40,
        )

        self.percent_combo = Combo.objects.create(
            name='Starter Bundle',
            code='starter-bundle',
            discount_type='percent',
            discount_value=Decimal('10.00'),
        )
        ComboItem.objects.create(combo=self.percent_combo, product=self.product_a, quantity=2)
        ComboItem.objects.create(combo=self.percent_combo, product=self.product_b, quantity=1)

        self.fixed_combo = Combo.objects.create(
            name='Accessory Pack',
            code='accessory-pack',
            discount_type='fixed',
            discount_value=Decimal('30.00'),
        )
        ComboItem.objects.create(combo=self.fixed_combo, product=self.product_a, quantity=1)
        ComboItem.objects.create(combo=self.fixed_combo, product=self.product_c, quantity=3)

        self.customer = Customer.objects.create(name='Example Customer')
        self.user = get_user_model().objects.create_user(username='combo-user', password='safe-pass')

    def test_combo_available_quantity_uses_lowest_stock(self):
        expected_initial = min(self.product_a.quantity // 2, self.product_b.quantity // 1)
        self.assertEqual(combo_available_quantity(self.percent_combo), expected_initial)
        self.product_b.quantity = 4
        self.product_b.save()
        self.assertEqual(combo_available_quantity(self.percent_combo), min(self.product_a.quantity // 2, 4 // 1))

    def test_add_percent_discount_combo_expands_to_lines_and_discount(self):
        invoice = Invoice.objects.create(customer=self.customer)
        created_lines, discount_line, final_price = add_combo_to_invoice(invoice, self.percent_combo.id, quantity=3)

        self.assertEqual(len(created_lines), 2)
        self.assertIsNotNone(discount_line)

        line_a = invoice.lines.get(product=self.product_a)
        line_b = invoice.lines.get(product=self.product_b)
        self.assertEqual(line_a.quantity, Decimal('6.00'))
        self.assertEqual(line_b.quantity, Decimal('3.00'))

        base_total = (self.product_a.price * Decimal('2') + self.product_b.price) * Decimal('3')
        expected_final = self.percent_combo.compute_price() * Decimal('3')
        expected_discount = (expected_final - base_total).quantize(Decimal('0.01'))

        self.assertEqual(final_price, expected_final.quantize(Decimal('0.01')))
        self.assertEqual(discount_line.product, None)
        self.assertEqual(discount_line.quantity, Decimal('1.00'))
        self.assertEqual(discount_line.line_total, expected_discount)

    def test_add_fixed_discount_combo_applies_flat_amount(self):
        invoice = Invoice.objects.create(customer=self.customer)
        _, discount_line, final_price = add_combo_to_invoice(invoice, self.fixed_combo.id, quantity=2)
        self.assertIsNotNone(discount_line)
        self.assertEqual(discount_line.line_total, Decimal('-60.00'))
        expected_total = self.fixed_combo.compute_price() * Decimal('2')
        self.assertEqual(final_price, expected_total.quantize(Decimal('0.01')))

    def test_combo_addition_on_quotation_creates_discount_line(self):
        quotation = Quotation.objects.create(customer=self.customer)
        created_lines, discount_line, total = add_combo_to_quotation(quotation, self.percent_combo.id, quantity=1)
        self.assertEqual(len(created_lines), 2)
        self.assertIsNotNone(discount_line)
        self.assertEqual(total, self.percent_combo.compute_price().quantize(Decimal('0.01')))
        self.assertEqual(quotation.lines.filter(product__isnull=True).count(), 1)

    def test_invoice_confirmation_affects_only_product_stock(self):
        invoice = Invoice.objects.create(customer=self.customer, created_by=self.user)
        add_combo_to_invoice(invoice, self.percent_combo.id, quantity=2)
        initial_a = self.product_a.quantity
        initial_b = self.product_b.quantity

        StockService.reserve_stock(invoice)
        invoice.confirm(user=self.user)
        StockService.finalize_sale(invoice)

        self.product_a.refresh_from_db()
        self.product_b.refresh_from_db()
        discount_lines = invoice.lines.filter(product__isnull=True)

        self.assertEqual(self.product_a.quantity, initial_a - 4)
        self.assertEqual(self.product_b.quantity, initial_b - 2)
        self.assertEqual(discount_lines.count(), 1)
        self.assertIsNone(discount_lines.first().product)
