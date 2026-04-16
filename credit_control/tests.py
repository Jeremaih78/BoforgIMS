from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting.models import (
    APPayment,
    Account,
    BankAccount,
    Company,
    Currency,
    SupplierBill,
)
from credit_control.models import CollectionTask, PromiseToPay
from credit_control.services import (
    auto_create_collection_tasks,
    build_creditor_aging,
    build_debtor_aging,
    get_customer_outstanding,
    get_supplier_outstanding,
    monitor_promises,
    sync_creditor_account_summary,
    sync_debtor_account_summary,
)
from customers.models import Customer
from inventory.models import Supplier
from sales.models import DocumentLine, Invoice, Payment


class CreditControlServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tester", password="pass12345")
        self.company = Company.objects.create(name="Boforg")
        self.currency = Currency.objects.create(company=self.company, code="USD", name="US Dollar", is_base=True)
        self.liability = Account.objects.create(company=self.company, code="2000", name="Payables", type=Account.LIABILITY)
        self.asset = Account.objects.create(company=self.company, code="1000", name="Cash", type=Account.ASSET)
        self.bank = BankAccount.objects.create(company=self.company, name="Main", account=self.asset, currency=self.currency)

        self.customer = Customer.objects.create(name="Acme Ltd")
        self.supplier = Supplier.objects.create(name="SupplyPro")

        self.invoice = Invoice.objects.create(
            customer=self.customer,
            date=date.today() - timedelta(days=40),
            due_date=date.today() - timedelta(days=10),
            status=Invoice.CONFIRMED,
        )
        DocumentLine.objects.create(
            invoice=self.invoice,
            description="Service",
            quantity=Decimal("1"),
            unit_price=Decimal("1000"),
            tax_rate_percent=Decimal("0"),
            line_total=Decimal("1000"),
        )
        Payment.objects.create(invoice=self.invoice, amount=Decimal("200"), date=date.today() - timedelta(days=5))

        self.bill = SupplierBill.objects.create(
            company=self.company,
            supplier=self.supplier,
            currency=self.currency,
            date=date.today() - timedelta(days=35),
            due_date=date.today() - timedelta(days=5),
            total=Decimal("900"),
            status=SupplierBill.POSTED,
        )
        APPayment.objects.create(
            company=self.company,
            supplier=self.supplier,
            currency=self.currency,
            bank=self.bank,
            amount=Decimal("300"),
            date=date.today() - timedelta(days=2),
        )

    def test_customer_outstanding_and_aging(self):
        data = get_customer_outstanding(self.customer)
        self.assertEqual(data["outstanding"], Decimal("800.00"))
        self.assertEqual(data["overdue"], Decimal("800.00"))

        rows = build_debtor_aging()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buckets"]["1_30"], Decimal("800.00"))

    def test_supplier_outstanding_and_aging(self):
        data = get_supplier_outstanding(self.supplier)
        self.assertEqual(data["outstanding"], Decimal("600.00"))
        self.assertEqual(data["overdue"], Decimal("600.00"))

        rows = build_creditor_aging()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["buckets"]["1_30"], Decimal("600.00"))

    def test_summary_sync_updates_accounts(self):
        debtor = sync_debtor_account_summary(self.customer)
        creditor = sync_creditor_account_summary(self.supplier)

        self.assertEqual(debtor.total_outstanding, Decimal("800.00"))
        self.assertEqual(creditor.total_outstanding, Decimal("600.00"))

    def test_broken_promise_detection(self):
        PromiseToPay.objects.create(
            customer=self.customer,
            invoice=self.invoice,
            promised_amount=Decimal("500.00"),
            promised_date=date.today() - timedelta(days=1),
            created_by=self.user,
        )
        result = monitor_promises()
        self.assertEqual(len(result["broken"]), 1)

    def test_auto_task_generation(self):
        # Invoice offset +3 days (3 days overdue) should create follow-up.
        target_date = self.invoice.due_date + timedelta(days=3)
        created = auto_create_collection_tasks(as_of_date=target_date)
        self.assertTrue(created)
        self.assertTrue(CollectionTask.objects.filter(invoice=self.invoice, due_date=target_date).exists())
