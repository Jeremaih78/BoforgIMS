"""Read-only diagnostics by default. Applying facts is a separate local/staging operation."""

import json
from django.core.management.base import BaseCommand
from tasker.cloud_email_models import StaffEmailBinding


class Command(BaseCommand):
    help = "Read-only local staff email ledger summary. Never retries or sends."

    def handle(self, *args, **options):
        from django.db.models import Count

        rows = list(
            StaffEmailBinding.objects.values("phase", "remote_state").annotate(count=Count("pk"))
        )
        self.stdout.write(json.dumps({"read_only": True, "groups": rows}))
