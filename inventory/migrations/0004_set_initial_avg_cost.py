from django.db import migrations


def set_initial_avg_cost(apps, schema_editor):
    Product = apps.get_model('inventory','Product')
    for p in Product.objects.all():
        if (p.avg_cost or 0) == 0:
            p.avg_cost = p.price or 0
            p.save(update_fields=['avg_cost'])


class Migration(migrations.Migration):
    dependencies = [
        ('inventory', '0003_product_avg_cost_stockmovement_unit_cost'),
    ]

    operations = [
        migrations.RunPython(set_initial_avg_cost, migrations.RunPython.noop),
    ]

