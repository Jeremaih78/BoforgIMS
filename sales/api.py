from django.db import transaction
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from sales.models import DocumentLine
from inventory.models import ProductUnit, Product


class InvoiceLineSerialAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get_line(self, line_id):
        return DocumentLine.objects.select_related('invoice', 'product').get(pk=line_id)

    def get(self, request, line_id):
        line = self.get_line(line_id)
        if not line.product or line.product.tracking_mode != Product.TRACK_SERIAL:
            return Response({'detail': 'Line does not require serials.'}, status=status.HTTP_400_BAD_REQUEST)
        assigned = list(line.product_units.values_list('serial_number', flat=True))
        available_qs = ProductUnit.objects.filter(product=line.product, status=ProductUnit.STATUS_AVAILABLE)
        available = list(available_qs.values_list('serial_number', flat=True))
        return Response({
            'line_id': line.id,
            'invoice_id': line.invoice_id,
            'product': line.product.sku,
            'quantity_required': int(line.quantity),
            'assigned_serials': assigned,
            'available_serials': available,
        })

    @transaction.atomic
    def post(self, request, line_id):
        line = self.get_line(line_id)
        if not line.product or line.product.tracking_mode != Product.TRACK_SERIAL:
            return Response({'detail': 'Line does not require serials.'}, status=status.HTTP_400_BAD_REQUEST)
        serials = request.data.get('serial_numbers') or []
        if not isinstance(serials, list):
            return Response({'detail': 'serial_numbers must be a list.'}, status=status.HTTP_400_BAD_REQUEST)
        required = int(line.quantity)
        cleaned = [str(s).strip() for s in serials if str(s).strip()]
        if len(cleaned) != required:
            return Response({'detail': f'{line.product} requires exactly {required} serial numbers.'}, status=status.HTTP_400_BAD_REQUEST)
        units = list(ProductUnit.objects.select_for_update().filter(serial_number__in=cleaned, product=line.product))
        if len(units) != required:
            return Response({'detail': 'Some serials are invalid or belong to a different product.'}, status=status.HTTP_400_BAD_REQUEST)
        for unit in units:
            if unit.status not in (ProductUnit.STATUS_AVAILABLE, ProductUnit.STATUS_RESERVED) and unit.sale_line_id != line.id:
                return Response({'detail': f'Serial {unit.serial_number} is not available.'}, status=status.HTTP_400_BAD_REQUEST)
        ProductUnit.objects.filter(sale_line=line).exclude(serial_number__in=cleaned).update(
            sale_line=None,
            status=ProductUnit.STATUS_AVAILABLE,
            sold_at=None,
        )
        for unit in units:
            unit.sale_line = line
            unit.status = ProductUnit.STATUS_RESERVED
            unit.save(update_fields=['sale_line', 'status', 'updated_at'])
        return Response({'assigned_serials': cleaned})
