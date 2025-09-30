from django import forms
from django.core.exceptions import ValidationError
from PIL import Image

from .models import Product, StockMovement


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
