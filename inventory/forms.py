from django import forms
from .models import Product, Category, Supplier, StockMovement

class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = '__all__'

class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ['product','movement_type','quantity','unit_cost','note']
