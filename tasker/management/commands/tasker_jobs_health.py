"""Content-free outbox diagnostics suitable for a process-supervisor check."""
import json

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tasker.services.jobs import jobs_health


class Command(BaseCommand):
    help = "Print Tasker job counts, lease recovery and worker-heartbeat health without content."

    def add_arguments(self, parser):
        parser.add_argument("--stale-seconds", type=int, default=300)
        parser.add_argument("--fail-on-unhealthy", action="store_true")

    def handle(self, *args, **options):
        try:
            result = jobs_health(stale_seconds=options["stale_seconds"])
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
        self.stdout.write(json.dumps(result))
        if options["fail_on_unhealthy"] and not result["healthy"]:
            raise CommandError("Tasker jobs need attention; inspect failed jobs and supervised processes.")
