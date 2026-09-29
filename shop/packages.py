from inventory.models import Combo
from .context_processors import whatsapp_url


def public_packages(combo_ids=None, limit=12):
    packages = []
    queryset = Combo.objects.filter(is_active=True, is_public=True).prefetch_related('items__product')
    if combo_ids is not None:
        queryset = queryset.filter(pk__in=combo_ids)
    if limit is not None:
        queryset = queryset[:limit]
    for combo in queryset:
        items = list(combo.items.all())
        if not items or any(not i.product.is_public or not i.product.is_active or i.product.price <= 0 or i.quantity < 1 for i in items):
            continue
        currencies = {i.product.currency for i in items}
        if len(currencies) != 1 or combo.compute_price() <= 0:
            continue
        limits = [i.product.available_stock // i.quantity for i in items if i.product.track_inventory]
        combo.shop_available = min(limits) if limits else 9999
        combo.shop_currency = currencies.pop()
        combo.shop_whatsapp = whatsapp_url(f'Hello Boforg, please help me with {combo.name} ({combo.code}), {combo.shop_currency} {combo.compute_price()}. Please confirm the included training and support.')
        packages.append(combo)
    return packages
