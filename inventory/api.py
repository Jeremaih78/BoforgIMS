from django.db.models import F
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import transaction
from django.db.models.deletion import ProtectedError
from rest_framework.exceptions import ValidationError
from core.permissions import CataloguePermission, ShipmentPermission, allowed
from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.parsers import FormParser, MultiPartParser, JSONParser
from rest_framework.response import Response
from rest_framework.viewsets import ModelViewSet, ReadOnlyModelViewSet

from inventory.services import receive_shipment, ShipmentServiceError

from .models import Product, Combo, Shipment
from .serializers import ProductSerializer, ComboSerializer, ShipmentSerializer, ShipmentItemSerializer


class ProductViewSet(ModelViewSet):
    queryset = Product.objects.all().order_by('name')
    serializer_class = ProductSerializer
    permission_classes = [CataloguePermission]
    parser_classes = [MultiPartParser, FormParser]

    def get_queryset(self):
        if allowed(self.request.user, 'inventory.view_product', staff=True):
            return super().get_queryset()
        from shop.selectors import public_products
        return public_products().order_by('name')

    @transaction.atomic
    def perform_update(self, serializer):
        instance = Product.objects.select_for_update().get(pk=serializer.instance.pk)
        serializer.instance = instance
        previous_image = instance.image if instance.image else None
        # Quantity mutations are handled exclusively by stock movements.
        serializer.validated_data.pop('quantity', None)
        product = serializer.save()
        new_image = getattr(product, 'image', None)
        if previous_image and (not new_image or previous_image.name != new_image.name):
            transaction.on_commit(lambda: previous_image.delete(save=False))
        return product

    @transaction.atomic
    def perform_destroy(self, instance):
        previous_image = instance.image if instance.image else None
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError('Product has transaction history; mark it inactive instead.')
        if previous_image:
            transaction.on_commit(lambda: previous_image.delete(save=False))

class ComboViewSet(ReadOnlyModelViewSet):
    queryset = Combo.objects.filter(is_active=True).prefetch_related('items__product').order_by('name')
    serializer_class = ComboSerializer
    lookup_field = 'code'

    def get_queryset(self):
        if allowed(self.request.user, 'inventory.view_combo', staff=True):
            return super().get_queryset()
        from shop.packages import public_packages
        return super().get_queryset().filter(pk__in=[c.pk for c in public_packages(limit=None)])

    @action(detail=True, methods=['get'])
    def price(self, request, code=None):
        combo = self.get_object()
        try:
            qty = int(request.query_params.get('qty', 1))
        except (TypeError, ValueError):
            return Response({'detail': 'Quantity must be an integer.'}, status=400)
        if qty < 1:
            qty = 1
        components_total = combo.components_total() * qty
        computed_price = combo.compute_price() * qty
        return Response({
            'quantity': qty,
            'components_total': components_total,
            'computed_price': computed_price,
        })


class ShipmentViewSet(ReadOnlyModelViewSet):
    queryset = Shipment.objects.select_related('supplier').prefetch_related('items__product').order_by('-created_at')
    serializer_class = ShipmentSerializer
    permission_classes = [ShipmentPermission]

    def get_queryset(self):
        qs = super().get_queryset()
        status_param = self.request.query_params.get('status')
        if status_param:
            qs = qs.filter(status=status_param)
        return qs

    @action(detail=True, methods=['get'], permission_classes=[ShipmentPermission], url_path='pending-items')
    def pending_items(self, request, pk=None):
        shipment = self.get_object()
        items = shipment.items.filter(quantity_received__lt=F('quantity_expected')).select_related('product')
        serializer = ShipmentItemSerializer(items, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[ShipmentPermission], parser_classes=[JSONParser])
    def receive(self, request, pk=None):
        shipment = self.get_object()
        receipts = request.data.get('receipts', [])
        if not isinstance(receipts, list):
            return Response({'detail': 'Receipts must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            receive_shipment(
                shipment_id=shipment.id,
                receipts=receipts,
                received_by=request.user,
            )
        except (ShipmentServiceError, DjangoValidationError) as exc:
            message = exc.messages[0] if isinstance(exc.messages, list) else str(exc)
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)
        serializer = self.get_serializer(self.get_object())
        return Response(serializer.data, status=status.HTTP_200_OK)


