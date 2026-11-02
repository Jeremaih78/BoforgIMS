"""AI and Tasker-only URL configuration."""

from django.urls import include, path

from config.host_urls.common import with_debug_media
from core.views import private_document


# Preserve the established ``ims:tasker:*`` namespace contract while serving
# Tasker at the root of ai.boforg.co.zw.
ai_patterns = (
    [
        path("documents/<path:name>", private_document, name="private_document"),
        path("", include(("tasker.urls", "tasker"), namespace="tasker")),
    ],
    "ims",
)

urlpatterns = [
    path("", include(ai_patterns, namespace="ims")),
    path("accounts/", include("django.contrib.auth.urls")),
]

urlpatterns = with_debug_media(urlpatterns)

handler400="core.errors.bad_request"
handler403="core.errors.forbidden"
handler404="core.errors.not_found"
handler500="core.errors.server_error"
