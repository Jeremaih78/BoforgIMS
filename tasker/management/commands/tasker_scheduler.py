"""Run Tasker's bounded repair sweeps; no platform scheduler is installed here."""
import json
import time

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tasker.services.jobs import scheduler_tick


class Command(BaseCommand):
    help = "Run Tasker reconciliation and optional retention; --loop needs external supervision."

    def add_arguments(self, parser):
        parser.add_argument("--scheduler-id", default="local-scheduler")
        parser.add_argument("--limit", type=int, default=100)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--once", action="store_true", help="Run one sweep (the default).")
        parser.add_argument("--poll-seconds", type=float, default=30)
        parser.add_argument("--prune", action="store_true", help="Clear old completed payload detail, retaining dedupe tombstones.")
        parser.add_argument("--retention-days", type=int)

    def handle(self, *args, **options):
        if options["once"] and options["loop"]:
            raise CommandError("Choose either --once or --loop.")
        if not 1 <= options["poll_seconds"] <= 60:
            raise CommandError("Choose a reconciliation interval from 1 to 60 seconds.")
        try:
            while True:
                result = scheduler_tick(scheduler_id=options["scheduler_id"], limit=options["limit"],
                    prune=options["prune"], retention_days=options["retention_days"])
                self.stdout.write(json.dumps(result, default=str))
                if not options["loop"]:
                    break
                time.sleep(options["poll_seconds"])
        except KeyboardInterrupt:
            self.stdout.write("Scheduler stopped; durable queued work remains available.")
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
