from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView

import base64
import hashlib
import hmac
import json
import os
import uuid


class PrivacyView(TemplateView):
    template_name = "legal/privacy.html"


class TermsView(TemplateView):
    template_name = "legal/terms.html"


class DataDeletionView(TemplateView):
    template_name = "legal/data_deletion.html"


def _parse_signed_request(signed_request: str, app_secret: str):
    encoded_sig, payload = signed_request.split(".", 1)

    def _pad(value: str) -> str:
        return value + "=" * (-len(value) % 4)

    sig = base64.urlsafe_b64decode(_pad(encoded_sig))
    data_bytes = base64.urlsafe_b64decode(_pad(payload))

    expected_sig = hmac.new(
        app_secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).digest()

    if not hmac.compare_digest(sig, expected_sig):
        return None

    try:
        return json.loads(data_bytes.decode("utf-8"))
    except Exception:
        return None


@csrf_exempt
def facebook_data_deletion(request):
    if request.method != "POST":
        return HttpResponseBadRequest("POST required")

    app_secret = os.environ.get(
        "META_APP_SECRET",
        getattr(settings, "META_APP_SECRET", ""),
    )
    if not app_secret:
        return HttpResponseBadRequest("App secret not configured")

    signed_request = request.POST.get("signed_request")
    if not signed_request and request.body:
        try:
            body = json.loads(request.body.decode("utf-8"))
            signed_request = body.get("signed_request")
        except Exception:
            pass

    if not signed_request:
        return HttpResponseBadRequest("signed_request missing")

    data = _parse_signed_request(signed_request, app_secret)
    if not data:
        return HttpResponseBadRequest("invalid signed_request")

    confirmation_code = str(uuid.uuid4())
    base_url = getattr(settings, "PUBLIC_BASE_URL", "https://boforg.co.zw")

    return JsonResponse(
        {
            "url": f"{base_url}/legal/data-deletion/",
            "confirmation_code": confirmation_code,
        }
    )
