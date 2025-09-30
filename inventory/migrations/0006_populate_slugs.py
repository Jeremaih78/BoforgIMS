from django.conf import settings
from django.db import migrations
from django.utils import timezone
from django.utils.text import slugify


def _populate_category_slugs(Category):
    for category in Category.objects.all():
        if category.slug:
            continue
        base = slugify(category.name) or f"category-{category.pk}"
        slug = base
        idx = 1
        while Category.objects.filter(slug=slug).exclude(pk=category.pk).exists():
            slug = f"{base}-{idx}"
            idx += 1
        category.slug = slug
        category.save(update_fields=['slug'])


def _populate_product_fields(Product):
    default_currency = getattr(settings, 'BASE_CURRENCY_CODE', 'USD')
    for product in Product.objects.all().select_related('category'):
        update_fields = []
        if not product.slug:
            base_value = product.name or product.sku or f"product-{product.pk}"
            base = slugify(base_value) or f"product-{product.pk}"
            slug = base
            idx = 1
            while Product.objects.filter(slug=slug).exclude(pk=product.pk).exists():
                slug = f"{base}-{idx}"
                idx += 1
            product.slug = slug
            update_fields.append('slug')
        if not product.currency:
            product.currency = default_currency
            update_fields.append('currency')
        if not product.created_at:
            product.created_at = timezone.now()
            update_fields.append('created_at')
        if update_fields:
            product.save(update_fields=update_fields)


def forwards(apps, schema_editor):
    Category = apps.get_model('inventory', 'Category')
    Product = apps.get_model('inventory', 'Product')
    _populate_category_slugs(Category)
    _populate_product_fields(Product)


def backwards(apps, schema_editor):
    # No-op: we do not want to remove slugs if they exist.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_alter_category_options_alter_product_options_and_more'),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
