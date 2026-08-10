"""Inventory Management System-only URL configuration."""

from django.contrib import admin
from django.urls import include, path

from config.host_urls.common import with_debug_media
from users.views import dashboard


ims_patterns = (
    [
        path("", dashboard, name="dashboard"),
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
