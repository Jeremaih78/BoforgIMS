from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from .models import Product, Combo
from .serializers import ProductSerializer, ComboSerializer


class ProductViewSet(ModelViewSet):
    queryset = Product.objects.all().order_by('name')
    serializer_class = ProductSerializer
    parser_classes = [MultiPartParser, FormParser]

    def perform_update(self, serializer):
        instance = serializer.instance
        previous_image = instance.image if instance.image else None
        product = serializer.save()
        new_image = getattr(product, 'image', None)
        if previous_image and new_image and previous_image.name != new_image.name:
            previous_image.delete(save=False)
        if previous_image and not new_image:
            previous_image.delete(save=False)
        return product

    def perform_destroy(self, instance):
        if instance.image:
            instance.image.delete(save=False)
        super().perform_destroy(instance)

class ComboViewSet(ReadOnlyModelViewSet):
    queryset = Combo.objects.filter(is_active=True).prefetch_related('items__product').order_by('name')
    serializer_class = ComboSerializer
    lookup_field = 'code'

    @action(detail=True, methods=['get'])
    def price(self, request, code=None):
        combo = self.get_object()
        qty = int(request.query_params.get('qty', 1))
        if qty < 1:
            qty = 1
        components_total = combo.components_total() * qty
        computed_price = combo.compute_price() * qty
        return Response({
            'quantity': qty,
            'components_total': components_total,
            'computed_price': computed_price,
        })


