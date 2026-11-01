from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from inventory.models import Category, Product, Shipment, ShipmentItem, Supplier
from sales.models import Invoice, Payment, Quotation
from users.dashboard import dashboard_context


class DashboardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.admin = get_user_model().objects.create_user(username='dashboard-admin')
        cls.admin.groups.add(Group.objects.get_or_create(name='Admin')[0])
        cls.staff = get_user_model().objects.create_user(username='dashboard-staff')
        cls.staff.groups.add(Group.objects.get_or_create(name='Staff')[0])
        cls.reader = get_user_model().objects.create_user(username='invoice-reader')
        cls.reader.user_permissions.add(Permission.objects.get(codename='view_invoice', content_type__app_label='sales'))
        cls.customer = Customer.objects.create(name='Dashboard Print Studio', phone='+263 771234567')
        cls.category = Category.objects.create(name='Dashboard supplies')
        cls.product = Product.objects.create(name='Dashboard heat press', sku='DASH-HP', category=cls.category, price=100, track_inventory=False)

    def setUp(self):
        self.client.force_login(self.admin)
        self.today = timezone.localdate()

    def invoice(self, amount='100', **values):
        invoice = Invoice.objects.create(customer=self.customer, **values)
        invoice.add_product_line(self.product, 1, Decimal(amount))
        return invoice

    def test_dashboard_sums_lines_and_payments_separately_and_uses_payment_dates(self):
        inv = self.invoice('200', due_date=self.today-timedelta(days=1))
        inv.add_product_line(self.product, 1, 50)
        previous = self.invoice('300', date=self.today.replace(day=1)-timedelta(days=1), due_date=self.today-timedelta(days=1))
        self.invoice('500', status=Invoice.PAID)
        self.invoice('999', status='VOID')
        self.invoice('888', date=self.today+timedelta(days=1))
        Payment.objects.bulk_create([Payment(invoice=inv, amount=50, date=self.today), Payment(invoice=inv, amount=25, date=self.today), Payment(invoice=previous, amount=60, date=self.today)])
        context = dashboard_context(self.admin, {})
        self.assertEqual(context['invoiced_amount'], Decimal('750'))
        self.assertEqual(context['received_amount'], Decimal('135'))
        self.assertEqual(context['open_amount'], Decimal('415'))
        self.assertEqual(context['overdue_count'], 2)
        self.assertEqual(context['invoice_count'], 2)

    def test_overdue_is_computed_from_due_date_and_net_balance(self):
        inv = self.invoice(due_date=self.today-timedelta(days=1))
        Payment.objects.bulk_create([Payment(invoice=inv, amount=40, date=self.today)])
        today = self.invoice(due_date=self.today)
        self.invoice(status=Invoice.PAID, due_date=self.today-timedelta(days=2))
        settled = self.invoice(due_date=self.today-timedelta(days=1))
        Payment.objects.bulk_create([Payment(invoice=settled, amount=100, date=self.today)])
        context = dashboard_context(self.admin, {})
        self.assertEqual(context['overdue_count'], 1)
        self.assertEqual(context['overdue_amount'], Decimal('60'))
        self.assertEqual(context['due_today_count'], 1)
        self.assertEqual(context['open_count'], 2)

    def test_low_stock_uses_active_tracked_available_units_and_reorder_levels(self):
        low = Product.objects.create(name='Reserved stock', sku='DASH-LOW', category=self.category, price=10, quantity=12, reserved=10, reorder_level=3)
        Product.objects.create(name='Small but enough', sku='DASH-OK', category=self.category, price=10, quantity=2, reorder_level=0)
        Product.objects.create(name='Inactive stock', sku='DASH-OFF', category=self.category, price=10, quantity=0, is_active=False)
        Product.objects.create(name='Service', sku='DASH-SVC', category=self.category, price=10, quantity=0, track_inventory=False)
        context = dashboard_context(self.admin, {})
        self.assertEqual(context['low_stock_count'], 1)
        self.assertEqual(context['low_products'][0].pk, low.pk)
        response = self.client.get(reverse('ims:inventory:product_list'), {'low':'1'})
        self.assertEqual([p.pk for p in response.context['products']], [low.pk])

    def test_currencies_are_separate_and_mixed_documents_are_flagged(self):
        self.invoice('100')
        zar = Product.objects.create(name='ZAR item', sku='DASH-ZAR', category=self.category, price=500, currency='ZAR', track_inventory=False)
        foreign = Invoice.objects.create(customer=self.customer)
        foreign.add_product_line(zar, 1)
        mixed = self.invoice('90')
        mixed.add_product_line(zar, 1)
        usd = dashboard_context(self.admin, {})
        self.assertEqual(usd['invoiced_amount'], Decimal('100'))
        self.assertEqual(usd['mixed_currency_count'], 1)
        self.assertEqual(dashboard_context(self.admin, {'currency':'ZAR'})['invoiced_amount'], Decimal('500'))

    def test_period_limits_sales_but_keeps_old_unpaid_priorities(self):
        self.invoice('100', date=self.today-timedelta(days=2), due_date=self.today-timedelta(days=1))
        self.invoice('50')
        context = dashboard_context(self.admin, {'period':'today'})
        self.assertEqual(context['invoiced_amount'], Decimal('50'))
        self.assertEqual(context['overdue_count'], 1)
        self.assertEqual(context['open_amount'], Decimal('150'))

    def test_converted_drafts_are_not_offered_for_continuation(self):
        draft = Quotation.objects.create(customer=self.customer)
        converted = Quotation.objects.create(customer=self.customer)
        Invoice.objects.create(customer=self.customer, quotation=converted)
        context = dashboard_context(self.admin, {})
        self.assertEqual(context['draft_count'], 1)
        self.assertEqual([q.pk for q in context['draft_quotes']], [draft.pk])

    def test_shipment_followup_matches_drilldown(self):
        supplier = Supplier.objects.create(name='Dashboard supplier')
        late = Shipment.objects.create(name='Late shipment', supplier=supplier, status=Shipment.STATUS_IN_TRANSIT, eta_date=self.today-timedelta(days=2))
        ready = Shipment.objects.create(name='Ready shipment', supplier=supplier, status=Shipment.STATUS_ARRIVED)
        done = Shipment.objects.create(name='Received shipment', supplier=supplier, status=Shipment.STATUS_RECEIVED)
        ShipmentItem.objects.create(shipment=ready, product=self.product, quantity_expected=5, quantity_received=2, unit_purchase_price=50)
        ShipmentItem.objects.create(shipment=done, product=self.product, quantity_expected=5, quantity_received=5, unit_purchase_price=50)
        context = dashboard_context(self.admin, {})
        self.assertEqual(context['shipment_count'], 2)
        response = self.client.get(reverse('ims:inventory:shipment_list'), {'attention':'1'})
        self.assertEqual({s.pk for s in response.context['shipments']}, {late.pk, ready.pk})

    def test_invoice_reader_gets_only_permitted_sections_and_search_results(self):
        inv = self.invoice()
        Quotation.objects.create(customer=self.customer)
        self.client.force_login(self.reader)
        response = self.client.get(reverse('ims:dashboard'))
        self.assertEqual(response.status_code, 200)
        for text in ['Stock to replenish', 'Continue a quotation', 'Shipment follow-up', 'New invoice', 'Price list PDF', 'Payments received', 'Credit control']:
            self.assertNotContains(response, text)
        results = self.client.get(reverse('ims:dashboard_lookup'), {'q':'Dashboard'}).json()['results']
        self.assertTrue(results)
        self.assertEqual({row['kind'] for row in results}, {'Invoice'})
        self.assertEqual(results[0]['url'], reverse('ims:sales:invoice_edit', args=[inv.pk]))

    def test_staff_does_not_see_admin_shipments_finance_or_stock_adjustment(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse('ims:dashboard'))
        self.assertContains(response, 'New quotation')
        self.assertContains(response, 'Stock to replenish')
        for text in ['Shipment follow-up', '> Accounting', '> Credit control', 'Stock in / out']:
            self.assertNotContains(response, text)

    def test_search_supports_sku_phone_and_document_reference(self):
        inv = self.invoice(reference='DASH-PO-123')
        for query, kind in [('DASH-HP', 'Product'), ('771234567','Customer'), ('DASH-PO-123','Invoice')]:
            response = self.client.get(reverse('ims:dashboard_lookup'), {'q':query})
            self.assertEqual(response.status_code, 200)
            self.assertIn(kind, {row['kind'] for row in response.json()['results']})
        response = self.client.get(reverse('ims:dashboard'), {'q':'DASH-PO-123'})
        self.assertContains(response, inv.number)

    def test_search_matches_phone_numbers_despite_spacing_and_punctuation(self):
        customer = Customer.objects.create(name='Formatted phone customer', phone='+263 (77) 000-1234')
        Invoice.objects.create(customer=customer)
        response = self.client.get(reverse('ims:dashboard_lookup'), {'q':'770001234'})
        self.assertEqual({row['kind'] for row in response.json()['results']}, {'Customer','Invoice'})

    def test_dashboard_drilldowns_match_overdue_and_received_records(self):
        overdue = self.invoice(due_date=self.today-timedelta(days=1))
        due = self.invoice(due_date=self.today)
        context = dashboard_context(self.admin, {'period':'today'})
        response = self.client.get(context['overdue_url'])
        self.assertEqual([i.pk for i in response.context['invoices']], [overdue.pk])
        Payment.objects.bulk_create([Payment(invoice=due, amount=20, date=self.today)])
        response = self.client.get(context['received_url'])
        self.assertEqual([i.pk for i in response.context['invoices']], [due.pk])

    def test_invoiced_drilldown_excludes_void_future_and_foreign_invoices(self):
        invoice = self.invoice()
        self.invoice(status='VOID')
        self.invoice(date=self.today+timedelta(days=1))
        context = dashboard_context(self.admin, {})
        response = self.client.get(context['invoiced_url'])
        self.assertEqual([i.pk for i in response.context['invoices']], [invoice.pk])

    def test_dashboard_is_read_only_and_queries_are_bounded(self):
        for _ in range(20):
            self.invoice()
        with CaptureQueriesContext(connection) as queries:
            dashboard_context(self.admin, {})
        self.assertLess(len(queries), 55)
        self.assertFalse(any(q['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for q in queries))

    def test_anonymous_and_unprivileged_access_is_rejected(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ims:dashboard')).status_code, 302)
        outsider = get_user_model().objects.create_user(username='dashboard-outsider')
        self.client.force_login(outsider)
        self.assertEqual(self.client.get(reverse('ims:dashboard')).status_code, 403)
        self.assertEqual(self.client.get(reverse('ims:dashboard_lookup'), {'q':'Dashboard'}).status_code, 403)

    @override_settings(ROOT_URLCONF='config.host_urls.ims')
    def test_dashboard_search_is_registered_on_ims_hostname(self):
        self.assertEqual(self.client.get('/search/', {'q':'DASH-HP'}, HTTP_HOST='ims.boforg.co.zw').status_code, 200)
