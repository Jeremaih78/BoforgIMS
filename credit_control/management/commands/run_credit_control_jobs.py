from django.core.management.base import BaseCommand

from credit_control.tasks import run_daily_credit_control_jobs


class Command(BaseCommand):
    help = "Run daily credit control sync, task generation, and alert stubs"

    def handle(self, *args, **options):
        result = run_daily_credit_control_jobs()
        self.stdout.write(self.style.SUCCESS(f"Credit control jobs completed: {result}"))
