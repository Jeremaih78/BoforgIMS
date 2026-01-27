from django import forms


INPUT_CLASS = 'mt-1 w-full border rounded-xl px-3 py-2'


class CheckoutForm(forms.Form):
    full_name = forms.CharField(
        max_length=150,
        required=False,
        label='Full Name',
        widget=forms.TextInput(attrs={'class': INPUT_CLASS}),
    )
    email = forms.EmailField(
        label='Email Address',
        widget=forms.EmailInput(attrs={'class': INPUT_CLASS}),
    )
    notes = forms.CharField(
        widget=forms.Textarea(attrs={'rows': 3, 'class': INPUT_CLASS}),
        required=False,
        label='Notes',
    )
