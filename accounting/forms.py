from django import forms
from uuid import uuid4
from .models import Expense, ExpenseCategory
from decimal import Decimal
from django.utils import timezone
from .models import BankAccount, Currency, TaxRate, Account, MoneyMovement, SupplierBill
from .services.money import book_rate, check_posting_date
from inventory.models import Supplier
from django.db.models import Q


class WalletChoice(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f'{obj.name} · {obj.currency.code}'


def wallets():
    return BankAccount.objects.filter(company=None, is_active=True, account__is_active=True, account__type=Account.ASSET).select_related('currency').order_by('name')


def style_fields(form):
    for field in form.fields.values():
        if isinstance(field.widget, forms.HiddenInput):
            continue
        field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'
        if isinstance(field, forms.DateField):
            field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type':'date','class':'form-control'})
            if not field.initial:
                field.initial = timezone.localdate
        if isinstance(field, forms.DecimalField):
            field.widget.attrs['inputmode'] = 'decimal'


class ExpenseForm(forms.ModelForm):
    submission_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)
    bank = WalletChoice(queryset=BankAccount.objects.none(), required=False, label='Paid from')

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Expense amount must be positive.')
        return amount

    def clean_fx_rate(self):
        rate = self.cleaned_data['fx_rate']
        if rate <= 0:
            raise forms.ValidationError('Exchange rate must be positive.')
        return rate

    def clean_attachment(self):
        upload = self.cleaned_data.get('attachment')
        if upload:
            from core.uploads import validate_document
            validate_document(upload)
        return upload

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['bank'].queryset = wallets()
        self.fields['bank'].widget.attrs['required']=True
        self.fields['category'].queryset = ExpenseCategory.objects.filter(company=None,default_account__is_active=True,default_account__type=Account.EXPENSE)
        self.fields['tax'].queryset = TaxRate.objects.filter(company=None)
        self.fields['currency'].queryset = Currency.objects.filter(company=None)
        self.fields['currency'].required = False
        self.fields['currency'].widget = forms.HiddenInput()
        self.fields['amount'].label = 'Total paid (including tax)'
        self.fields['amount'].decimal_places = 2
        self.fields['amount'].max_digits = 14
        self.fields['fx_rate'].help_text = 'Base-currency units per 1 transaction-currency unit. Use 1 for the base currency.'
        self.fields['attachment'].widget.attrs.update(accept='image/*,application/pdf',capture='environment')
        self.fields['notes'].widget.attrs['rows'] = 3
        style_fields(self)

    def clean(self):
        data=super().clean()
        bank=data.get('bank')
        if bank:
            data['currency']=bank.currency
            self.instance.currency=bank.currency
        if not data.get('currency'):
            self.add_error('bank','Choose the account used to pay this expense.')
        if data.get('currency') and data.get('fx_rate'):
            try: book_rate(data['currency'],data['fx_rate'])
            except forms.ValidationError as exc: self.add_error('fx_rate',exc)
        if data.get('date'):
            check_posting_date(data['date'])
        return data

    class Meta:
        model = Expense
        fields = [
            'date', 'payee', 'category', 'amount', 'bank', 'tax', 'currency', 'fx_rate', 'reference', 'notes', 'attachment'
        ]


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ['name', 'default_account']


class MoneyForm(forms.Form):
    submission_key=forms.UUIDField(initial=uuid4,widget=forms.HiddenInput)
    date=forms.DateField(initial=timezone.localdate)
    amount=forms.DecimalField(max_digits=14,decimal_places=2,min_value=Decimal('.01'))
    fx_rate=forms.DecimalField(max_digits=18,decimal_places=6,min_value=Decimal('.000001'),initial=1,label='Book exchange rate')
    reference=forms.CharField(max_length=120,required=False)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        style_fields(self)

    def clean_date(self):
        day=self.cleaned_data['date'];check_posting_date(day);return day


class MovementForm(MoneyForm):
    kind=forms.ChoiceField(choices=MoneyMovement.KIND_CHOICES,label='Movement')
    source_account=WalletChoice(queryset=BankAccount.objects.none(),required=False,label='From')
    destination_account=WalletChoice(queryset=BankAccount.objects.none(),required=False,label='To')
    counter_account=forms.ModelChoiceField(queryset=Account.objects.none(),required=False,label='Owner / loan account')
    notes=forms.CharField(max_length=500,required=False,widget=forms.Textarea(attrs={'rows':2}))

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['source_account'].queryset=wallets()
        self.fields['destination_account'].queryset=wallets()
        self.fields['counter_account'].queryset=Account.objects.filter(company=None,is_active=True,type__in=[Account.EQUITY,Account.LIABILITY])
        self.order_fields(['kind','source_account','destination_account','amount','date','counter_account','fx_rate','reference','notes'])


