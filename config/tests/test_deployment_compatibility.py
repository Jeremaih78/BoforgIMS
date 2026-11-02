"""The prior release can still insert records after the additive deployment."""
from django.test import TestCase
from django.db import connection
from accounting.models import BankAccount,Currency,Expense,ExpenseCategory,Account,SupplierBill
from customers.models import Customer
from inventory.models import Supplier
from sales.models import Invoice,Quotation


class PreviousReleaseInsertTests(TestCase):
    def old_insert(self,obj,omitted,unique_name,replacement):
        fields=[f.column for f in obj._meta.local_fields if not f.primary_key and f.name not in omitted]
        quote=connection.ops.quote_name
        values=[('%s' if name==unique_name else quote(name)) for name in fields]
        sql='INSERT INTO '+quote(obj._meta.db_table)+' ('+','.join(map(quote,fields))+') SELECT '+','.join(values)+' FROM '+quote(obj._meta.db_table)+' WHERE id=%s RETURNING id'
        with connection.cursor() as cursor:
            cursor.execute(sql,[replacement,obj.pk]);pk=cursor.fetchone()[0]
        return type(obj).objects.get(pk=pk)

    def test_prior_invoice_and_quotation_inserts_keep_the_legacy_tax_convention(self):
        customer=Customer.objects.create(name='Deployment compatibility customer')
        for model in [Invoice,Quotation]:
            current=model.objects.create(customer=customer)
            previous=self.old_insert(current,{'tax_added'},'number','LEGACY-'+model.__name__)
            self.assertFalse(previous.tax_added)
            self.assertTrue(current.tax_added)

    def test_prior_bank_expense_and_bill_inserts_have_safe_database_defaults(self):
        currency=Currency.objects.get(company=None,code='USD')
        bank=BankAccount.objects.filter(company=None,currency=currency).first()
        previous=self.old_insert(bank,{'kind','is_active'},'name','Compatibility bank')
        self.assertTrue(previous.is_active);self.assertEqual(previous.kind,'BANK')
        category=ExpenseCategory.objects.create(name='Compatibility',default_account=Account.objects.get(company=None,code='5100'))
        expense=Expense.objects.create(payee='Compatibility',category=category,currency=currency,amount=10)
        previous=self.old_insert(expense,{'reference','bank','created_by'},'doc_no','LEGACY-EXP')
        self.assertEqual(previous.reference,'')
        bill=SupplierBill.objects.create(supplier=Supplier.objects.create(name='Compatibility'),currency=currency,total=10)
        previous=self.old_insert(bill,{'reference','submission_key','created_by'},'doc_no','LEGACY-BILL')
        self.assertEqual(previous.reference,'')
