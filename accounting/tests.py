from decimal import Decimal

from django.test import TestCase

from customers.models import Customer
from inventory.models import Category, Product
from sales.models import Invoice, InvoiceItem, Payment
from accounting.models import JournalEntry, JournalLine, ExpenseCategory, Expense, Currency, Account
from django.db.models import Sum
from django.utils import timezone


class AccountingPostingTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Default")
        self.product = Product.objects.create(
            name="Widget",
            sku="W-ACCT",
            category=self.cat,
            price=Decimal('100.00'),
            quantity=10,
            tax_rate=Decimal('15.00'),
        )
        self.customer = Customer.objects.create(name="Acme")
        # Ensure base currency and default accounts exist via migration seeds
        self.currency, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "is_base": True})
        self.expense_account = Account.objects.get(code="5100")
        self.vat_input = Account.objects.get(code="1410")
        self.cat = ExpenseCategory.objects.create(name="Ops", default_account=self.expense_account)

    def test_invoice_payment_posts_journal_and_balances(self):
        inv = Invoice.objects.create(customer=self.customer)
        InvoiceItem.objects.create(
            invoice=inv,
            product=self.product,
            quantity=2,
            unit_price=Decimal('100.00'),
            discount_percent=Decimal('0.00'),
            discount_value=Decimal('0.00'),
            tax_rate=Decimal('15.00'),
        )
        # pay full
        Payment.objects.create(invoice=inv, amount=inv.total, method="Cash")
        # Revenue JE should exist
        self.assertTrue(JournalEntry.objects.filter(source="INVOICE", source_id=inv.id, is_posted=True).exists())
        # Sum TB must balance
        totals = JournalLine.objects.aggregate(d_total_sum=Sum('debit_base'), c_total_sum=Sum('credit_base'))
        d = totals.get('d_total_sum') or Decimal('0')
        c = totals.get('c_total_sum') or Decimal('0')
        self.assertEqual(round(d, 2), round(c, 2))

    def test_cash_expense_posts_and_balances(self):
        exp = Expense.objects.create(
            date=timezone.now().date(),
            payee="Supplier",
            category=self.cat,
            amount=Decimal('115.00'),
            currency=self.currency,
        )
        # post
        from accounting.services.posting import post_expense
        post_expense(exp.id)
        je = JournalEntry.objects.filter(source="EXPENSE", source_id=exp.id, is_posted=True).first()
        self.assertIsNotNone(je)
        totals = JournalLine.objects.aggregate(d_total_sum=Sum('debit_base'), c_total_sum=Sum('credit_base'))
        d = totals.get('d_total_sum') or Decimal('0')
        c = totals.get('c_total_sum') or Decimal('0')
        self.assertEqual(round(d, 2), round(c, 2))
