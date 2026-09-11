from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import close_old_connections, connection
from django.db.models.deletion import ProtectedError
from django.test import TestCase, TransactionTestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from accounting.models import JournalEntry
from customers.models import Customer
from inventory.models import Product, StockMovement, Combo, ComboItem
from inventory.services.combos import add_combo_to_invoice, combo_available_quantity
from sales.models import Invoice, Quotation, Payment, StockReservation
from sales.forms import DocumentLineForm
from sales.services import StockService
from sales.services.payments import record_payment
from shop.models import Cart, CartItem, Order, OrderItem, Payment as ShopPayment
from shop.services import create_order_from_cart, mark_order_as_paid, mark_order_as_failed, OrderCreationError


class ReliabilityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user('review-staff')
        self.user.groups.add(Group.objects.get_or_create(name='Staff')[0])
        self.customer = Customer.objects.create(name='Synthetic customer')
        self.product = Product.objects.create(name='Printer', sku='review-printer', price=100, avg_cost=60, quantity=10)
        self.invoice = Invoice.objects.create(customer=self.customer)
        self.invoice.add_product_line(self.product, 2)

    def pay(self, amount='200.00', key=None):
        return record_payment(invoice_id=self.invoice.pk, amount=Decimal(amount), date=timezone.localdate(),
            method='Cash', note='', submission_key=key or uuid4(), user=self.user)

    def test_repeated_product_lines_and_retry_reserve_exact_total(self):
        self.invoice.add_product_line(self.product, 3)
        StockService.reserve_stock(self.invoice)
        StockService.reserve_stock(self.invoice)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 5)
        self.assertEqual(StockReservation.objects.get(invoice=self.invoice).quantity, 5)

    def test_removing_line_reconciles_old_reservation(self):
        StockService.reserve_stock(self.invoice)
        self.invoice.lines.all().delete()
        StockService.reserve_stock(self.invoice)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 0)
        self.assertFalse(self.invoice.reservations.exists())

    def test_combo_and_direct_line_share_availability(self):
        combo = Combo.objects.create(name='Bundle', code='review-bundle')
        ComboItem.objects.create(combo=combo, product=self.product, quantity=2)
        add_combo_to_invoice(self.invoice, combo.pk, 2)
        StockService.reserve_stock(self.invoice)
        self.assertEqual(combo_available_quantity(combo), 2)
        self.assertEqual(self.invoice.reservations.get().quantity, 6)

    def test_stock_movement_is_immutable_and_cannot_oversell(self):
        movement = StockMovement.objects.create(product=self.product, movement_type='OUT', quantity=1)
        with self.assertRaises(ValidationError):
            movement.save()
        with self.assertRaises(ValidationError):
            StockMovement.objects.create(product=self.product, movement_type='OUT', quantity=20)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 9)
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_stale_product_receipts_preserve_moving_average(self):
        StockMovement.objects.create(product=self.product, movement_type='IN', quantity=10, unit_cost=Decimal('80'))
        StockMovement.objects.create(product=self.product, movement_type='IN', quantity=10, unit_cost=Decimal('100'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 30)
        self.assertEqual(self.product.avg_cost, Decimal('80'))

    def test_duplicate_payment_only_posts_and_deducts_once(self):
        key = uuid4()
        first = self.pay(key=key)
        second = self.pay(key=key)
        self.assertEqual(first.pk, second.pk)
        self.product.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)
        self.assertEqual(self.product.reserved, 0)
        self.assertTrue(self.invoice.stock_finalized)
        self.assertEqual(self.invoice.status, Invoice.PAID)
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(JournalEntry.objects.filter(source='PAYMENT').count(), 1)
        StockService.finalize_sale(self.invoice)
        self.product.refresh_from_db()
        self.assertEqual(self.product.quantity, 8)

    def test_document_date_defaults_follow_harare_midnight(self):
        from datetime import datetime, timezone as dt_timezone, date
        with patch('django.utils.timezone.now', return_value=datetime(2026, 9, 10, 23, 30, tzinfo=dt_timezone.utc)):
            invoice = Invoice.objects.create(customer=self.customer)
            quotation = Quotation.objects.create(customer=self.customer)
        invoice.refresh_from_db()
        quotation.refresh_from_db()
        self.assertEqual(invoice.date, date(2026, 9, 11))
        self.assertEqual(quotation.date, date(2026, 9, 11))

    def test_receipt_journal_uses_entered_payment_date(self):
        from datetime import timedelta
        payment_date = timezone.localdate() - timedelta(days=1)
        Payment.objects.create(invoice=self.invoice, amount=Decimal('25'), date=payment_date)
        self.assertEqual(JournalEntry.objects.get(source='PAYMENT').date, payment_date)

    def test_instalments_do_not_repeat_cogs(self):
        self.pay('50')
        self.pay('150')
        self.assertEqual(JournalEntry.objects.filter(source='INVOICE', source_id=self.invoice.pk,
            lines__account__code='5000').count(), 1)
        self.assertEqual(self.invoice.payments.count(), 2)

    def test_posting_failure_rolls_back_payment_status_stock_and_journals(self):
        with patch('accounting.services.posting.post_ar_receipt', side_effect=ValueError('ledger unavailable')):
            with self.assertRaises(ValueError):
                self.pay()
        self.product.refresh_from_db()
        self.invoice.refresh_from_db()
        self.assertEqual(self.product.quantity, 10)
        self.assertEqual(self.product.reserved, 0)
        self.assertEqual(self.invoice.status, Invoice.PENDING)
        self.assertFalse(Payment.objects.exists())
        self.assertFalse(JournalEntry.objects.exists())

    def test_first_payment_cost_snapshot_survives_product_cost_change(self):
        from accounting.services.finance_dashboard import get_total_cogs, Period
        self.invoice.status = Invoice.CONFIRMED
        self.invoice.save(update_fields=['status'])
        self.pay('50')
        self.product.avg_cost = 90
        self.product.save(update_fields=['avg_cost'])
        today = timezone.localdate()
        self.assertEqual(get_total_cogs(Period(today, today)), Decimal('120.00'))
        self.assertEqual(self.invoice.lines.get().cost_unit_snapshot, Decimal('60.00'))

    def test_invalid_payment_and_fractional_stock_are_rejected(self):
        for amount in ('0', '-10', '201'):
            with self.assertRaises(ValueError):
                self.pay(amount)
        form = DocumentLineForm({'product': self.product.pk, 'quantity': '1.5', 'unit_price': '-2'})
        self.assertFalse(form.is_valid())
        self.assertIn('quantity', form.errors)
        self.assertIn('unit_price', form.errors)

    def test_payment_invoice_is_bound_to_route_and_retry_key(self):
        self.client.force_login(self.user)
        other = Invoice.objects.create(customer=self.customer)
        key = uuid4()
        data = {'add_payment': '1', 'invoice': other.pk, 'amount': '50', 'date': timezone.localdate(),
            'method': 'Cash', 'note': '', 'submission_key': str(key)}
        url = reverse('ims:sales:invoice_edit', args=[self.invoice.pk])
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(self.client.post(url, data).status_code, 302)
        self.assertEqual(Payment.objects.get().invoice_id, self.invoice.pk)
        self.assertFalse(other.payments.exists())

    def test_customer_history_is_protected(self):
        with self.assertRaises(ProtectedError):
            self.customer.delete()

    def test_unprivileged_login_cannot_read_internal_records_or_write_catalogue(self):
        outsider = get_user_model().objects.create_user('outsider')
        self.client.force_login(outsider)
        for url in (reverse('ims:sales:sales_home'), reverse('ims:customers:customer_list'),
                    reverse('ims:accounting:accounting_dashboard'), reverse('ims:inventory:shipment_list')):
            self.assertEqual(self.client.get(url).status_code, 403, url)
        for host, url in [('testserver', '/ims/inventory/api/products/'), ('api.boforg.co.zw', '/inventory/products/')]:
            self.assertEqual(self.client.post(url, {'name': 'forbidden'}, HTTP_HOST=host).status_code, 403)

    def test_anonymous_legacy_catalogue_write_is_forbidden(self):
        self.assertEqual(self.client.post('/ims/inventory/api/products/', {'name': 'forbidden'}).status_code, 403)

    def test_conversion_is_post_only_and_idempotent(self):
        self.client.force_login(self.user)
        quotation = Quotation.objects.create(customer=self.customer)
        quotation.add_product_line(self.product, 1)
        url = reverse('ims:sales:quotation_to_invoice', args=[quotation.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(self.client.post(url).status_code, 302)
        self.assertEqual(Invoice.objects.filter(quotation=quotation).count(), 1)

    def test_stock_failure_rolls_back_added_invoice_line(self):
        self.client.force_login(self.user)
        result = self.client.post(reverse('ims:sales:invoice_edit', args=[self.invoice.pk]),
            {'add_line': '1', 'product': self.product.pk, 'quantity': '20', 'unit_price': '100'})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(self.invoice.lines.count(), 1)
        self.assertFalse(self.invoice.reservations.exists())

    def test_sales_status_queries_are_bounded_with_prefetch(self):
        from sales.invoice_status import get_invoice_status_context
        for _ in range(9):
            invoice = Invoice.objects.create(customer=self.customer)
            invoice.add_product_line(self.product, 1)
        with CaptureQueriesContext(connection) as before:
            for invoice in Invoice.objects.all():
                get_invoice_status_context(invoice)
        with CaptureQueriesContext(connection) as after:
            for invoice in Invoice.objects.prefetch_related('lines', 'payments'):
                get_invoice_status_context(invoice)
        self.assertEqual(len(before), 21)
        self.assertEqual(len(after), 3)


class ShopReliabilityTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(name='Laptop', sku='review-laptop', price=100, quantity=5)
        self.cart = Cart.objects.create(session_key='synthetic-cart')
        CartItem.objects.create(cart=self.cart, product=self.product, quantity=2)
        self.order = create_order_from_cart(self.cart, email='synthetic@example.invalid').order
        self.payment = ShopPayment.objects.create(order=self.order, poll_url='https://www.paynow.co.zw/poll/test')

    def test_checkout_consumes_cart_and_cannot_reserve_twice(self):
        with self.assertRaises(OrderCreationError):
            create_order_from_cart(self.cart, email='synthetic@example.invalid')
        self.assertEqual(Order.objects.count(), 1)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 2)

    def test_repeated_paid_and_late_failed_callbacks_do_not_change_stock_twice(self):
        mark_order_as_paid(self.order)
        mark_order_as_paid(self.order)
        mark_order_as_failed(self.order)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (3, 0))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PAID)

    def test_forged_callback_and_url_status_do_not_mark_paid(self):
        with patch('shop.views.paynow.poll_status', return_value={'status': 'unknown', 'raw': {}}):
            response = self.client.post(reverse('shop:paynow_result'), {'reference': self.order.number, 'status': 'paid'})
            self.assertEqual(response.status_code, 503)
            session = self.client.session
            session['shop_last_order'] = self.order.number
            session.save()
            self.client.get(reverse('shop:paynow_return'), {'status': 'paid'})
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, Order.Status.PENDING)

    def test_verified_callback_is_repeat_safe(self):
        payload = {'status': 'paid', 'raw': {'reference': self.order.number, 'amount': '200'}}
        with patch('shop.views.paynow.poll_status', return_value=payload):
            for _ in range(2):
                self.assertEqual(self.client.post(reverse('shop:paynow_result'),
                    {'reference': self.order.number, 'status': 'paid'}).status_code, 200)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (3, 0))


class PostgreSQLConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.customer = Customer.objects.create(name='Concurrent synthetic customer')
        self.product = Product.objects.create(name='Last unit', sku='last-unit', quantity=1, price=100)
        self.invoices = [Invoice.objects.create(customer=self.customer) for _ in range(2)]
        for invoice in self.invoices:
            invoice.add_product_line(self.product, 1)

    def race(self, actions):
        barrier = Barrier(len(actions))
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return action()
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=len(actions)) as executor:
            futures = [executor.submit(run, action) for action in actions]
            return [f.result(timeout=20) for f in futures]

    def test_two_staff_selling_last_unit_only_one_succeeds(self):
        self.assertEqual(connection.vendor, 'postgresql')
        def sell(invoice):
            try:
                StockService.finalize_sale(invoice)
                return 'sold'
            except ValueError:
                return 'unavailable'
        outcomes = self.race([lambda: sell(self.invoices[0]), lambda: sell(self.invoices[1])])
        self.assertCountEqual(outcomes, ['sold', 'unavailable'])
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.reserved), (0, 0))
        self.assertEqual(StockMovement.objects.count(), 1)

    def test_two_checkouts_for_last_unit_only_one_reserves(self):
        carts = [Cart.objects.create(session_key=f'race-{i}') for i in range(2)]
        for cart in carts:
            CartItem.objects.create(cart=cart, product=self.product, quantity=1)
        def checkout(cart):
            try:
                create_order_from_cart(cart, email='race@example.invalid')
                return 'reserved'
            except OrderCreationError:
                return 'unavailable'
        self.assertCountEqual(self.race([lambda: checkout(carts[0]), lambda: checkout(carts[1])]),
            ['reserved', 'unavailable'])
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 1)

    def test_unrelated_payments_get_distinct_journal_numbers(self):
        call_command('seed_chart_of_accounts', verbosity=0)
        user = get_user_model().objects.create_user('parallel-cashier')
        other_product = Product.objects.create(name='Other product', sku='other-race', quantity=1, price=100)
        self.invoices[1].lines.all().delete()
        self.invoices[1].add_product_line(other_product, 1)
        def pay(invoice):
            return record_payment(invoice_id=invoice.pk, amount=Decimal('100'),
                date=timezone.localdate(), method='Cash', note='', submission_key=uuid4(), user=user).pk
        ids = self.race([lambda: pay(self.invoices[0]), lambda: pay(self.invoices[1])])
        self.assertEqual(len(set(ids)), 2)
        numbers = list(JournalEntry.objects.values_list('number', flat=True))
        self.assertEqual(len(numbers), len(set(numbers)))

    def test_concurrent_same_payment_key_creates_one_receipt(self):
        call_command('seed_chart_of_accounts', verbosity=0)
        user = get_user_model().objects.create_user('concurrent-cashier')
        key = uuid4()
        def pay():
            return record_payment(invoice_id=self.invoices[0].pk, amount=Decimal('100'),
                date=timezone.localdate(), method='Cash', note='', submission_key=key, user=user).pk
        ids = self.race([pay, pay])
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Payment.objects.count(), 1)
        self.assertEqual(StockMovement.objects.count(), 1)


