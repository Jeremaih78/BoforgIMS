from decimal import Decimal, ROUND_HALF_UP
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
    def clean_custom_fields(self):
        value = self.cleaned_data.get('custom_fields') or {}
        if not isinstance(value, dict) or len(value) > 12:
            raise forms.ValidationError('Use up to 12 named custom fields.')
        cleaned = {}
        for label, text in value.items():
            if not isinstance(label, str) or not isinstance(text, str):
                raise forms.ValidationError('Custom field names and values must be text.')
            label = label.strip()
            if not label or len(label) > 60 or len(text) > 500:
                raise forms.ValidationError('Use names of 1–60 characters and values up to 500 characters.')
            if label in cleaned:
                raise forms.ValidationError('Custom field names must be unique.')
            cleaned[label] = text.strip()
        return cleaned

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
            if name == 'terms':
                field.widget.attrs['rows'] = 3
            if name == 'custom_fields':
                field.widget = forms.Textarea(attrs={'class': 'form-control', 'rows': 3,
                    'data-custom-fields-source': '', 'aria-label': 'Custom fields as JSON'})
                field.help_text = 'Add your own fields, such as delivery address, PO number or warranty.'


class QuotationForm(CustomerOrderedFormMixin, forms.ModelForm):
    submission_key=forms.UUIDField(initial=uuid4,required=False,widget=forms.HiddenInput)
    document_signature=forms.CharField(required=False,widget=forms.HiddenInput)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Quotation
        fields = ['customer', 'date', 'reference', 'notes', 'terms', 'custom_fields']


class InvoiceForm(CustomerOrderedFormMixin, forms.ModelForm):
    submission_key=forms.UUIDField(initial=uuid4,required=False,widget=forms.HiddenInput)
    document_signature=forms.CharField(required=False,widget=forms.HiddenInput)
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Invoice
        fields = ['customer', 'date', 'due_date', 'reference', 'notes', 'terms', 'custom_fields']

    def clean(self):
        cleaned = super().clean()
        if cleaned.get('date') and cleaned.get('due_date') and cleaned['due_date'] < cleaned['date']:
            self.add_error('due_date', 'Due date cannot be before the invoice date.')
        return cleaned


class DocumentLineForm(forms.ModelForm):
    class Meta:
        model = DocumentLine
        fields = ['product', 'description', 'quantity', 'unit_price', 'tax_rate_percent']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['product'].queryset = Product.objects.filter(
            Q(is_active=True) | Q(pk=self.instance.product_id)
        ).select_related('category')
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
        cleaned['line_total'] = (price_decimal * quantity).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
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


class DocumentLineSet(forms.BaseModelFormSet):
    """A batch is validated before any header, line or reservation is written."""

    def clean(self):
        super().clean()
        if any(self.errors):
            return
        currencies = set()
        for form in self.forms:
            data = form.cleaned_data
            if not data or data.get('DELETE'):
                continue
            product = data.get('product')
            if product:
                currencies.add(product.currency)
            if form.instance.pk and (set(form.changed_data) & {'product', 'quantity'}):
                if form.instance.product_units.exists():
                    raise forms.ValidationError('A line with assigned physical units cannot change product or quantity here. Remove the line from the document first.')
        for form in self.deleted_forms:
            if form.instance.pk and form.instance.product_units.exists():
                raise forms.ValidationError('Remove lines with assigned physical units from the document page so their units are released.')
        if len(currencies) > 1:
            raise forms.ValidationError('Use products in one currency per document. Currency conversion is not automatic.')


DocumentLineFormSet = forms.modelformset_factory(
    DocumentLine, form=DocumentLineForm, formset=DocumentLineSet,
    extra=1, can_delete=True, min_num=0,
    max_num=100, validate_max=True, absolute_max=100,
)


class ComboSelectionForm(forms.Form):
    combo = forms.ModelChoiceField(queryset=Combo.objects.filter(is_active=True))
    quantity = forms.IntegerField(min_value=1, initial=1)


class ComposerComboForm(ComboSelectionForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['combo'].queryset = Combo.objects.filter(is_active=True).prefetch_related('items__product')
        self.fields['combo'].widget = forms.HiddenInput()
        self.fields['quantity'].widget.attrs.update({'class': 'form-control', 'min': '1', 'step': '1'})
        self.selected_combo = None
        self.combo_info = None
        combo_id = self.data.get(self.add_prefix('combo')) if self.is_bound else self.initial.get('combo')
        if combo_id:
            try:
                from .combo_catalog import combo_details
                self.selected_combo = self.fields['combo'].queryset.get(pk=combo_id)
                self.combo_info = combo_details(self.selected_combo)
            except (Combo.DoesNotExist, TypeError, ValueError):
                pass

    def clean(self):
        cleaned = super().clean()
        combo, quantity = cleaned.get('combo'), cleaned.get('quantity')
        if combo and quantity:
            from .combo_catalog import validate_combo_quantity
            try:
                cleaned['combo_info'] = validate_combo_quantity(combo, quantity)
            except ValueError as exc:
                raise forms.ValidationError(str(exc)) from exc
        return cleaned


ComposerComboFormSet = forms.formset_factory(
    ComposerComboForm, extra=0, can_delete=True,
    max_num=100, validate_max=True, absolute_max=100,
)


class PaymentForm(forms.ModelForm):
    submission_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)

    def clean_amount(self):
        amount = self.cleaned_data['amount']
        if amount <= 0:
            raise forms.ValidationError('Payment must be positive.')
        return amount

    def __init__(self, *args, **kwargs):
        invoice=kwargs.pop('invoice',None)
        super().__init__(*args, **kwargs)
        from accounting.models import BankAccount, JournalEntry
        from django.conf import settings
        self.fields['bank'].queryset=BankAccount.objects.filter(company=None,is_active=True,account__is_active=True).select_related('currency').order_by('name')
        self.fields['bank'].label='Received into'
        self.fields['fx_rate'].required=False
        self.fields['fx_rate'].label='Invoice book exchange rate'
        self.fields['fx_rate'].help_text='Base currency per 1 invoice-currency unit. Use the agreed invoice rate; conversions are separate.'
        if invoice:
            from accounting.services.receipts import invoice_currency_code
            try:
                code=invoice_currency_code(invoice)
            except forms.ValidationError:
                code=None
            self.fields['bank'].queryset=self.fields['bank'].queryset.filter(currency__code=code)
            default=self.fields['bank'].queryset.first()
            if default:
                self.initial.setdefault('bank',default.pk)
            previous=JournalEntry.objects.filter(source='INVOICE',source_id=invoice.pk,is_posted=True,lines__account__code='4000').first()
            if previous:
                self.initial.setdefault('fx_rate',previous.fx_rate)
            elif code!=getattr(settings,'BASE_CURRENCY_CODE','USD'):
                self.initial['fx_rate']=None
        for field in self.fields.values():
            if not isinstance(field.widget,forms.HiddenInput):
                field.widget.attrs.setdefault('class','form-select' if isinstance(field.widget,forms.Select) else 'form-control')
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Payment
        fields = ['amount', 'date', 'bank', 'method', 'note', 'fx_rate']


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
            Q(status=ProductUnit.STATUS_AVAILABLE, order_item__isnull=True) | Q(sale_line=line)
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

