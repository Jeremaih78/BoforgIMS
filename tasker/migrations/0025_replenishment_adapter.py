"""Keep Tasker's enabled adapter schema aligned with its canonical source."""
from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ('tasker', '0024_businesssynccursor_businessrecordlink_and_more'),
        ('inventory', '0019_replenishmentplan_replenishmentevent_and_more'),
    ]
    operations = []
