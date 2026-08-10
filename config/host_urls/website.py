"""Public company website URL configuration."""

from django.urls import include, path

from config.host_urls.common import with_debug_media
from config.host_urls.redirects import legacy_host_redirect
from legal.views import return_policy


urlpatterns = [
    # Keep old public entry points usable without exposing the applications on
    # the website host. More-specific AI redirects must precede the IMS route.
    path(
        "ims/tasker/",
        legacy_host_redirect,
        {"host_name": "ai"},
        name="legacy_ai_root",
    ),
    path(
        "ims/tasker/<path:path>",
        legacy_host_redirect,
        {"host_name": "ai"},
        name="legacy_ai_path",
    ),
    path("shop/", legacy_host_redirect, {"host_name": "shop"}, name="legacy_shop_root"),
    path(
        "shop/<path:path>",
        legacy_host_redirect,
        {"host_name": "shop"},
        name="legacy_shop_path",
    ),
    path("ims/", legacy_host_redirect, {"host_name": "ims"}, name="legacy_ims_root"),
    path(
        "ims/<path:path>",
        legacy_host_redirect,
        {"host_name": "ims"},
        name="legacy_ims_path",
    ),
    path("admin/", legacy_host_redirect, {"host_name": "ims", "path": "admin/"}),
    path(
        "admin/<path:path>",
        legacy_host_redirect,
        {"host_name": "ims"},
    ),
    path("accounts/", legacy_host_redirect, {"host_name": "ims", "path": "accounts/"}),
    path(
        "accounts/<path:path>",
        legacy_host_redirect,
        {"host_name": "ims"},
    ),
    path("return-policy/", return_policy, name="return_policy"),
    path("legal/", include(("legal.urls", "legal"), namespace="legal")),
    path("", include(("website.urls", "website"), namespace="website")),
]

urlpatterns = with_debug_media(urlpatterns)
