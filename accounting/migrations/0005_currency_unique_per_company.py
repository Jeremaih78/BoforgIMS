from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('accounting', '0004_expense_status_expensecategory_default_tax_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='currency',
            name='code',
            field=models.CharField(max_length=3),
        ),
        migrations.AlterUniqueTogether(
            name='currency',
            unique_together={('company', 'code')},
        ),
    ]

