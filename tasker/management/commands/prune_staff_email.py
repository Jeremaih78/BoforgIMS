from datetime import timedelta
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from tasker.cloud_email_models import StaffEmailBinding


class Command(BaseCommand):
    help = "Preview staff-email payload minimization. Identity/receipts/UNKNOWN remain."

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=90)
        parser.add_argument("--execute", action="store_true")

    def handle(self, *args, **options):
        if options["days"] < 30:
            raise CommandError("Retention must be at least 30 days.")
        query = StaffEmailBinding.objects.filter(
            created_at__lt=timezone.now() - timedelta(days=options["days"]),
            delivery__state__in=("PROVIDER_ACCEPTED", "CANCELLED", "FAILED"),
        ).exclude(payload={})
        count = query.count()
        if options["execute"]:
            query.update(payload={})
        self.stdout.write(
            f"{'Minimized' if options['execute'] else 'Would minimize'} {count} payloads; identities and receipts retained."
        )
