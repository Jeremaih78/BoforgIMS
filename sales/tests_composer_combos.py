from decimal import Decimal
from uuid import uuid4
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from customers.models import Customer
from inventory.models import Category, Combo, ComboItem, Product
from sales.models import DocumentLine, Invoice, Quotation, StockReservation
from sales.services import StockService
from sales.services.payments import record_payment


class ComposerComboTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username='composer-combo-staff')
        self.user.groups.add(Group.objects.get_or_create(name='Staff')[0])
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(name='Combo studio')
        self.category = Category.objects.create(name='Composer kits')
        self.printer = Product.objects.create(name='Kit printer', sku='CP-PRN', category=self.category, price=100, quantity=20)
        self.press = Product.objects.create(name='Kit heat press', sku='CP-HP', category=self.category, price=250, quantity=10)
        self.combo = Combo.objects.create(name='Printer starter kit', code='starter-kit', discount_type='percent', discount_value=10)
        ComboItem.objects.create(combo=self.combo, product=self.printer, quantity=2)
        ComboItem.objects.create(combo=self.combo, product=self.press, quantity=1)

    def payload(self, **changes):
        data = {
            'customer':self.customer.pk, 'date':timezone.localdate().isoformat(), 'reference':'COMBO-ONE-SAVE',
            'lines-TOTAL_FORMS':'0', 'lines-INITIAL_FORMS':'0',
            'combos-TOTAL_FORMS':'1', 'combos-INITIAL_FORMS':'0',
            'combos-0-combo':self.combo.pk, 'combos-0-quantity':'2',
        }
        data.update(changes)
        return data

    def test_combo_only_quote_expands_components_and_percent_discount_without_reserving(self):
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        quote = Quotation.objects.get()
        self.assertRedirects(response, reverse('ims:sales:quotation_edit', args=[quote.pk]))
        self.assertEqual(quote.total, Decimal('810'))
        self.assertEqual(quote.lines.get(product=self.printer).quantity, 4)
        self.assertEqual(quote.lines.get(product=self.press).quantity, 2)
        self.assertEqual(quote.lines.get(product=None).line_total, Decimal('-90'))
        self.assertFalse(StockReservation.objects.exists())
        self.printer.refresh_from_db()
        self.assertEqual(self.printer.reserved, 0)

    def test_combo_only_invoice_reserves_every_component_in_one_save(self):
        self.client.post(reverse('ims:sales:invoice_create'), self.payload())
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, Decimal('810'))
        self.assertEqual(dict(invoice.reservations.values_list('product_id','quantity')), {self.printer.pk:4, self.press.pk:2})
        self.printer.refresh_from_db(); self.press.refresh_from_db()
        self.assertEqual((self.printer.reserved, self.press.reserved), (4,2))
        self.assertEqual((self.printer.quantity, self.press.quantity), (20,10))

    def test_fixed_discount_combo_and_direct_product_share_demand(self):
        self.combo.discount_type = Combo.DISCOUNT_FIXED
        self.combo.discount_value = 30
        self.combo.save()
        data = self.payload(**{'lines-TOTAL_FORMS':'1', 'lines-0-product':self.printer.pk,
                               'lines-0-quantity':'3', 'lines-0-unit_price':'95'})
        response = self.client.post(reverse('ims:sales:invoice_create'), data)
        self.assertEqual(response.status_code, 302)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, Decimal('1125'))
        self.assertEqual(invoice.lines.get(product=None).line_total, Decimal('-60'))
        self.assertEqual(invoice.reservations.get(product=self.printer).quantity, 7)

    def test_multiple_combos_with_shared_components_reserve_aggregate_demand(self):
        other = Combo.objects.create(name='Press package', code='press-package')
        ComboItem.objects.create(combo=other, product=self.press, quantity=2)
        data = self.payload(**{'combos-TOTAL_FORMS':'2', 'combos-1-combo':other.pk, 'combos-1-quantity':'1'})
        self.client.post(reverse('ims:sales:invoice_create'), data)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, Decimal('1310'))
        self.assertEqual(invoice.reservations.get(product=self.press).quantity, 4)

    def test_blank_optional_product_row_does_not_block_combo_only_save(self):
        data = self.payload(**{'lines-TOTAL_FORMS':'1', 'lines-0-product':'', 'lines-0-description':'',
                               'lines-0-quantity':'1', 'lines-0-unit_price':'', 'lines-0-tax_rate_percent':'0'})
        response = self.client.post(reverse('ims:sales:quotation_create'), data)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(Quotation.objects.get().total, Decimal('810'))

    def test_mixed_demand_stock_failure_rolls_back_header_lines_discounts_and_reservations(self):
        data = self.payload(**{'lines-TOTAL_FORMS':'1', 'lines-0-product':self.printer.pk,
                               'lines-0-quantity':'18', 'lines-0-unit_price':'100'})
        response = self.client.post(reverse('ims:sales:invoice_create'), data)
        self.assertContains(response, 'Insufficient stock')
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(DocumentLine.objects.exists())
        self.assertFalse(StockReservation.objects.exists())
        self.printer.refresh_from_db(); self.press.refresh_from_db()
        self.assertEqual((self.printer.reserved,self.press.reserved), (0,0))

    def test_conversion_copies_discounts_and_reserves_combo_stock_once(self):
        self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        quote = Quotation.objects.get()
        url = reverse('ims:sales:quotation_to_invoice', args=[quote.pk])
        self.client.post(url); self.client.post(url)
        invoice = Invoice.objects.get()
        self.assertEqual(invoice.total, Decimal('810'))
        self.assertEqual(list(invoice.lines.values_list('quantity','unit_price','line_total')), list(quote.lines.values_list('quantity','unit_price','line_total')))
        self.assertEqual(invoice.reservations.get(product=self.printer).quantity, 4)

    def test_combo_pdfs_render_component_and_discount_lines_without_product_lookup_errors(self):
        for kind, model in [('quotation',Quotation),('invoice',Invoice)]:
            self.client.post(reverse(f'ims:sales:{kind}_create'),self.payload())
            document=model.objects.get()
            with patch('sales.pdf_utils.render_pdf_from_html',return_value=b'%PDF-combo-preview') as renderer:
                response=self.client.get(reverse(f'ims:sales:{kind}_pdf',args=[document.pk]))
            self.assertEqual(response.status_code,200)
            html=renderer.call_args.args[0]
            for text in ['Kit printer','Kit heat press','Printer starter kit','discount','-90.00','810.00']:
                self.assertIn(text,html)

    def test_adding_combo_on_edit_preserves_existing_lines_and_does_not_repeat_it_on_plain_save(self):
        invoice = Invoice.objects.create(customer=self.customer)
        line = invoice.add_product_line(self.printer, 1)
        StockService.reserve_stock(invoice)
        data = self.payload(**{'lines-INITIAL_FORMS':'1', 'lines-TOTAL_FORMS':'1', 'lines-0-id':line.pk,
                               'lines-0-product':self.printer.pk, 'lines-0-quantity':'1', 'lines-0-unit_price':'100',
                               'lines-0-description':line.description, 'lines-0-tax_rate_percent':'0'})
        self.assertEqual(self.client.post(reverse('ims:sales:invoice_compose',args=[invoice.pk]), data).status_code, 302)
        self.assertEqual(invoice.total, Decimal('910'))
        product_lines = list(invoice.lines.filter(product__isnull=False).order_by('pk'))
        data = self.payload(**{'combos-TOTAL_FORMS':'0','lines-TOTAL_FORMS':str(len(product_lines)), 'lines-INITIAL_FORMS':str(len(product_lines))})
        for i, line in enumerate(product_lines):
            for field, value in {'id':line.pk, 'product':line.product_id, 'quantity':line.quantity, 'unit_price':line.unit_price, 'description':line.description, 'tax_rate_percent':line.tax_rate_percent}.items():
                data[f'lines-{i}-{field}'] = str(value)
        self.assertEqual(self.client.post(reverse('ims:sales:invoice_compose',args=[invoice.pk]),data).status_code,302)
        self.assertEqual(invoice.total, Decimal('910'))
        self.assertEqual(invoice.lines.count(), 4)
        self.assertEqual(invoice.reservations.get(product=self.printer).quantity, 5)

    def test_combo_price_cannot_be_overridden_with_hidden_posted_totals(self):
        self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-0-price':'1','combos-0-discount':'899','combos-0-unit_price':'0'}))
        self.assertEqual(Quotation.objects.get().total, Decimal('810'))

    def test_discount_rounding_matches_existing_per_combo_price(self):
        self.printer.price = Decimal('0.01'); self.printer.save()
        self.press.price = Decimal('0.03'); self.press.save()
        self.combo.discount_value = 50; self.combo.save()
        self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-0-quantity':'3'}))
        self.assertEqual(Quotation.objects.get().total, self.combo.compute_price()*3)

    def test_existing_product_can_be_replaced_with_combo_in_same_save(self):
        invoice = Invoice.objects.create(customer=self.customer)
        line = invoice.add_product_line(self.printer,19)
        StockService.reserve_stock(invoice)
        data = self.payload(**{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'1','lines-0-id':line.pk,'lines-0-DELETE':'on'})
        self.assertEqual(self.client.post(reverse('ims:sales:invoice_compose',args=[invoice.pk]),data).status_code,302)
        self.assertEqual(invoice.total, Decimal('810'))
        self.assertEqual(invoice.reservations.get(product=self.printer).quantity,4)

    def test_malformed_combo_management_fields_are_not_silently_ignored(self):
        data = self.payload()
        del data['combos-TOTAL_FORMS']; del data['combos-INITIAL_FORMS']
        response = self.client.post(reverse('ims:sales:quotation_create'),data)
        self.assertEqual(response.status_code,200)
        self.assertFalse(Quotation.objects.exists())

    def test_expanded_line_limit_is_checked_before_writing(self):
        response = self.client.post(reverse('ims:sales:quotation_create'),self.payload(**{
            'combos-TOTAL_FORMS':'34', **{f'combos-{i}-{key}':value for i in range(34) for key,value in [('combo',self.combo.pk),('quantity','1')]}}))
        self.assertContains(response,'100 expanded lines')
        self.assertFalse(Quotation.objects.exists())

    def test_empty_document_is_rejected(self):
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-TOTAL_FORMS':'0'}))
        self.assertContains(response, 'Add at least one product or combo')
        self.assertFalse(Quotation.objects.exists())

    def test_invalid_deleted_or_empty_combo_rows(self):
        for quantity in ['0','-1','1.5']:
            with self.subTest(quantity=quantity):
                response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-0-quantity':quantity}))
                self.assertEqual(response.status_code, 200)
                self.assertFalse(Quotation.objects.exists())
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-0-DELETE':'on'}))
        self.assertContains(response,'Add at least one product or combo')

    def test_inactive_or_empty_combos_are_rejected(self):
        empty = Combo.objects.create(name='Empty kit',code='empty-kit')
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'combos-0-combo':empty.pk}))
        self.assertContains(response,'at least one component')
        self.combo.is_active = False; self.combo.save()
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        self.assertEqual(response.status_code,200)
        self.assertFalse(Quotation.objects.exists())

    def test_mixed_currencies_and_inactive_components_are_rejected(self):
        self.press.currency = 'ZAR'; self.press.save()
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        self.assertContains(response,'multiple currencies')
        self.press.currency = 'USD'; self.press.is_active = False; self.press.save()
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload())
        self.assertContains(response,'inactive product')
        self.assertFalse(Quotation.objects.exists())

    def test_direct_product_and_foreign_combo_are_rejected(self):
        self.printer.currency = self.press.currency = 'ZAR'; self.printer.save(); self.press.save()
        other = Product.objects.create(name='USD service',sku='CP-USD',price=10,track_inventory=False)
        response = self.client.post(reverse('ims:sales:quotation_create'), self.payload(**{'lines-TOTAL_FORMS':'1','lines-0-product':other.pk,'lines-0-quantity':'1','lines-0-unit_price':'10'}))
        self.assertContains(response,'one currency')
        self.assertFalse(Quotation.objects.exists())

    def test_failed_edit_keeps_original_document_and_reservation(self):
        invoice = Invoice.objects.create(customer=self.customer,reference='Original')
        line = invoice.add_product_line(self.printer, 19)
        StockService.reserve_stock(invoice)
        data = self.payload(**{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'1','lines-0-id':line.pk,
                               'lines-0-product':self.printer.pk,'lines-0-quantity':'19','lines-0-unit_price':'100'})
        response = self.client.post(reverse('ims:sales:invoice_compose',args=[invoice.pk]), data)
        self.assertContains(response,'Insufficient stock')
        invoice.refresh_from_db()
        self.assertEqual(invoice.reference,'Original')
        self.assertEqual(invoice.lines.count(),1)
        self.assertEqual(invoice.reservations.get().quantity,19)

    def test_combo_stock_deducts_once_when_paid(self):
        from django.core.management import call_command
        call_command('seed_chart_of_accounts',verbosity=0)
        self.client.post(reverse('ims:sales:invoice_create'),self.payload())
        invoice = Invoice.objects.get()
        key=uuid4()
        args=dict(invoice_id=invoice.pk,amount=invoice.total,date=timezone.localdate(),method='Cash',note='',submission_key=key,user=self.user)
        record_payment(**args); record_payment(**args)
        self.printer.refresh_from_db(); self.press.refresh_from_db()
        self.assertEqual((self.printer.quantity,self.press.quantity),(16,8))
        self.assertEqual((self.printer.reserved,self.press.reserved),(0,0))

    def test_serial_component_still_requires_physical_unit_assignment(self):
        self.printer.tracking_mode = Product.TRACK_SERIAL; self.printer.save()
        self.client.post(reverse('ims:sales:invoice_create'),self.payload())
        invoice = Invoice.objects.get()
        with self.assertRaises(ValueError):
            StockService.finalize_sale(invoice)
        invoice.refresh_from_db()
        self.assertFalse(invoice.stock_finalized)

    def test_out_of_stock_combo_can_be_quoted_but_cannot_be_invoiced(self):
        self.printer.quantity=0; self.printer.save()
        self.assertEqual(self.client.post(reverse('ims:sales:quotation_create'),self.payload()).status_code,302)
        quote=Quotation.objects.get()
        response=self.client.post(reverse('ims:sales:invoice_create'),self.payload())
        self.assertContains(response,'Insufficient stock')
        self.client.post(reverse('ims:sales:quotation_to_invoice',args=[quote.pk]))
        quote.refresh_from_db()
        self.assertEqual(quote.status,Quotation.DRAFT)
        self.assertFalse(Invoice.objects.exists())
        self.assertFalse(StockReservation.objects.exists())

    def test_service_only_combos_do_not_create_stock_reservations(self):
        self.printer.track_inventory=False; self.printer.quantity=0; self.printer.save()
        self.press.track_inventory=False; self.press.quantity=0; self.press.save()
        response=self.client.post(reverse('ims:sales:invoice_create'),self.payload())
        self.assertEqual(response.status_code,302)
        self.assertEqual(Invoice.objects.get().total,Decimal('810'))
        self.assertFalse(StockReservation.objects.exists())

    def test_combo_search_matches_name_code_and_component_and_hides_invalid_combos(self):
        empty = Combo.objects.create(name='Empty kit',code='empty-search-kit')
        inactive = Combo.objects.create(name='Inactive kit',code='inactive-kit',is_active=False)
        for query in ['starter','starter-kit','CP-HP','Kit printer']:
            response = self.client.get(reverse('ims:sales:combo_search'),{'q':query})
            self.assertEqual(response.status_code,200)
            rows=response.json()['results']
            self.assertEqual([row['id'] for row in rows],[self.combo.pk])
            self.assertEqual(rows[0]['price'],'405.00')
            self.assertEqual(rows[0]['discount'],'45.00')
            self.assertEqual(rows[0]['available_stock'],10)
        self.press.currency='ZAR';self.press.save()
        self.assertEqual(self.client.get(reverse('ims:sales:combo_search')).json()['results'],[])

    def test_search_and_create_still_require_permissions(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('ims:sales:combo_search')).status_code,302)
        user=get_user_model().objects.create_user(username='combo-outsider')
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('ims:sales:combo_search')).status_code,403)
        self.assertEqual(self.client.post(reverse('ims:sales:invoice_create'),self.payload()).status_code,403)
