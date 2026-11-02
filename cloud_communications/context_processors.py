from django.conf import settings


def navigation(request):
    return {
        "cloud_communications_enabled": settings.CLOUD_COMMUNICATIONS_ENABLED,
        "cloud_auto_invoice": settings.CLOUD_COMMUNICATIONS_ENABLED and settings.CLOUD_COMMUNICATIONS_AUTO_INVOICE,
        "cloud_auto_quotation": settings.CLOUD_COMMUNICATIONS_ENABLED and settings.CLOUD_COMMUNICATIONS_AUTO_QUOTATION,
    }
