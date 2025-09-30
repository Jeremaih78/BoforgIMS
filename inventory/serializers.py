from rest_framework import serializers

from .models import Product, Combo, ComboItem


class ProductSerializer(serializers.ModelSerializer):
    image = serializers.ImageField(required=False, allow_null=True)

    class Meta:
        model = Product
        fields = [
            'id',
            'name',
            'sku',
            'price',
            'quantity',
            'image',
        ]

    def validate_image(self, image):
        if image:
            max_size = 5 * 1024 * 1024
            size = getattr(image, 'size', 0) or 0
            if size > max_size:
                raise serializers.ValidationError('Image must be 5MB or smaller.')
        return image

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request') if isinstance(self.context, dict) else None
        image_field = instance.image
        if image_field:
            data['image'] = request.build_absolute_uri(image_field.url) if request else image_field.url
        elif instance.image_url:
            data['image'] = instance.image_url
        else:
            data['image'] = None
        return data


class ComboItemSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source='product.name', read_only=True)
    product_sku = serializers.CharField(source='product.sku', read_only=True)
    product_price = serializers.DecimalField(source='product.price', max_digits=12, decimal_places=2, read_only=True)

    class Meta:
        model = ComboItem
        fields = ['id', 'product', 'product_name', 'product_sku', 'product_price', 'quantity']


class ComboSerializer(serializers.ModelSerializer):
    items = ComboItemSerializer(many=True, read_only=True)
    components_total = serializers.SerializerMethodField()
    computed_price = serializers.SerializerMethodField()

    class Meta:
        model = Combo
        fields = [
            'id',
            'name',
            'code',
            'description',
            'is_active',
            'discount_type',
            'discount_value',
            'items',
            'components_total',
            'computed_price',
        ]

    def get_components_total(self, obj):
        return obj.components_total()

    def get_computed_price(self, obj):
        return obj.compute_price()
