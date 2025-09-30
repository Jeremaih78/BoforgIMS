from django import forms


class CheckoutForm(forms.Form):
    full_name = forms.CharField(max_length=150, required=False, label='Full Name')
    email = forms.EmailField(label='Email Address')
    notes = forms.CharField(widget=forms.Textarea(attrs={'rows': 3}), required=False, label='Notes')

