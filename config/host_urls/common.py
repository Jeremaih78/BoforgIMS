"""Small URLConf helpers shared by Boforg hosts."""

from django.conf import settings
from django.conf.urls.static import static


def with_debug_media(urlpatterns):
    """Serve uploaded media through Django only while developing."""
    if settings.DEBUG:
        return [*urlpatterns, *static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)]
    return urlpatterns
