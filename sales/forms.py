from decimal import Decimal
from uuid import uuid4

from django import forms
from django.db.models import Q

from .models import Quotation, Invoice, Payment, DocumentLine
from inventory.models import Combo, Product, ProductUnit
from customers.models import Customer


def newest_customer_queryset():
    return Customer.objects.order_by('-id')


class CustomerSearchSelect(forms.Select):
    """Keep native selection/validation while exposing searchable contact details."""

    def create_option(self, name, value, label, selected, index, subindex=None, attrs=None):
        option = super().create_option(name, value, label, selected, index, subindex, attrs)
        if value and hasattr(value, 'instance'):
            option['attrs']['data-phone'] = value.instance.phone or ''
            option['attrs']['data-email'] = value.instance.email or ''
        return option


class CustomerOrderedFormMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'customer' in self.fields:
            self.fields['customer'].queryset = newest_customer_queryset()
            self.fields['customer'].widget = CustomerSearchSelect(attrs={
                'class': 'form-select', 'data-customer-search': '',
            })
            self.fields['customer'].widget.choices = self.fields['customer'].choices
        for name, field in self.fields.items():
            if name != 'customer':
                widget = field.widget
                css_class = 'form-control'
                if isinstance(widget, forms.Select):
                    css_class = 'form-select'
                existing = widget.attrs.get('class', '')
                widget.attrs['class'] = f'{existing} {css_class}'.strip()
            if name == 'notes':
                field.widget.attrs['rows'] = 1


class QuotationForm(CustomerOrderedFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Quotation
        fields = ['customer', 'date', 'notes']


class InvoiceForm(CustomerOrderedFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Invoice
        fields = ['customer', 'date', 'due_date', 'notes']


class DocumentLineForm(forms.ModelForm):
    class Meta:
        model = DocumentLine
        fields = ['product', 'description', 'quantity', 'unit_price', 'tax_rate_percent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(is_active=True).select_related('category')
        self.fields['product'].widget = forms.HiddenInput()
        self.fields['unit_price'].required = False
        self.fields['tax_rate_percent'].required = False
        self.fields['quantity'].initial = self.fields['quantity'].initial or 1
        for name in ('description', 'quantity', 'unit_price', 'tax_rate_percent'):
            self.fields[name].widget.attrs['class'] = 'form-control'
        self.fields['description'].widget.attrs['rows'] = 2
        self.fields['quantity'].widget.attrs.update({'min': '0.01', 'step': '0.01'})
        self.fields['unit_price'].widget.attrs.update({'min': '0', 'step': '0.01'})
        self.fields['tax_rate_percent'].widget.attrs.update({'min': '0', 'step': '0.01'})
        self.selected_product = None
        product_id = self.data.get(self.add_prefix('product')) if self.is_bound else self.initial.get('product')
        if not product_id and getattr(self.instance, 'product_id', None):
            product_id = self.instance.product_id
        if product_id:
            try:
                self.selected_product = self.fields['product'].queryset.get(pk=product_id)
            except (Product.DoesNotExist, TypeError, ValueError):
                pass

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
        if price_decimal < 0:
            self.add_error('unit_price', 'Price cannot be negative.')
        if product.track_inventory and quantity != quantity.to_integral_value():
            self.add_error('quantity', 'Stock quantities must be whole units.')
        tax_value = cleaned.get('tax_rate_percent')
        if tax_value in (None, ''):
            tax_value = getattr(product, 'tax_rate', 0) or 0
        if not 0 <= Decimal(str(tax_value)) <= 100:
            self.add_error('tax_rate_percent', 'Tax must be between 0 and 100.')
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
    submission_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Payment must be positive.')
        return amount

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Payment
        fields = ['amount', 'date', 'method', 'note']


class InvoiceLineSerialAssignmentForm(forms.Form):
    serials = forms.ModelMultipleChoiceField(
        queryset=ProductUnit.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        required=False,
    )

    def __init__(self, *args, line: DocumentLine, **kwargs):
        self.line = line
        super().__init__(*args, **kwargs)
        qs = ProductUnit.objects.filter(product=line.product).filter(
            Q(status=ProductUnit.STATUS_AVAILABLE) | Q(sale_line=line)
        )
        self.fields['serials'].queryset = qs
        self.fields['serials'].label = f'Assign {int(line.quantity)} serials'
        self.fields['serials'].initial = line.product_units.values_list('id', flat=True)

    def clean_serials(self):
        serials = self.cleaned_data['serials']
        required = int(self.line.quantity)
        if len(serials) != required:
            raise forms.ValidationError(f'{self.line.product} requires exactly {required} serial numbers.')
        return serials

