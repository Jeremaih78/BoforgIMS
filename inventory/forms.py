from django import forms
from django.core.exceptions import ValidationError
from PIL import Image

from .models import (
    Product,
    StockMovement,
    Shipment,
    ShipmentItem,
    ShipmentCost,
)


class ProductForm(forms.ModelForm):
    remove_image = forms.BooleanField(required=False, initial=False, label="Remove image")

    class Meta:
        model = Product
        fields = [
            'name',
            'sku',
            'slug',
            'category',
            'supplier',
            'price',
            'currency',
            'avg_cost',
            'quantity',
            'reserved',
            'track_inventory',
            'tracking_mode',
            'reorder_level',
            'tax_rate',
            'description',
            'image',
            'is_active',
        ]
        widgets = {
            'description': forms.Textarea(attrs={'rows': 4}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            widget = field.widget
            if isinstance(widget, forms.CheckboxInput):
                classes = set(widget.attrs.get('class', '').split())
                classes.add('form-check-input')
                widget.attrs['class'] = ' '.join(filter(None, classes))
            else:
                classes = widget.attrs.get('class', '').split()
                if 'form-control' not in classes:
                    classes.append('form-control')
                widget.attrs['class'] = ' '.join(filter(None, classes))
        image_field = self.fields.get('image')
        if image_field:
            image_field.widget.attrs.update({'accept': 'image/*'})
            image_field.required = False
        slug_field = self.fields.get('slug')
        if slug_field:
            slug_field.required = False

    def clean_image(self):
        image = self.cleaned_data.get('image')
        if image:
            max_size = 5 * 1024 * 1024
            content_type = getattr(image, 'content_type', '') or ''
            if content_type and not content_type.startswith('image/'):
                raise ValidationError('Please upload an image file (PNG, JPG, GIF, etc.).')
            size = getattr(image, 'size', 0) or 0
            if size > max_size:
                raise ValidationError('Image must be 5MB or smaller.')
            try:
                Image.open(image).verify()
            except Exception as exc:  # noqa: BLE001 - pillow raises many exception types
                raise ValidationError('Upload a valid image file.') from exc
            finally:
                if hasattr(image, 'seek'):
                    image.seek(0)
        return image

    def clean(self):
        cleaned_data = super().clean()
        if cleaned_data.get('remove_image') and cleaned_data.get('image'):
            self.add_error('remove_image', 'Uncheck "Remove image" if you are uploading a replacement.')
        return cleaned_data

    def save(self, commit=True):
        product = super().save(commit=False)
        remove_image = self.cleaned_data.get('remove_image')
        if remove_image:
            if product.pk and product.image:
                product.image.delete(save=False)
            product.image = None
        if commit:
            product.save()
            self.save_m2m()
        return product


class StockMovementForm(forms.ModelForm):
    class Meta:
        model = StockMovement
        fields = ['product', 'movement_type', 'quantity', 'unit_cost', 'note']


class ShipmentForm(forms.ModelForm):
    class Meta:
        model = Shipment
        fields = [
            'name',
            'supplier',
            'origin_country',
            'destination_country',
            'incoterm',
            'shipping_method',
            'eta_date',
            'arrival_date',
            'allocation_basis',
            'status',
        ]


class ShipmentItemForm(forms.ModelForm):
    class Meta:
        model = ShipmentItem
        fields = [
            'product',
            'quantity_expected',
            'unit_purchase_price',
            'hs_code',
            'tracking_mode',
        ]

    def __init__(self, *args, shipment=None, **kwargs):
        self.shipment = shipment
        super().__init__(*args, **kwargs)
        self.fields['tracking_mode'].widget.attrs['class'] = 'form-select'

    def clean(self):
        cleaned = super().clean()
        product = cleaned.get('product')
        if product and not cleaned.get('tracking_mode'):
            cleaned['tracking_mode'] = product.tracking_mode
        return cleaned

    def save(self, commit=True):
        obj = super().save(commit=False)
        if self.shipment:
            obj.shipment = self.shipment
        if commit:
            obj.save()
        return obj


class ShipmentCostForm(forms.ModelForm):
    class Meta:
        model = ShipmentCost
        fields = ['cost_type', 'description', 'amount', 'currency', 'fx_rate', 'supporting_document']
        widgets = {
            'supporting_document': forms.ClearableFileInput(attrs={
                'accept': '.pdf,.png,.jpg,.jpeg,.doc,.docx,.xls,.xlsx'
            })
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        doc_field = self.fields.get('supporting_document')
        if doc_field:
            doc_field.help_text = 'Optional file (PDF, image, Word, Excel up to 10MB).'

    def clean_supporting_document(self):
        upload = self.cleaned_data.get('supporting_document')
        if upload:
            max_size = 10 * 1024 * 1024  # 10 MB
            size = getattr(upload, 'size', 0) or 0
            if size > max_size:
                raise ValidationError('Supporting document must be 10MB or smaller.')
        return upload


class ShipmentItemReceiptForm(forms.Form):
    item_id = forms.IntegerField(widget=forms.HiddenInput)
    quantity = forms.IntegerField(min_value=0)
    serials = forms.CharField(widget=forms.Textarea(attrs={'rows': 2}), required=False)

    def __init__(self, *args, item: ShipmentItem, **kwargs):
        self.item = item
        super().__init__(*args, **kwargs)
        self.fields['item_id'].initial = item.id
        self.fields['quantity'].initial = max(item.quantity_expected - item.quantity_received, 0)
        if not item.requires_serials:
            self.fields['serials'].widget = forms.HiddenInput()
            self.fields['serials'].required = False

    def clean(self):
        cleaned = super().clean()
        quantity = cleaned.get('quantity') or 0
        remaining = max(self.item.quantity_expected - self.item.quantity_received, 0)
        if quantity > remaining:
            raise ValidationError(f'Cannot receive more than {remaining} units for {self.item.product}.')
        serials_raw = cleaned.get('serials') or ''
        serials = [s.strip() for s in serials_raw.replace(',', '\n').splitlines() if s.strip()]
        if self.item.requires_serials:
            if quantity == 0:
                serials = []
            if quantity != len(serials):
                raise ValidationError(f'{self.item.product} requires {quantity} serial numbers.')
        cleaned['serial_list'] = serials
        return cleaned


class ProductLandedCostForm(forms.Form):
    product = forms.ModelChoiceField(queryset=Product.objects.filter(is_active=True), required=True)


class SerialProfitLookupForm(forms.Form):
    serial_number = forms.CharField(max_length=120)
