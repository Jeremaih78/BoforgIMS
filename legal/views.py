from django.conf import settings
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.utils.decorators import method_decorator
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


def _get_company_profile():
    return {
        "name": "Boforg Technologies Private Limited",
        "phone": "+263786264994",
        "email": "adriannzvimbo@gmail.com",
        "address": "Robert Mugabe Street, Harare, Zimbabwe",
        "cta_whatsapp": "https://wa.me/263786264994",
    }


def return_policy(request):
    return render(
        request,
        "legal/return_policy.html",
        {"now": timezone.now(), "company": _get_company_profile()},
    )


def _parse_signed_request(signed_request: str, app_secret: str):
    try:
        encoded_sig, payload = signed_request.split('.', 1)

        def _pad(b):
            return b + '=' * (-len(b) % 4)

        sig = base64.urlsafe_b64decode(_pad(encoded_sig))
        data_bytes = base64.urlsafe_b64decode(_pad(payload))
        expected_sig = hmac.new(
            app_secret.encode("utf-8"),
            msg=payload.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(sig, expected_sig):
            return None
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

    # Example: delete data for data.get('user_id') if you store any references
    # TODO: Your deletion logic here, e.g.
    # Integration.objects.filter(fb_user_id=data.get('user_id')).delete()

    confirmation_code = str(uuid.uuid4())
    base_url = getattr(settings, "PUBLIC_BASE_URL", "https://boforg.co.zw")
    return JsonResponse(
        {
            "url": f"{base_url}/legal/data-deletion/",
            "confirmation_code": confirmation_code,
        }
    )
