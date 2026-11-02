"""REST API-only URL configuration."""

from django.http import JsonResponse
from django.urls import path
from rest_framework import permissions
from rest_framework.routers import DefaultRouter

from inventory.api import ComboViewSet, ProductViewSet, ShipmentViewSet
from sales.api import InvoiceLineSerialAPIView


class APIProductViewSet(ProductViewSet):
    """Expose catalogue reads publicly but require authentication for writes."""

    permission_classes = ProductViewSet.permission_classes


def api_index(request):
    return JsonResponse(
        {
            "name": "Boforg API",
            "version": "v1",
            "endpoints": {
                "products": "/inventory/products/",
                "combos": "/inventory/combos/",
                "shipments": "/inventory/shipments/",
            },
        }
    )


def healthcheck(request):
    return JsonResponse({"ok": True, "service": "api"})


router = DefaultRouter()
router.register("inventory/products", APIProductViewSet, basename="product")
router.register("inventory/combos", ComboViewSet, basename="combo")
router.register("inventory/shipments", ShipmentViewSet, basename="shipment")

urlpatterns = [
    path("", api_index, name="api_index"),
    path("healthz/", healthcheck, name="healthcheck"),
    path(
        "sales/invoice-lines/<int:line_id>/serials/",
        InvoiceLineSerialAPIView.as_view(),
        name="invoice_line_serials",
    ),
    *router.urls,
]

from config.host_urls.common import health_patterns
urlpatterns=health_patterns()+urlpatterns

handler400="core.errors.bad_request"
handler403="core.errors.forbidden"
handler404="core.errors.not_found"
handler500="core.errors.server_error"
