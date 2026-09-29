"""Shared public catalogue boundary. Never use this to hide IMS records."""
from inventory.models import Product


def public_products():
    return Product.objects.filter(is_active=True, is_public=True, price__gt=0).select_related('category')
