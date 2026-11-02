from datetime import timedelta
from decimal import Decimal
from io import StringIO
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase,override_settings,Client
from django.contrib.auth import get_user_model
from django.core.management import call_command,CommandError
from django.urls import reverse
from django.utils import timezone
from django.db import close_old_connections
from django.core.cache import cache
from accounting.models import Currency,TaxRate,Company,JournalEntry,JournalLine,Account
from customers.models import Customer
from inventory.models import Product
from sales.models import Invoice,Quotation,DocumentSubmission
from sales.services.document_saves import edit_signature
from sales.services.integrity import integrity_report
from core.query import csv_cell


class WholeIMSRegressionTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=get_user_model().objects.create_superuser('whole-ims-audit','audit@example.test','test')
        cls.customer=Customer.objects.create(name='Audit customer')
        cls.product=Product.objects.create(name='Audit stock',sku='WHOLE-AUDIT',quantity=20,price=100)

    def setUp(self):self.client.force_login(self.user)

    def payload(self,**extra):
        data={'submission_key':str(uuid4()),'customer':self.customer.pk,'date':timezone.localdate().isoformat(),
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-product':self.product.pk,
            'lines-0-quantity':'1','lines-0-unit_price':'100','lines-0-tax_rate_percent':'0',
            'combos-TOTAL_FORMS':'0','combos-INITIAL_FORMS':'0'}
        data.update(extra);return data

    def test_retried_document_saves_do_not_duplicate_documents_or_reservations(self):
        for kind,model in [('invoice',Invoice),('quotation',Quotation)]:
            data=self.payload();url=reverse('ims:sales:'+kind+'_create')
            first=self.client.post(url,data);second=self.client.post(url,data)
            self.assertEqual((first.status_code,second.status_code),(302,302))
            self.assertEqual(first.url,second.url);self.assertEqual(model.objects.count(),1)
        self.product.refresh_from_db();self.assertEqual(self.product.reserved,1)
        self.assertEqual(DocumentSubmission.objects.count(),2)

    def test_reusing_document_key_for_different_contents_is_rejected(self):
        data=self.payload();url=reverse('ims:sales:invoice_create');self.client.post(url,data)
        response=self.client.post(url,{**data,'lines-0-quantity':'2'})
        self.assertContains(response,'different document details');self.assertEqual(Invoice.objects.count(),1)
        self.product.refresh_from_db();self.assertEqual(self.product.reserved,1)

    def test_stale_editor_cannot_overwrite_a_more_recent_save(self):
        invoice=Invoice.objects.create(customer=self.customer);line=invoice.add_product_line(self.product,1)
        data=self.payload(**{'lines-INITIAL_FORMS':'1','lines-0-id':str(line.pk),'document_signature':edit_signature(invoice),'reference':'First editor'})
        url=reverse('ims:sales:invoice_compose',args=[invoice.pk])
        self.assertEqual(self.client.post(url,data).status_code,302)
        response=self.client.post(url,{**data,'submission_key':str(uuid4()),'reference':'Stale editor'})
        self.assertContains(response,'changed in another window')
        invoice.refresh_from_db();self.assertEqual(invoice.reference,'First editor')
        self.assertEqual(self.client.post(url,data).status_code,302)

    def test_failed_document_save_releases_the_submission_for_a_safe_retry(self):
        data=self.payload(**{'lines-0-quantity':'21'});url=reverse('ims:sales:invoice_create')
        self.assertEqual(self.client.post(url,data).status_code,200)
        self.assertFalse(Invoice.objects.exists());self.assertFalse(DocumentSubmission.objects.exists())
        self.assertEqual(self.client.post(url,{**data,'lines-0-quantity':'1'}).status_code,302)

    def test_malformed_dates_and_finance_ids_return_400_instead_of_500(self):
        cases=[('accounting:accounting_dashboard',{'from':'2026-02-31'}),('accounting:expense_list',{'to':'garbage'}),
            ('accounting:finance_cashflow',{'account':'not-an-id'}),('accounting:finance_expenses',{'customer':'9'*100}),
            ('credit_control:dashboard',{'as_of':'2026-13-02'}),('credit_control:aging_reports',{'as_of':'2026-02-31'})]
        for name,data in cases:self.assertEqual(self.client.get(reverse('ims:'+name),data).status_code,400,name)

    def test_csv_export_quotes_formula_cells_but_preserves_money_numbers(self):
        self.customer.name='=HYPERLINK("example")';self.customer.save()
        invoice=Invoice.objects.create(customer=self.customer);invoice.add_product_line(self.product,1)
        response=self.client.get(reverse('ims:credit_control:report_export_csv',args=['top_debtors']))
        self.assertContains(response,"'=HYPERLINK")
        self.assertEqual(csv_cell(Decimal('-25.00')),Decimal('-25.00'))

    def test_currency_and_tax_defaults_are_isolated_per_company(self):
        base=Currency.objects.get(company=None,code='USD');base.is_base=True;base.save()
        tax=TaxRate.objects.filter(company=None).first();tax.is_default=True;tax.save()
        company=Company.objects.create(name='Separate organization')
        Currency.objects.create(company=company,code='USD',name='Separate base',is_base=True)
        TaxRate.objects.create(company=company,name='Separate VAT',rate=10,is_default=True)
        base.refresh_from_db();tax.refresh_from_db();self.assertTrue(base.is_base);self.assertTrue(tax.is_default)

    def test_reconciliation_detects_unbalanced_journals_and_posted_tax_mismatch(self):
        invoice=Invoice.objects.create(customer=self.customer);invoice.add_product_line(self.product,1)
        entry=JournalEntry.objects.create(currency=Currency.objects.get(company=None,code='USD'),is_posted=True,source='INVOICE',source_id=invoice.pk)
        JournalLine.objects.create(entry=entry,account=Account.objects.get(company=None,code='1200'),debit=115,debit_base=115)
        JournalLine.objects.create(entry=entry,account=Account.objects.get(company=None,code='4000'),credit=100,credit_base=100)
        report=integrity_report();self.assertEqual(len(report['unbalanced_posted_journals']),1)
        self.assertEqual(len(report['invoice_ledger_total_mismatches']),1)
        with self.assertRaises(CommandError):call_command('check_ims_integrity','--strict',stdout=StringIO())

    def test_health_probes_are_read_only_and_readiness_fails_on_database_loss(self):
        cache.clear();before=JournalEntry.objects.count()
        self.assertEqual(self.client.get('/health/live/').status_code,200)
        self.assertEqual(self.client.get('/health/ready/').status_code,200)
        with patch('core.health.connection.cursor',side_effect=RuntimeError('private database details')):
            response=self.client.get('/health/ready/')
        self.assertEqual(response.status_code,503);self.assertNotIn('private database details',response.content.decode())
        self.assertEqual(JournalEntry.objects.count(),before)

    @override_settings(DEBUG=False)
    def test_production_error_page_does_not_need_database_context(self):
        with patch('core.health.connection.cursor',side_effect=RuntimeError('database unavailable')):
            response=self.client.get('/not-a-real-page/')
        self.assertEqual(response.status_code,404);self.assertContains(response,'Boforg IMS',status_code=404)
        self.assertIn('X-Request-ID',response)

    def test_main_shell_uses_local_navigation_assets(self):
        response=self.client.get(reverse('ims:accounting:accounting_dashboard'))
        self.assertNotContains(response,'cdn.jsdelivr.net');self.assertContains(response,'tasker/vendor/bootstrap/js/bootstrap.bundle.min.js')


