from datetime import timedelta
from decimal import Decimal as D
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db.models import Sum
from django.urls import reverse
from django.utils import timezone
from accounting.models import Account,BankAccount,Currency,ExpenseCategory,Expense,JournalEntry,JournalLine,TaxRate,SupplierBill,APPayment,MoneyMovement,FiscalPeriod,Company,ARPayment
from accounting.services.money import create_money_account,record_movement,record_supplier_bill,record_supplier_payment,post_entry
from accounting.services.posting import post_expense
from accounting.services.workspace import money_workspace
from accounting.services.finance_dashboard import Period,get_cash_in,get_cash_out,cashflow_trend,get_total_expenses
from credit_control.services.balances import get_customer_outstanding,get_supplier_outstanding,bill_outstanding_balance
from credit_control.models import DebtorAccount,CreditorAccount
from customers.models import Customer
from inventory.models import Supplier,Product,Category,Combo,ComboItem
from inventory.services.combos import add_combo_to_document
from sales.models import Invoice,Quotation,Payment
from sales.services.payments import record_payment
from sales.workspace import with_invoice_balances


class MoneyWorkflowTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user=get_user_model().objects.create_superuser('money-admin','money@example.test','test-pass')
        cls.today=timezone.localdate()
        cls.usd=Currency.objects.get(company=None,code='USD')
        cls.zar=Currency.objects.create(code='ZAR',name='Rand')
        cls.bank=BankAccount.objects.filter(company=None,currency=cls.usd).first()
        cls.cash=create_money_account(user=cls.user,name='Cash drawer',currency=cls.usd,kind='CASH')
        cls.foreign=create_money_account(user=cls.user,name='Rand wallet',currency=cls.zar,kind='MOBILE')
        cls.category=ExpenseCategory.objects.create(name='Travel',default_account=Account.objects.get(company=None,code='5100'))
        cls.supplier=Supplier.objects.create(name='Transport supplier')
        cls.customer=Customer.objects.create(name='Money customer')
        cls.product=Product.objects.create(name='Taxed stock',sku='MONEY-TAX',price=100,tax_rate=20,quantity=20,avg_cost=30,category=Category.objects.create(name='Money test'))
        cls.vat=TaxRate.objects.create(name='Recoverable 15%',rate=15)

    def movement(self,**kwargs):
        data=dict(user=self.user,submission_key=uuid4(),kind='TRANSFER',date=self.today,amount=D('100'),source_account=self.bank,destination_account=self.cash)
        data.update(kwargs);return record_movement(**data)

    def bill(self,**kwargs):
        data=dict(user=self.user,submission_key=uuid4(),supplier=self.supplier,date=self.today,due_date=self.today,currency=self.usd,category=self.category,amount=D('100'))
        data.update(kwargs);return record_supplier_bill(**data)

    def pay(self,bill,**kwargs):
        data=dict(user=self.user,submission_key=uuid4(),bill_id=bill.pk,bank=self.bank,date=self.today,amount=D('40'))
        data.update(kwargs);return record_supplier_payment(**data)

    def invoice(self,**kwargs):
        invoice=Invoice.objects.create(customer=self.customer,status=Invoice.CONFIRMED,**kwargs)
        invoice.add_product_line(self.product,1);return invoice

    def test_transfer_preserves_combined_balance_and_is_not_income_or_expense(self):
        self.movement()
        data=money_workspace(start=self.today,end=self.today)
        usd=next(row for row in data['money_summary'] if row['currency']=='USD')
        self.assertEqual((usd['balance'],usd['money_in'],usd['money_out'],usd['transfers']),(0,0,0,100))
        self.assertEqual(get_cash_in(Period(self.today,self.today)),0)
        self.assertEqual(get_cash_out(Period(self.today,self.today)),0)
        self.assertEqual(data['entries'].count(),1)

    def test_transfer_retry_is_one_entry_and_changed_key_is_rejected(self):
        key=uuid4();first=self.movement(submission_key=key)
        self.assertEqual(first.pk,self.movement(submission_key=key).pk)
        with self.assertRaises(ValidationError):self.movement(submission_key=key,amount=101)
        self.assertEqual(JournalEntry.objects.filter(source='MONEY').count(),1)

    def test_cross_currency_same_account_and_foreign_company_transfers_rejected(self):
        for destination in [self.bank,self.foreign]:
            with self.assertRaises(ValidationError):self.movement(destination_account=destination)
        other=Company.objects.create(name='Other company')
        wallet=BankAccount.objects.create(company=other,name='Other',currency=self.usd,account=self.cash.account)
        with self.assertRaises(ValidationError):self.movement(destination_account=wallet)
        self.assertFalse(JournalEntry.objects.exists())

    def test_funding_opening_balance_and_withdrawal_are_separate_from_profit(self):
        wallet=create_money_account(user=self.user,name='Verified opening',currency=self.usd,opening_balance=250,date=self.today)
        self.movement(kind='OUT',source_account=wallet,destination_account=None,counter_account=Account.objects.get(company=None,code='3000'),amount=20)
        data=money_workspace(start=self.today,end=self.today)
        self.assertEqual(next(w.balance for w in data['money_accounts'] if w.pk==wallet.pk),230)
        self.assertEqual(get_cash_in(Period(self.today,self.today)),250)
        self.assertEqual(get_cash_out(Period(self.today,self.today)),20)
        self.assertEqual(get_total_expenses(Period(self.today,self.today)),0)
        bill=self.bill(amount=115,tax=self.vat)
        self.assertEqual((bill.subtotal,bill.tax),(100,15))
        self.assertEqual(get_total_expenses(Period(self.today,self.today)),100)

    def test_failed_journal_has_no_draft_or_partial_lines(self):
        with self.assertRaises(ValidationError):
            post_entry(day=self.today,currency=self.usd,rate=1,memo='invalid',source='MONEY',lines=[(self.cash.account,10,0),(self.bank.account,0,9)])
        self.assertFalse(JournalEntry.objects.exists())

    def test_future_and_closed_period_movements_rejected(self):
        with self.assertRaises(ValidationError):self.movement(date=self.today+timedelta(days=1))
        FiscalPeriod.objects.create(name='Closed',start_date=self.today,end_date=self.today,is_closed=True)
        with self.assertRaises(ValidationError):self.movement()

    def test_expense_selected_wallet_tax_and_foreign_book_rate(self):
        expense=Expense.objects.create(date=self.today,payee='Taxi',category=self.category,amount=115,tax=self.vat,currency=self.zar,fx_rate=D('.05'),bank=self.foreign)
        journal=post_expense(expense.pk);expense.refresh_from_db()
        self.assertEqual((expense.status,expense.posted),(Expense.POSTED,True))
        bankline=journal.lines.get(account=self.foreign.account)
        self.assertEqual((bankline.credit,bankline.credit_base),(D('115'),D('5.75')))
        self.assertEqual(journal.lines.get(account__code='1410').debit,15)
        self.assertEqual(post_expense(expense.pk).pk,journal.pk)
        self.assertEqual(get_total_expenses(Period(self.today,self.today),{'currency':'ZAR'}),100)
        self.vat.rate=20;self.vat.save()
        self.assertEqual(get_total_expenses(Period(self.today,self.today),{'currency':'ZAR'}),100)

    def test_expense_currency_mismatch_rolls_back(self):
        exp=Expense.objects.create(payee='Taxi',category=self.category,amount=100,currency=self.usd,bank=self.foreign)
        with self.assertRaises(ValidationError):post_expense(exp.pk)
        self.assertFalse(JournalEntry.objects.exists())

    def test_bill_payment_allocates_to_chosen_bill_partial_then_full(self):
        first=self.bill();second=self.bill()
        p=self.pay(second)
        self.assertEqual((bill_outstanding_balance(first),bill_outstanding_balance(second)),(100,60))
        self.assertEqual(p.allocations.get().bill_id,second.pk)
        self.pay(second,amount=60)
        second.refresh_from_db();self.assertEqual(second.status,SupplierBill.PAID)
        self.assertEqual(get_supplier_outstanding(self.supplier)['outstanding'],100)

    def test_supplier_retry_overpayment_wrong_currency_and_immutable_payment(self):
        bill=self.bill();key=uuid4();payment=self.pay(bill,submission_key=key,amount=100)
        self.assertEqual(self.pay(bill,submission_key=key,amount=100).pk,payment.pk)
        with self.assertRaises(ValidationError):self.pay(bill)
        with self.assertRaises(ValidationError):payment.save()
        unpaid=self.bill()
        with self.assertRaises(ValidationError):self.pay(unpaid,bank=self.foreign)
        self.assertEqual(APPayment.objects.count(),1)

    def test_bill_or_payment_post_failure_rolls_back_everything(self):
        with patch('accounting.services.money.post_entry',side_effect=ValidationError('No posting')):
            with self.assertRaises(ValidationError):self.bill()
        self.assertFalse(SupplierBill.objects.exists())
        bill=self.bill()
        with patch('accounting.services.money.post_entry',side_effect=ValidationError('No posting')):
            with self.assertRaises(ValidationError):self.pay(bill)
        self.assertFalse(APPayment.objects.exists());bill.refresh_from_db();self.assertEqual(bill.status,SupplierBill.POSTED)

    def test_new_invoice_adds_tax_receipt_clears_exact_gross_and_stock(self):
        invoice=self.invoice();self.assertEqual(invoice.total,120)
        record_payment(invoice_id=invoice.pk,user=self.user,submission_key=uuid4(),amount=D('120'),date=self.today,method='Cash',note='',bank=self.cash)
        invoice.refresh_from_db();self.product.refresh_from_db()
        self.assertEqual(invoice.status,Invoice.PAID);self.assertEqual(self.product.quantity,19)
        self.assertEqual(get_customer_outstanding(self.customer)['outstanding'],0)
        ar=JournalLine.objects.filter(account__code='1200').aggregate(d=Sum('debit'),c=Sum('credit'))
        self.assertEqual(ar['d']-ar['c'],0)
        self.assertEqual(JournalLine.objects.get(account__code='2100').credit,20)
        self.assertEqual(Payment.objects.get().bank_id,self.cash.pk)

    def test_legacy_invoice_retains_total_and_has_no_new_tax_posting(self):
        invoice=self.invoice(tax_added=False)
        self.assertEqual(invoice.total,100)
        Payment.objects.create(invoice=invoice,amount=100)
        self.assertFalse(JournalLine.objects.filter(account__code='2100').exists())

    def test_tax_rounding_annotation_and_combo_discount_match(self):
        zero=Product.objects.create(name='Zero tax item',sku='MONEY-ZERO',price=100,quantity=10,tax_rate=0)
        combo=Combo.objects.create(name='Mixed tax bundle',code='MONEY-COMBO',discount_type=Combo.DISCOUNT_FIXED,discount_value=40)
        ComboItem.objects.create(combo=combo,product=self.product,quantity=1);ComboItem.objects.create(combo=combo,product=zero,quantity=1)
        invoice=Invoice.objects.create(customer=self.customer)
        add_combo_to_document(invoice,combo.pk)
        self.assertEqual((invoice.subtotal,invoice.tax_total,invoice.total),(160,16,176))
        self.assertEqual(with_invoice_balances().get(pk=invoice.pk).workspace_total,176)
        invoice.add_misc_line('Round half up',D('.05'),10)
        self.assertEqual(invoice.tax_total,D('16.01'))
        self.assertEqual(with_invoice_balances().get(pk=invoice.pk).workspace_total,invoice.total)

    def test_supplier_balance_never_crosses_currency_company_or_draft(self):
        bill=self.bill()
        foreign=self.bill(currency=self.zar,amount=200)
        APPayment.objects.create(supplier=self.supplier,currency=self.zar,bank=self.foreign,amount=50)
        SupplierBill.objects.create(supplier=self.supplier,currency=self.usd,total=999,status=SupplierBill.DRAFT)
        other=Company.objects.create(name='Other')
        APPayment.objects.create(payment_no='OTHER-001',company=other,supplier=self.supplier,currency=self.usd,bank=self.bank,amount=999)
        self.assertEqual(bill_outstanding_balance(bill),100)
        self.assertEqual(bill_outstanding_balance(foreign),150)

    def test_as_of_excludes_later_customer_and_supplier_payments(self):
        yesterday=self.today-timedelta(days=1)
        invoice=self.invoice(date=yesterday,due_date=yesterday)
        Payment.objects.create(invoice=invoice,amount=40,date=self.today)
        self.assertEqual(get_customer_outstanding(self.customer,yesterday)['outstanding'],120)
        bill=self.bill(date=yesterday,due_date=yesterday);self.pay(bill)
        self.assertEqual(get_supplier_outstanding(self.supplier,yesterday)['outstanding'],100)

    def test_cashflow_chart_covers_all_sources_and_keeps_currencies_separate(self):
        invoice=self.invoice();Payment.objects.create(invoice=invoice,amount=40)
        ARPayment.objects.create(customer=self.customer,currency=self.usd,bank=self.bank,amount=10)
        bill=self.bill();self.pay(bill,amount=30)
        APPayment.objects.create(supplier=self.supplier,currency=self.zar,bank=self.foreign,amount=1000)
        period=Period(self.today,self.today);trend=cashflow_trend(period,'day')
        self.assertEqual(sum(r['cash_in'] for r in trend),get_cash_in(period))
        self.assertEqual(sum(r['cash_out'] for r in trend),get_cash_out(period))
        self.assertEqual((get_cash_in(period),get_cash_out(period)),(50,30))

    def test_get_screens_are_read_only_and_render_actions(self):
        self.invoice(due_date=self.today-timedelta(days=4));self.bill();self.client.force_login(self.user)
        before=(DebtorAccount.objects.count(),CreditorAccount.objects.count(),JournalEntry.objects.count())
        for name,args in [('accounting:accounting_dashboard',[]),('accounting:expense_create',[]),('accounting:money_move',[]),('accounting:money_account_create',[]),('accounting:money_receive',[]),('accounting:money_pay',[]),('accounting:supplier_bill_create',[]),('credit_control:dashboard',[]),('credit_control:debtors_list',[]),('credit_control:creditors_list',[]),('credit_control:debtor_detail',[self.customer.pk]),('credit_control:creditor_detail',[self.supplier.pk])]:
            response=self.client.get(reverse('ims:'+name,args=args));self.assertEqual(response.status_code,200,name)
        self.assertEqual(before,(DebtorAccount.objects.count(),CreditorAccount.objects.count(),JournalEntry.objects.count()))

    def test_mobile_capture_posts_and_duplicate_key_content_is_checked(self):
        self.client.force_login(self.user)
        data={'date':self.today,'amount':'25','payee':'Taxi','category':self.category.pk,'bank':self.cash.pk,'fx_rate':'1','submission_key':uuid4()}
        url=reverse('ims:accounting:expense_create')
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(self.client.post(url,data).status_code,302)
        response=self.client.post(url,{**data,'amount':'30'})
        self.assertContains(response,'already used');self.assertEqual(Expense.objects.count(),1)
        receipt={'invoice':self.invoice().pk,'date':self.today,'amount':'120','bank':self.cash.pk,'method':'Cash','fx_rate':'1','submission_key':uuid4()}
        self.assertEqual(self.client.post(reverse('ims:accounting:money_receive'),receipt).status_code,302)

    def test_money_actions_require_permission(self):
        user=get_user_model().objects.create_user('money-viewer');self.client.force_login(user)
        with self.assertRaises(PermissionDenied):self.movement(user=user)
        for name in ['money_move','money_receive','money_pay','money_account_create','supplier_bill_create','money_export']:
            self.assertEqual(self.client.get(reverse('ims:accounting:'+name)).status_code,403)

    def test_credit_exports_use_live_balances_without_creating_cache_rows(self):
        self.invoice();self.client.force_login(self.user)
        response=self.client.get(reverse('ims:credit_control:report_export_csv',args=['top_debtors']))
        self.assertContains(response,'Money customer,120.00')
        self.assertFalse(DebtorAccount.objects.exists())

    def test_staff_receipt_returns_to_invoice_without_finance_permission(self):
        from django.contrib.auth.models import Group
        staff=get_user_model().objects.create_user('receipt-staff');staff.groups.add(Group.objects.get_or_create(name='Staff')[0]);self.client.force_login(staff)
        invoice=self.invoice()
        data={'invoice':invoice.pk,'date':self.today,'amount':'120','bank':self.cash.pk,'method':'Cash','fx_rate':'1','submission_key':uuid4()}
        response=self.client.post(reverse('ims:accounting:money_receive'),data)
        self.assertRedirects(response,reverse('ims:sales:invoice_edit',args=[invoice.pk]))


