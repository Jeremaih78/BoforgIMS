from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse
from django_hosts.resolvers import reverse as host_reverse


def public_url(request, name, **kwargs):
    namespace = name.split(':')[0]
    urlconf = getattr(request, 'urlconf', settings.ROOT_URLCONF)
    if urlconf == 'config.urls' or urlconf == f'config.host_urls.{namespace}':
        return reverse(name, urlconf=urlconf, **kwargs)
    return host_reverse(name, host=namespace, **kwargs)


def whatsapp_url(message):
    number = ''.join(c for c in settings.BOFORG_WHATSAPP_NUMBER if c.isdigit())
    return f'https://wa.me/{number}?{urlencode({"text": message})}'


def storefront(request):
    match = getattr(request, 'resolver_match', None)
    if match and not set(match.app_names).intersection({'website', 'shop', 'legal'}):
        return {}
    count = 0
    session = getattr(request, 'session', None)
    if session and session.session_key:
        from django.db.models import Sum
        from .models import CartItem
        count = CartItem.objects.filter(cart__session_key=session.session_key).aggregate(n=Sum('quantity'))['n'] or 0
    if session and session.session_key:
        from .models import CartPackage
        count += CartPackage.objects.filter(cart__session_key=session.session_key).aggregate(n=Sum('quantity'))['n'] or 0
    return {
        'store_home': public_url(request, 'website:home'),
        'store_shop': public_url(request, 'shop:catalog'),
        'store_cart': public_url(request, 'shop:cart_detail'),
        'store_package_add': public_url(request, 'shop:package_add'),
        'store_count': count,
        'store_events': session.pop('store_events', []) if session is not None else [],
        'store_whatsapp': whatsapp_url('Hello Boforg Technologies, please help me choose the right equipment or supplies.'),
    }
