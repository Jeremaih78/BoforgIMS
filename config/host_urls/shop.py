"""Online shop-only URL configuration."""

from django.urls import include, path

from config.host_urls.common import with_debug_media


urlpatterns = [
    path("", include(("shop.urls", "shop"), namespace="shop")),
]

urlpatterns = with_debug_media(urlpatterns)

handler400="core.errors.bad_request"
handler403="core.errors.forbidden"
handler404="core.errors.not_found"
handler500="core.errors.server_error"
