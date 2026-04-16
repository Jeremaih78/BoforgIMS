from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from inventory.models import Category, Product
from sales.models import Invoice, Payment, DocumentLine
from accounting.models import JournalEntry, JournalLine, ExpenseCategory, Expense, Currency, Account
from accounting.services.finance_dashboard import Period, dashboard_payload, get_gross_profit, get_net_profit, get_total_cogs, get_total_revenue


class AccountingPostingTests(TestCase):
    def setUp(self):
        self.cat = Category.objects.create(name="Default")
        self.product = Product.objects.create(
            name="Widget",
            sku="W-ACCT",
            category=self.cat,
            price=Decimal("100.00"),
            quantity=10,
            tax_rate=Decimal("15.00"),
        )
        self.customer = Customer.objects.create(name="Acme")
        self.currency, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "is_base": True})
        self.expense_account = Account.objects.get(code="5100")
        self.vat_input = Account.objects.get(code="1410")
        self.cat = ExpenseCategory.objects.create(name="Ops", default_account=self.expense_account)

    def test_invoice_payment_posts_journal_and_balances(self):
        inv = Invoice.objects.create(customer=self.customer)
        DocumentLine.objects.create(
            invoice=inv,
            product=self.product,
            quantity=Decimal("2"),
            unit_price=Decimal("100.00"),
            tax_rate_percent=Decimal("15.00"),
            line_total=Decimal("200.00"),
        )
        Payment.objects.create(invoice=inv, amount=inv.total, method="Cash")
        self.assertTrue(JournalEntry.objects.filter(source="INVOICE", source_id=inv.id, is_posted=True).exists())
        totals = JournalLine.objects.aggregate(d_total_sum=Sum('debit_base'), c_total_sum=Sum('credit_base'))
        d = totals.get('d_total_sum') or Decimal('0')
        c = totals.get('c_total_sum') or Decimal('0')
        self.assertEqual(round(d, 2), round(c, 2))


class FinanceDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="finance", password="safe-pass")
        self.customer = Customer.objects.create(name="Finance Customer")
        self.product_category = Category.objects.create(name="Machines")
        self.product = Product.objects.create(
            name="Printer",
            sku="FIN-PRN",
            category=self.product_category,
            price=Decimal("150.00"),
            avg_cost=Decimal("90.00"),
            quantity=5,
        )
        self.currency, _ = Currency.objects.get_or_create(code="USD", defaults={"name": "US Dollar", "is_base": True})
        self.expense_account = Account.objects.get(code="5100")
        self.expense_category = ExpenseCategory.objects.create(name="Marketing", default_account=self.expense_account)
        self.today = timezone.now().date()
        self.invoice = Invoice.objects.create(customer=self.customer, date=self.today, status=Invoice.PAID)
        DocumentLine.objects.create(
            invoice=self.invoice,
            product=self.product,
            quantity=Decimal("2"),
            unit_price=Decimal("150.00"),
            tax_rate_percent=Decimal("0"),
            line_total=Decimal("300.00"),
        )
        Payment.objects.create(invoice=self.invoice, amount=Decimal("200.00"), date=self.today, method="Cash")
        Expense.objects.create(
            date=self.today,
            payee="Ad Platform",
            category=self.expense_category,
            amount=Decimal("50.00"),
            currency=self.currency,
            posted=True,
        )

    def test_finance_service_core_metrics(self):
        period = Period(self.today, self.today)

        self.assertEqual(get_total_revenue(period), Decimal("300.00"))
        self.assertEqual(get_total_cogs(period), Decimal("180.00"))
        self.assertEqual(get_gross_profit(period), Decimal("120.00"))
        self.assertEqual(get_net_profit(period), Decimal("70.00"))

    def test_dashboard_payload_contains_insights_and_trends(self):
        period = Period(self.today, self.today)
        payload = dashboard_payload(period, "day")

        self.assertIn("summary", payload)
        self.assertIn("insights", payload)
        self.assertEqual(payload["summary"]["cash_in"], Decimal("200.00"))
        self.assertTrue(payload["revenue_trend"])

    def test_finance_dashboard_pages_render(self):
        self.client.login(username="finance", password="safe-pass")
        urls = [
            reverse("ims:accounting:accounting_dashboard"),
            reverse("ims:accounting:finance_cashflow"),
            reverse("ims:accounting:finance_expenses"),
            reverse("ims:accounting:finance_revenue"),
            reverse("ims:accounting:finance_profitability"),
        ]
        for url in urls:
            response = self.client.get(url, {"period": "today", "group_by": "day"})
            self.assertEqual(response.status_code, 200)

    def test_finance_csv_export(self):
        self.client.login(username="finance", password="safe-pass")
        response = self.client.get(reverse("ims:accounting:finance_export", args=["cashflow", "csv"]), {"period": "today", "group_by": "day"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("Cash In", response.content.decode())

    def test_cash_expense_posts_and_balances(self):
        exp = Expense.objects.create(
            date=timezone.now().date(),
            payee="Supplier",
            category=self.expense_category,
            amount=Decimal("115.00"),
            currency=self.currency,
        )
        from accounting.services.posting import post_expense
        post_expense(exp.id)
        je = JournalEntry.objects.filter(source="EXPENSE", source_id=exp.id, is_posted=True).first()
        self.assertIsNotNone(je)
        totals = JournalLine.objects.aggregate(d_total_sum=Sum('debit_base'), c_total_sum=Sum('credit_base'))
        d = totals.get('d_total_sum') or Decimal('0')
        c = totals.get('c_total_sum') or Decimal('0')
        self.assertEqual(round(d, 2), round(c, 2))
