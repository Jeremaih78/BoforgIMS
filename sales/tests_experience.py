from decimal import Decimal
from io import BytesIO
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from pypdf import PdfReader

from customers.models import Customer
from inventory.models import Category, Product
from sales.forms import InvoiceForm
from sales.models import DocumentLine, Invoice, Quotation


class ExperienceFixture(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='experience-staff')
        self.user.groups.add(Group.objects.get_or_create(name='Staff')[0])
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Print Studio', phone='+263 770000000')
        self.category = Category.objects.create(name='Printing')
        self.product = Product.objects.create(name='Heat press', sku='EX-HP', category=self.category, price=Decimal('100.00'), quantity=10)

    def payload(self, **extra):
        data = {'customer': self.customer.pk, 'date': '2026-10-30', 'notes': 'Deliver before noon',
                'reference': 'PO-42', 'terms': 'Collection on payment', 'custom_fields': '{"Warranty":"12 months"}',
                'lines-TOTAL_FORMS': '1', 'lines-INITIAL_FORMS': '0',
                'lines-0-product': self.product.pk, 'lines-0-description': 'Custom printed description',
                'lines-0-quantity': '2', 'lines-0-unit_price': '95', 'lines-0-tax_rate_percent': '0'}
        data.update(extra)
        return data


class SalesExperienceTests(ExperienceFixture):
    def test_single_submit_creates_invoice_and_reserves_stock(self):
        response = self.client.post(reverse('ims:sales:invoice_create'), self.payload())
        invoice = Invoice.objects.get()
        self.assertRedirects(response, reverse('ims:sales:invoice_edit', args=[invoice.pk]))
        self.assertEqual(invoice.total, Decimal('190.00'))
        self.assertEqual(invoice.reference, 'PO-42')
        self.assertEqual(invoice.custom_fields, {'Warranty': '12 months'})
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 2)

    def test_stock_error_rolls_back_whole_document(self):
        response = self.client.post(reverse('ims:sales:invoice_create'), self.payload(**{'lines-0-quantity': '99'}))
        self.assertContains(response, 'Insufficient stock')
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(DocumentLine.objects.exists())
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 0)

    def test_quote_conversion_preserves_customization(self):
        self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        quote = Quotation.objects.get()
        self.assertEqual(quote.total, Decimal('190'))
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 0)
        self.client.post(reverse('ims:sales:quotation_to_invoice', args=[quote.pk]))
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.custom_fields, quote.custom_fields)
        self.assertEqual(invoice.reference, quote.reference)
        self.assertEqual(invoice.terms, quote.terms)

    def test_batch_edit_recalculates_lines_and_reservations(self):
        self.client.post(reverse('ims:sales:invoice_create'), self.payload())
        invoice = Invoice.objects.get()
        line = invoice.lines.get()
        data = self.payload(**{'lines-INITIAL_FORMS': '1', 'lines-0-id': line.pk, 'lines-0-quantity': '3'})
        response = self.client.post(reverse('ims:sales:invoice_compose', args=[invoice.pk]), data)
        self.assertEqual(response.status_code, 302)
        invoice.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(invoice.total, Decimal('285.00'))
        self.assertEqual(self.product.reserved, 3)

    def test_finalized_document_cannot_be_batch_edited(self):
        invoice = Invoice.objects.create(customer=self.customer, stock_finalized=True)
        response = self.client.post(reverse('ims:sales:invoice_compose', args=[invoice.pk]), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(invoice.lines.exists())

    def test_foreign_line_ids_cannot_be_edited(self):
        other = Invoice.objects.create(customer=self.customer)
        line = other.add_product_line(self.product, 1)
        invoice = Invoice.objects.create(customer=self.customer)
        invoice.add_product_line(self.product, 1)
        response = self.client.post(reverse('ims:sales:invoice_compose', args=[invoice.pk]), self.payload(**{'lines-INITIAL_FORMS': '1', 'lines-0-id': line.pk}))
        self.assertEqual(response.status_code, 200)
        line.refresh_from_db()
        self.assertEqual(line.quantity, 1)

    def test_bad_second_line_creates_nothing(self):
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{
            'lines-TOTAL_FORMS': '2', 'lines-1-product': self.product.pk, 'lines-1-quantity': '-2', 'lines-1-unit_price': '100'}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Quotation.objects.exists())

    def test_due_date_and_custom_field_validation(self):
        for extra in ({'due_date':'2026-10-01'}, {'custom_fields':'["invalid"]'}, {'custom_fields':'{"Test":12}'}, {'custom_fields':'{" Test":"a","Test":"b"}'}):
            form = InvoiceForm(self.payload(**extra))
            self.assertFalse(form.is_valid())

    def test_pdf_uses_edited_description_totals_and_custom_fields(self):
        self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        quote = Quotation.objects.get()
        # Render the real templates, isolating native PDF renderer dependencies here.
        with patch('sales.pdf_utils.render_pdf_from_html', return_value=b'%PDF-preview') as renderer:
            self.client.get(reverse('ims:sales:quotation_pdf', args=[quote.pk]))
        html = renderer.call_args.args[0]
        for text in ['Custom printed description', '190.00', 'PO-42', 'Warranty', '12 months', 'Collection on payment']:
            self.assertIn(text, html)

    def test_anonymous_and_unprivileged_users_cannot_author(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ims:sales:invoice_create')).status_code, 302)
        viewer = get_user_model().objects.create_user(username='experience-viewer')
        self.client.force_login(viewer)
        self.assertEqual(self.client.post(reverse('ims:sales:invoice_create'), self.payload()).status_code, 403)

    def test_batch_delete_releases_reservation_and_keeps_remaining_line(self):
        self.client.post(reverse('ims:sales:invoice_create'), self.payload())
        invoice = Invoice.objects.get()
        line = invoice.lines.get()
        response = self.client.post(reverse('ims:sales:invoice_compose', args=[invoice.pk]), self.payload(**{
            'lines-INITIAL_FORMS':'1', 'lines-TOTAL_FORMS':'2', 'lines-0-id':line.pk, 'lines-0-DELETE':'on',
            'lines-1-product':self.product.pk, 'lines-1-quantity':'1', 'lines-1-unit_price':'90'}))
        self.assertEqual(response.status_code, 302)
        self.product.refresh_from_db()
        self.assertEqual(self.product.reserved, 1)
        self.assertEqual(invoice.lines.count(), 1)
        self.assertEqual(invoice.total, Decimal('90'))

    def test_failed_batch_edit_preserves_old_lines_and_header(self):
        self.client.post(reverse('ims:sales:invoice_create'), self.payload())
        invoice = Invoice.objects.get()
        response = self.client.post(reverse('ims:sales:invoice_compose', args=[invoice.pk]), self.payload(**{
            'reference':'Changed', 'lines-INITIAL_FORMS':'1', 'lines-0-id':invoice.lines.get().pk, 'lines-0-quantity':'99'}))
        self.assertEqual(response.status_code, 200)
        invoice.refresh_from_db(); self.product.refresh_from_db()
        self.assertEqual(invoice.reference, 'PO-42')
        self.assertEqual(invoice.total, Decimal('190'))
        self.assertEqual(self.product.reserved, 2)

    def test_save_pdf_returns_to_saved_document_before_download(self):
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(save_action='pdf'))
        quote = Quotation.objects.get()
        self.assertRedirects(response, reverse('ims:sales:quotation_edit', args=[quote.pk])+'?download=1')

    def test_mixed_currency_document_is_rejected(self):
        other = Product.objects.create(name='Other currency', sku='OTHER', category=self.category, currency='ZAR', price=10)
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{
            'lines-TOTAL_FORMS':'2', 'lines-1-product':other.pk, 'lines-1-quantity':'1', 'lines-1-unit_price':'10'}))
        self.assertContains(response, 'one currency')
        self.assertFalse(Quotation.objects.exists())

    def test_converted_quote_stays_locked_even_if_status_is_stale(self):
        quote = Quotation.objects.create(customer=self.customer)
        Invoice.objects.create(customer=self.customer, quotation=quote)
        response = self.client.post(reverse('ims:sales:quotation_compose', args=[quote.pk]), self.payload())
        self.assertEqual(response.status_code, 302)
        self.assertFalse(quote.lines.exists())


