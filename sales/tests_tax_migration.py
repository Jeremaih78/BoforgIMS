from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from decimal import Decimal


class TaxMigrationTests(TransactionTestCase):
    def test_existing_agreed_totals_survive_and_new_documents_add_tax(self):
        before=[('sales','0011_invoice_custom_fields_invoice_reference_and_more')]
        after=MigrationExecutor(connection).loader.graph.leaf_nodes('sales')
        try:
            executor=MigrationExecutor(connection);executor.migrate(before)
            apps=executor.loader.project_state(before).apps
            customer=apps.get_model('customers','Customer').objects.create(name='Legacy tax customer')
            invoice=apps.get_model('sales','Invoice').objects.create(number='OLD-TAX-INVOICE',customer=customer)
            quote=apps.get_model('sales','Quotation').objects.create(number='OLD-TAX-QUOTE',customer=customer)
            Line=apps.get_model('sales','DocumentLine')
            for name,document in [('invoice',invoice),('quotation',quote)]:
                Line.objects.create(**{name:document},description='Agreed price',quantity=1,unit_price=100,line_total=100,tax_rate_percent=15)
            executor=MigrationExecutor(connection);executor.migrate(after)
            from sales.models import Invoice,Quotation
            from customers.models import Customer
            for model in [Invoice,Quotation]:
                original=model.objects.get(pk=invoice.pk if model is Invoice else quote.pk)
                self.assertFalse(original.tax_added);self.assertEqual(original.total,100)
                new=model.objects.create(customer=Customer.objects.get(pk=customer.pk))
                new.add_misc_line('Entered before tax',100,tax_rate_percent=15)
                self.assertTrue(new.tax_added);self.assertEqual(new.total,Decimal('115.00'))
        finally:
            MigrationExecutor(connection).migrate(after)
