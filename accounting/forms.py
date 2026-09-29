from django import forms
from uuid import uuid4
from .models import Expense, ExpenseCategory


class ExpenseForm(forms.ModelForm):
    submission_key = forms.UUIDField(initial=uuid4, widget=forms.HiddenInput)

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
        for name, field in self.fields.items():
            if isinstance(field, forms.DateField):
                field.widget = forms.DateInput(format='%Y-%m-%d', attrs={'type': 'date', 'class': 'form-control'})

    class Meta:
        model = Expense
        fields = [
            'date', 'payee', 'category', 'amount', 'tax', 'currency', 'fx_rate', 'notes', 'attachment'
        ]


class ExpenseCategoryForm(forms.ModelForm):
    class Meta:
        model = ExpenseCategory
        fields = ['name', 'default_account']

