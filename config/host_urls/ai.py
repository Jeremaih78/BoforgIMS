"""AI and Tasker-only URL configuration."""

from django.urls import include, path

from config.host_urls.common import with_debug_media


# Preserve the established ``ims:tasker:*`` namespace contract while serving
# Tasker at the root of ai.boforg.co.zw.
ai_patterns = (
    [
        path("", include(("tasker.urls", "tasker"), namespace="tasker")),
    ],
    "ims",
)

urlpatterns = [
    path("", include(ai_patterns, namespace="ims")),
    path("accounts/", include("django.contrib.auth.urls")),
]

urlpatterns = with_debug_media(urlpatterns)
