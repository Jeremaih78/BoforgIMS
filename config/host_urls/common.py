"""Small URLConf helpers shared by Boforg hosts."""

from django.conf import settings
from django.conf.urls.static import static
from django.urls import path
from core.health import live,ready


def health_patterns():
    return [path("health/live/",live,name="ims_health_live"),path("health/ready/",ready,name="ims_health_ready")]


def with_debug_media(urlpatterns):
    """Serve uploaded media through Django only while developing."""
    urlpatterns=[*health_patterns(),*urlpatterns]
    if settings.DEBUG:
        return [*urlpatterns, *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)]
    return urlpatterns
