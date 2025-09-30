from decimal import Decimal

from django import forms

from .models import Quotation, Invoice, Payment, DocumentLine
from inventory.models import Combo


class QuotationForm(forms.ModelForm):
    class Meta:
        model = Quotation
        fields = ['customer', 'date', 'notes']


class InvoiceForm(forms.ModelForm):
    class Meta:
        model = Invoice
        fields = ['customer', 'date', 'due_date', 'notes']


class DocumentLineForm(forms.ModelForm):
    class Meta:
        model = DocumentLine
        fields = ['product', 'description', 'quantity', 'unit_price', 'tax_rate_percent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['unit_price'].required = False
        self.fields['tax_rate_percent'].required = False
        self.fields['quantity'].initial = self.fields['quantity'].initial or 1

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        quantity = Decimal(str(cleaned.get('quantity') or 0))
        unit_price = cleaned.get('unit_price')
        if not product:
            raise forms.ValidationError('Select a product.')
        if quantity <= 0:
            raise forms.ValidationError('Quantity must be positive.')
        if unit_price is None:
            unit_price = product.price
            cleaned['unit_price'] = unit_price
        price_decimal = Decimal(str(cleaned['unit_price']))
        tax_value = cleaned.get('tax_rate_percent')
        if tax_value in (None, ''):
            tax_value = getattr(product, 'tax_rate', 0) or 0
        cleaned['tax_rate_percent'] = Decimal(str(tax_value)).quantize(Decimal('0.01'))
        cleaned['quantity'] = quantity
        cleaned['line_total'] = (price_decimal * quantity).quantize(Decimal('0.01'))
        return cleaned


    def save(self, commit=True):
        instance = super().save(commit=False)
        data = self.cleaned_data
        instance.unit_price = data['unit_price']
        instance.tax_rate_percent = data['tax_rate_percent']
        instance.line_total = data['line_total']
        if commit:
            instance.save()
        return instance


class ComboSelectionForm(forms.Form):
    combo = forms.ModelChoiceField(queryset=Combo.objects.filter(is_active=True))
    quantity = forms.IntegerField(min_value=1, initial=1)


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ['invoice', 'amount', 'date', 'method', 'note']

