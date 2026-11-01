from django.db.models import F, Q, Sum, Value
from django.db.models.functions import Greatest
from django.utils import timezone

from .models import Product, Shipment


def low_stock_products(queryset=None):
    qs = Product.objects.all() if queryset is None else queryset
    return qs.filter(is_active=True, track_inventory=True).annotate(
        available_for_reorder=Greatest(F('quantity') - F('reserved'), Value(0))
    ).filter(available_for_reorder__lte=F('reorder_level'))


def shipment_followups(queryset=None, today=None):
    today = today or timezone.localdate()
    qs = Shipment.objects.all() if queryset is None else queryset
    ready = Q(status__in=[Shipment.STATUS_ARRIVED, Shipment.STATUS_CLEARED])
    late = Q(status__in=[Shipment.STATUS_CREATED, Shipment.STATUS_IN_TRANSIT], eta_date__lt=today)
    return qs.filter(ready | late).annotate(
        expected=Sum('items__quantity_expected'), received=Sum('items__quantity_received')
    ).filter(late | Q(expected__gt=F('received')))