class PriceListTests(ExperienceFixture):
    def test_price_list_is_real_download_and_excludes_internal_information(self):
        self.product.avg_cost = Decimal('47.77'); self.product.save()
        Product.objects.create(name='Hidden item', sku='HIDDEN', category=self.category, price=1, is_active=False)
        response = self.client.get(reverse('ims:inventory:product_price_list'))
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertIn('attachment;', response['Content-Disposition'])
        text = '\n'.join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)
        for expected in ['Heat press', 'EX-HP', 'USD 100.00']:
            self.assertIn(expected, text)
        self.assertNotIn('47.77', text)
        self.assertNotIn('Hidden item', text)

    def test_export_filters_all_pages_and_handles_empty_results(self):
        Product.objects.bulk_create([Product(name=f'Product {i}', sku=f'EX-{i}', category=self.category, price=10) for i in range(35)])
        response = self.client.get(reverse('ims:inventory:product_price_list'), {'q':'Product'})
        reader = PdfReader(BytesIO(response.content))
        text = '\n'.join(page.extract_text() for page in reader.pages)
        self.assertGreater(len(reader.pages), 1)
        self.assertIn('EX-34', text)
        self.assertNotIn('Heat press', text)
        response = self.client.get(reverse('ims:inventory:product_price_list'), {'q':'nothing matches'})
        self.assertIn('No active products', PdfReader(BytesIO(response.content)).pages[0].extract_text())
