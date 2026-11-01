from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from core.permissions import ims_permission
from inventory.models import Combo
from .combo_catalog import combo_details


@login_required
@ims_permission('inventory.view_product', staff=True)
@require_GET
@never_cache
def combo_search(request):
    query = request.GET.get('q', '').strip()[:100]
    combos = Combo.objects.filter(is_active=True, items__isnull=False).exclude(
        items__product__is_active=False
    ).exclude(items__quantity__lte=0).exclude(items__product__price__lt=0).filter(
        discount_value__gte=0
    ).annotate(component_currencies=Count('items__product__currency', distinct=True)).filter(component_currencies=1)
    if query:
        for word in query.split():
            combos = combos.filter(
                Q(name__icontains=word) | Q(code__icontains=word) | Q(description__icontains=word)
                | Q(items__product__name__icontains=word) | Q(items__product__sku__icontains=word)
            )
    candidates = list(combos.distinct().order_by('name', 'code').prefetch_related('items__product')[:21])
    rows = [combo_details(combo) for combo in candidates]
    return JsonResponse({'results': rows[:20], 'has_more': len(rows) > 20})
