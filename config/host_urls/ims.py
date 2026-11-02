"""Inventory Management System-only URL configuration."""

from django.contrib import admin
from django.urls import include, path

from config.host_urls.common import with_debug_media
from users.views import dashboard, dashboard_lookup
from core.views import private_document


ims_patterns = (
    [
        path('operations/', include(('operations.urls', 'operations'), namespace='operations')),
        path("documents/<path:name>", private_document, name="private_document"),
        path("", dashboard, name="dashboard"),
        path("search/", dashboard_lookup, name="dashboard_lookup"),
        path(
            "inventory/",
            include(("inventory.urls", "inventory"), namespace="inventory"),
        ),
        path(
            "customers/",
            include(("customers.urls", "customers"), namespace="customers"),
        ),
        path("sales/", include(("sales.urls", "sales"), namespace="sales")),
        path(
            "accounting/",
            include(("accounting.urls", "accounting"), namespace="accounting"),
        ),
        path(
            "credit-control/",
            include(
                ("credit_control.urls", "credit_control"),
                namespace="credit_control",
            ),
        ),
    ],
    "ims",
)

urlpatterns = [
    path("", include(ims_patterns, namespace="ims")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]

urlpatterns = with_debug_media(urlpatterns)

handler400="core.errors.bad_request"
handler403="core.errors.forbidden"
handler404="core.errors.not_found"
handler500="core.errors.server_error"
