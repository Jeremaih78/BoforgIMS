from django import forms
from .models import Quotation, QuotationItem, Invoice, InvoiceItem, Payment

class QuotationForm(forms.ModelForm):
    class Meta: model = Quotation; fields = ['customer','date','notes']

class QuotationItemForm(forms.ModelForm):
    class Meta:
        model = QuotationItem
        fields = ['product','description','quantity','unit_price','discount_percent','discount_value']

class InvoiceForm(forms.ModelForm):
    class Meta: model = Invoice; fields = ['customer','date','due_date','notes']

class InvoiceItemForm(forms.ModelForm):
    class Meta:
        model = InvoiceItem
        fields = ['product','description','quantity','unit_price','discount_percent','discount_value']

class PaymentForm(forms.ModelForm):
    class Meta: model = Payment; fields = ['invoice','amount','date','method','note']