class ExpenseReliabilityTests(TestCase):
    def setUp(self):
        from accounting.models import ExpenseCategory, Account, Currency
        self.user = get_user_model().objects.create_user('expense-admin')
        self.user.groups.add(Group.objects.get_or_create(name='Admin')[0])
        self.client.force_login(self.user)
        category = ExpenseCategory.objects.create(name='Synthetic expense', default_account=Account.objects.get(code='5100', company=None))
        currency = Currency.objects.get(code='USD', company=None)
        self.data = {'date': timezone.localdate(), 'payee': 'Synthetic vendor', 'category': category.pk,
            'currency': currency.pk, 'amount': '25', 'fx_rate': '1', 'submission_key': str(uuid4())}
        self.url = reverse('ims:accounting:expense_create')

    def test_expense_retry_creates_one_record_and_one_journal(self):
        from accounting.models import Expense
        for _ in range(2):
            self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        self.assertEqual(Expense.objects.count(), 1)
        self.assertTrue(Expense.objects.get().posted)
        self.assertEqual(JournalEntry.objects.filter(source='EXPENSE').count(), 1)

    def test_expense_post_failure_does_not_save_unposted_record(self):
        from accounting.models import Expense
        with patch('accounting.views.post_expense', side_effect=ValueError('missing configuration')):
            response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Expense was not saved')
        self.assertFalse(Expense.objects.exists())

    def test_business_attachment_requires_authorization_and_is_private(self):
        from accounting.models import Expense
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.conf import settings
        from pathlib import Path
        upload = SimpleUploadedFile('synthetic-private.pdf', b'%PDF-1.4 synthetic', content_type='application/pdf')
        self.assertEqual(self.client.post(self.url, {**self.data, 'attachment': upload}).status_code, 302)
        field = Expense.objects.get().attachment
        self.addCleanup(field.delete, save=False)
        self.assertTrue(Path(field.path).is_relative_to(Path(settings.PRIVATE_MEDIA_ROOT)))
        response = self.client.get(field.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 synthetic")
        from django.urls import reverse
        for host, urlconf in [('ims.boforg.co.zw', 'config.host_urls.ims'), ('ai.boforg.co.zw', 'config.host_urls.ai')]:
            url = reverse('ims:private_document', kwargs={'name': field.name}, urlconf=urlconf)
            response = self.client.get(url, HTTP_HOST=host)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(b''.join(response.streaming_content), b'%PDF-1.4 synthetic')
        self.client.force_login(get_user_model().objects.create_user('private-outsider'))
        self.assertEqual(self.client.get(field.url).status_code, 404)
        self.client.logout()
        self.assertEqual(self.client.get(field.url).status_code, 302)


class PaynowTransportTests(TestCase):
    def test_sdk_adapter_bounds_network_and_retains_signature_verification(self):
        from payments.paynow import BoundedPaynowClient
        from paynow import HashMismatchException
        client = BoundedPaynowClient('synthetic-id', 'synthetic-key', '', '')
        payment = client.create_payment('synthetic-order', 'test@example.invalid')
        payment.add('Synthetic item', 10)
        with patch('payments.paynow.requests.post') as post:
            post.return_value.text = 'status=ok&hash=invalid'
            with self.assertRaises(HashMismatchException):
                client.send(payment)
            self.assertEqual(post.call_args.kwargs['timeout'], (5, 15))
            self.assertFalse(post.call_args.kwargs['allow_redirects'])

    def test_polling_rejects_non_provider_urls(self):
        from payments.paynow import poll_status
        with patch('payments.paynow.requests.post') as post:
            for url in ('http://www.paynow.co.zw/test', 'https://attacker.invalid/', 'http://127.0.0.1/admin/'):
                self.assertEqual(poll_status(url)['status'], 'unknown')
            post.assert_not_called()
