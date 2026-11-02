from django.conf import settings


def navigation(request):
    return {"cloud_communications_enabled": settings.CLOUD_COMMUNICATIONS_ENABLED}
