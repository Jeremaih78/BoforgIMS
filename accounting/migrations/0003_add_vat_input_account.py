from django.db import migrations


def add_vat_input(apps, schema_editor):
    Account = apps.get_model('accounting', 'Account')
    if not Account.objects.filter(code='1410').exists():
        Account.objects.create(code='1410', name='Tax/VAT Input', type='ASSET')


class Migration(migrations.Migration):
    dependencies = [
        ('accounting', '0002_seed_defaults'),
    ]

    operations = [
        migrations.RunPython(add_vat_input, migrations.RunPython.noop),
    ]

