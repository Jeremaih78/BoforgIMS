"""Run the one Tasker outbox locally or under an external process supervisor."""
import json
import time

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from tasker.services.jobs import run_worker_once


class Command(BaseCommand):
    help = "Process bounded Tasker outbox jobs; --loop requires external supervision."

    def add_arguments(self, parser):
        parser.add_argument("--worker-id", default="local-worker")
        parser.add_argument("--limit", type=int, default=25)
        parser.add_argument("--lease-seconds", type=int, default=120)
        parser.add_argument("--loop", action="store_true")
        parser.add_argument("--once", action="store_true", help="Run one pass (the default).")
        parser.add_argument("--poll-seconds", type=float, default=2)

    def handle(self, *args, **options):
        if options["once"] and options["loop"]:
            raise CommandError("Choose either --once or --loop.")
        if not 0.2 <= options["poll_seconds"] <= 60:
            raise CommandError("Choose a polling interval from 0.2 to 60 seconds.")
        try:
            while True:
                results = run_worker_once(worker_id=options["worker_id"], limit=options["limit"],
                    lease_seconds=options["lease_seconds"])
                self.stdout.write(json.dumps({"processed": len(results), "outcomes": results}))
                if not options["loop"]:
                    break
                time.sleep(options["poll_seconds"])
        except KeyboardInterrupt:
            self.stdout.write("Worker stopped; uncommitted effects roll back and expired leases can be recovered.")
        except ValidationError as error:
            raise CommandError(" ".join(error.messages)) from None