class DocumentSaveConcurrencyTests(TransactionTestCase):
    def test_same_submission_in_two_requests_creates_one_invoice(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        call_command('seed_chart_of_accounts',stdout=StringIO())
        user=get_user_model().objects.create_superuser('submission-race','race@example.test','test')
        customer=Customer.objects.create(name='Race customer');product=Product.objects.create(name='Race stock',sku='SUBMISSION-RACE',price=100,quantity=5)
        data={'submission_key':str(uuid4()),'customer':customer.pk,'date':timezone.localdate().isoformat(),
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-product':product.pk,'lines-0-quantity':'1','lines-0-unit_price':'100',
            'combos-TOTAL_FORMS':'0','combos-INITIAL_FORMS':'0'}
        barrier=Barrier(2);url=reverse('ims:sales:invoice_create')
        def save():
            close_old_connections()
            try:
                client=Client();client.force_login(get_user_model().objects.get(pk=user.pk));barrier.wait(timeout=10)
                response=client.post(url,data);return response.status_code,getattr(response,'url','')
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(save) for _ in range(2)];responses=[f.result(timeout=30) for f in futures]
        self.assertEqual(responses[0],responses[1]);self.assertEqual(responses[0][0],302)
        self.assertEqual(Invoice.objects.count(),1);product.refresh_from_db();self.assertEqual(product.reserved,1)
