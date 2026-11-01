from django.shortcuts import render
from core.permissions import ims_permission
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from .dashboard import dashboard_context, dashboard_search

@login_required
@ims_permission('sales.view_invoice', staff=True)
@never_cache
def dashboard(request):
    context = dashboard_context(request.user, request.GET)
    query = request.GET.get('q', '').strip()[:100]
    context.update({'search_query': query, 'search_results': dashboard_search(request.user, query, context['dashboard_permissions'])})
    return render(request, 'dashboard.html', context)


@login_required
@ims_permission('sales.view_invoice', staff=True)
@require_GET
@never_cache
def dashboard_lookup(request):
    return JsonResponse({'results': dashboard_search(request.user, request.GET.get('q', ''))})
