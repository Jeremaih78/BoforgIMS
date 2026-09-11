from django.db import transaction
from django.shortcuts import get_object_or_404
from core.permissions import InvoiceSerialPermission
from sales.services.serials import assign_serials
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from sales.models import DocumentLine
from inventory.models import ProductUnit, Product


class InvoiceLineSerialAPIView(APIView):
    permission_classes = [InvoiceSerialPermission]

    def get_line(self, line_id):
        return get_object_or_404(DocumentLine.objects.select_related('invoice', 'product'), pk=line_id, invoice__isnull=False)

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

    def post(self, request, line_id):
        self.get_line(line_id)
        try:
            serials = assign_serials(line_id=line_id, serials=request.data.get('serial_numbers', []))
        except ValueError as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response({'assigned_serials': serials})