class MoneyAccountForm(forms.Form):
    name=forms.CharField(max_length=100)
    kind=forms.ChoiceField(choices=BankAccount._meta.get_field('kind').choices)
    currency=forms.ModelChoiceField(queryset=Currency.objects.filter(company=None))
    opening_balance=forms.DecimalField(max_digits=14,decimal_places=2,min_value=0,initial=0)
    date=forms.DateField(initial=timezone.localdate,label='Opening balance date')
    fx_rate=forms.DecimalField(max_digits=18,decimal_places=6,min_value=Decimal('.000001'),initial=1)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);style_fields(self)


class SupplierBillForm(MoneyForm):
    supplier=forms.ModelChoiceField(queryset=Supplier.objects.order_by('name'))
    due_date=forms.DateField(required=False)
    currency=forms.ModelChoiceField(queryset=Currency.objects.filter(company=None))
    category=forms.ModelChoiceField(queryset=ExpenseCategory.objects.filter(company=None,default_account__is_active=True,default_account__type=Account.EXPENSE))
    tax=forms.ModelChoiceField(queryset=TaxRate.objects.filter(company=None),required=False,label='Included recoverable tax')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['amount'].label='Total bill amount (including tax)'
        self.order_fields(['supplier','amount','category','currency','date','due_date','tax','fx_rate','reference'])


class SupplierPaymentForm(MoneyForm):
    bill=forms.ModelChoiceField(queryset=SupplierBill.objects.none())
    bank=WalletChoice(queryset=BankAccount.objects.none(),label='Paid from')
    method=forms.CharField(max_length=50,initial='Bank')
    notes=forms.CharField(max_length=255,required=False)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields.pop('fx_rate');self.fields.pop('reference')
        self.fields['bill'].queryset=SupplierBill.objects.filter(company=None,status__in=[SupplierBill.POSTED,SupplierBill.PARTIAL]).select_related('supplier','currency')
        self.fields['bill'].label_from_instance=lambda b:f'{b.doc_no} · {b.supplier.name} · {b.currency.code} {b.total:.2f}'
        self.fields['bank'].queryset=wallets()
        # Include a just-paid bill on an idempotent retry; the service validates it.
        selected=self.data.get('bill') or self.initial.get('bill')
        if selected and str(selected).isdigit():
            self.fields['bill'].queryset=SupplierBill.objects.filter(company=None).filter(Q(status__in=[SupplierBill.POSTED,SupplierBill.PARTIAL])|Q(pk=selected)).select_related('supplier','currency')
        self.order_fields(['bill','bank','amount','date','method','notes'])


from sales.forms import PaymentForm
from sales.workspace import with_invoice_balances
from sales.models import Invoice


class ReceiptForm(PaymentForm):
    invoice=forms.ModelChoiceField(queryset=Invoice.objects.none())

    def __init__(self,*args,**kwargs):
        posted=args[0] if args else kwargs.get('data')
        selected=(posted or {}).get('invoice') or kwargs.get('initial',{}).get('invoice')
        if selected and str(selected).isdigit():
            kwargs['invoice']=Invoice.objects.filter(pk=selected).first()
        super().__init__(*args,**kwargs)
        from sales.workspace import VALID_STATUSES
        self.fields['invoice'].queryset=with_invoice_balances().filter(status__in=VALID_STATUSES,workspace_balance__gt=0).exclude(status='PAID').order_by('-date')
        selected=self.data.get('invoice') or self.initial.get('invoice')
        if selected and str(selected).isdigit():
            from django.db.models import Q
            self.fields['invoice'].queryset=with_invoice_balances().filter(Q(status__in=VALID_STATUSES,workspace_balance__gt=0)|Q(pk=selected)).exclude(status='CANCELLED')
        self.fields['invoice'].label_from_instance=lambda inv:f'{inv.number} · {inv.customer.name} · {inv.workspace_currency} {inv.workspace_balance:.2f} due'
        self.fields['invoice'].queryset=self.fields['invoice'].queryset.select_related('customer')
        self.fields['bank'].required=True
        self.order_fields(['invoice','bank','amount','date','method','note','fx_rate'])
        style_fields(self)

