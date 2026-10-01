from django import forms
from inventory.models import Product, ProductUnit


class StyledForm(forms.Form):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.widget.attrs['class'] = 'form-select' if isinstance(field.widget, forms.Select) else 'form-control'


class BarcodeForm(StyledForm):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True).order_by('name'))
    code = forms.CharField(max_length=120)


class UnitActionForm(StyledForm):
    action = forms.ChoiceField(choices=[('transfer', 'Move location'), ('return', 'Receive customer return'),
        ('fault', 'Mark faulty'), ('repair', 'Send for repair'), ('customer_return', 'Return repaired unit to its customer'),
        ('restock', 'Approve restock'), ('supplier_return', 'Return to supplier'), ('write_off', 'Write off'),
        ('correct_serial', 'Correct manufacturer serial')])
    note = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), max_length=2000)
    location = forms.CharField(max_length=100, required=False)
    serial = forms.CharField(max_length=120, required=False)

    def __init__(self, *args, unit=None, **kwargs):
        super().__init__(*args, **kwargs)
        if unit:
            actions = {'AVAILABLE': ['transfer', 'fault', 'supplier_return', 'write_off'],
                'SOLD': ['return'], 'RETURNED': ['transfer', 'fault', 'repair', 'restock', 'customer_return', 'supplier_return', 'write_off'],
                'FAULTY': ['transfer', 'repair', 'restock', 'customer_return', 'supplier_return', 'write_off'],
                'REPAIR': ['transfer', 'restock', 'customer_return', 'supplier_return', 'write_off']}.get(unit.status, [])
            self.fields['action'].choices = [(value, label) for value, label in self.fields['action'].choices if value in actions + ['correct_serial']]
        self.fields['serial'].label = 'Manufacturer serial (for correction only)'
        self.fields['location'].label = 'Destination (for location moves only)'


class StocktakeForm(StyledForm):
    name = forms.CharField(max_length=150)
    location = forms.CharField(max_length=100, required=False, help_text='Blank counts all stock. A location counts serialized units only.')


class CaseForm(StyledForm):
    problem = forms.CharField(max_length=4000, widget=forms.Textarea(attrs={'rows': 3}))


class CaseUpdateForm(StyledForm):
    action = forms.ChoiceField(choices=[('repair', 'Begin repair'), ('close', 'Close case')])
    inspection = forms.CharField(required=False, max_length=4000, widget=forms.Textarea(attrs={'rows': 2}))
    resolution = forms.CharField(required=False, max_length=4000, widget=forms.Textarea(attrs={'rows': 2}))
    replacement = forms.ModelChoiceField(queryset=ProductUnit.objects.none(), required=False,
        help_text='Optional same-product replacement when closing. Original warranty expiry is preserved; no refund is issued.')

    def __init__(self, *args, product_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['replacement'].queryset = ProductUnit.objects.filter(product_id=product_id, status='AVAILABLE', sale_line__isnull=True, order_item__isnull=True)
