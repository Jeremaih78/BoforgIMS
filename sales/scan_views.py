from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST

from core.permissions import allowed
from inventory.identity_views import json_errors
from sales.models import Invoice, Quotation
from sales.services.scanning import scan_document


@login_required
@require_POST
@json_errors
def scan(request, kind, pk):
    if kind not in ('invoice', 'quotation') or not allowed(request.user, f'sales.change_{kind}', staff=True):
        raise PermissionDenied
    get_object_or_404(Invoice if kind == 'invoice' else Quotation, pk=pk)
    return JsonResponse(scan_document(document_id=pk, kind=kind, code=request.POST.get('code', ''),
        key=request.POST.get('key'), actor=request.user))
