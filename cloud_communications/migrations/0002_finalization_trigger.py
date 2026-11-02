from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("cloud_communications", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="communicationrequest", name="trigger",
            field=models.CharField(default="manual", max_length=20),
        ),
        migrations.AddConstraint(
            model_name="communicationrequest",
            constraint=models.UniqueConstraint(
                fields=("invoice",), condition=models.Q(trigger="finalized"),
                name="comms_auto_invoice_once",
            ),
        ),
        migrations.AddConstraint(
            model_name="communicationrequest",
            constraint=models.UniqueConstraint(
                fields=("quotation",), condition=models.Q(trigger="finalized"),
                name="comms_auto_quotation_once",
            ),
        ),
    ]