class MoneyConcurrencyTests(TransactionTestCase):
    def setUp(self):
        from django.core.management import call_command
        call_command('seed_chart_of_accounts',verbosity=0)
        self.user=get_user_model().objects.create_superuser('money-race','race@example.test','test')
        self.usd=Currency.objects.get(company=None,code='USD')
        self.bank=BankAccount.objects.filter(company=None,currency=self.usd).first()
        self.cash=create_money_account(user=self.user,name='Race cash',currency=self.usd)
        self.category=ExpenseCategory.objects.create(name='Race expense',default_account=Account.objects.get(company=None,code='5100'))
        self.supplier=Supplier.objects.create(name='Race supplier')
        self.bill=record_supplier_bill(user=self.user,submission_key=uuid4(),supplier=self.supplier,date=timezone.localdate(),due_date=None,currency=self.usd,category=self.category,amount=100)

    def race(self,actions):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        barrier=Barrier(2)
        def run(action):
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                try:return action()
                except ValidationError:return 'rejected'
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(run,action) for action in actions]
            return [future.result(timeout=25) for future in futures]

    def pay(self,key):
        return record_supplier_payment(user=self.user,submission_key=key,bill_id=self.bill.pk,bank=self.bank,amount=100,date=timezone.localdate()).pk

    def test_concurrent_same_supplier_save_returns_one_payment(self):
        key=uuid4();results=self.race([lambda:self.pay(key),lambda:self.pay(key)])
        self.assertEqual(results[0],results[1]);self.assertNotIn('rejected',results)
        self.assertEqual(APPayment.objects.count(),1)

    def test_concurrent_different_supplier_saves_cannot_overpay(self):
        results=self.race([lambda:self.pay(uuid4()),lambda:self.pay(uuid4())])
        self.assertEqual(results.count('rejected'),1);self.assertEqual(APPayment.objects.count(),1)

    def test_concurrent_transfer_retry_rolls_back_duplicate_journal(self):
        key=uuid4()
        def move():return record_movement(user=self.user,submission_key=key,kind='TRANSFER',date=timezone.localdate(),amount=10,source_account=self.bank,destination_account=self.cash).pk
        results=self.race([move,move]);self.assertEqual(results[0],results[1])
        self.assertEqual(JournalEntry.objects.filter(source='MONEY').count(),1)
