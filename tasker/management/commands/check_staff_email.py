"""Offline readiness; never submits, authenticates SMTP or reads secret values."""

import json
from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Show offline staff email gates; no database or network access."

    def handle(self, *args, **options):
        self.stdout.write(
            json.dumps(
                {
                    "offline": True,
                    "staff_gate": getattr(settings, "TASKER_CLOUD_EMAIL_ENABLED", False),
                    "report_gate": getattr(settings, "TASKER_REPORT_EMAIL_LIVE_ENABLED", False),
                    "mapping_configured": bool(getattr(settings, "TASKER_CLOUD_EMAIL_CLIENTS", {})),
                    "eligibility_keys_configured": bool(
                        getattr(settings, "TASKER_CLOUD_EMAIL_KEYS", {})
                    ),
                    "provider_verified": False,
                    "company_policies_checked": False,
                }
            )
        )
