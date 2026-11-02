import signal
from threading import Event

from django.core.management.base import BaseCommand, CommandError
from django.db import close_old_connections

from cloud_communications.client import APIError
from cloud_communications.services import process_requests, reconcile


class Command(BaseCommand):
    help = "Submit the local IMS outbox and reconcile Cloud status without blocking business workflows."

    def add_arguments(self, parser):
        parser.add_argument("--watch", action="store_true")
        parser.add_argument("--limit", type=int, default=50)

    def handle(self, *args, **options):
        stop = Event()
        if options["watch"]:
            for sig in (signal.SIGTERM, signal.SIGINT):
                signal.signal(sig, lambda *_: stop.set())
        while not stop.is_set():
            close_old_connections()
            try:
                processed = process_requests(limit=max(1, min(options["limit"], 100)))
                reconciled = reconcile(limit=max(1, min(options["limit"], 100)))
            except APIError as error:
                raise CommandError(error.code) from None
            self.stdout.write(f"Submitted {processed}; reconciled {reconciled}.")
            if not options["watch"]:
                break
            stop.wait(10)
