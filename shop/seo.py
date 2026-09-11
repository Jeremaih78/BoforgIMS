from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from inventory.models import Category
from .selectors import public_products


class ProductSitemap(Sitemap):
    limit = 1000
    def items(self):
        return public_products().order_by('pk')
    def location(self, item):
        return reverse('shop:product_detail', args=[item.slug])
    def lastmod(self, item):
        return item.updated_at


class CategorySitemap(Sitemap):
    limit = 1000
    def items(self):
        return Category.objects.filter(product__in=public_products()).distinct()
    def location(self, item):
        return reverse('shop:catalog') + '?category=' + item.slug
