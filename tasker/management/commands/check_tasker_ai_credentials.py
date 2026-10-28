"""Offline presence checks only; never authenticate or display credentials."""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Check Tasker credential configuration without displaying it or contacting OpenAI."
    requires_system_checks = []
    requires_migrations_checks = False

    def handle(self, *args, **options):
        key = getattr(settings, "TASKER_AI_OPENAI_API_KEY", "")
        missing = key is None or (isinstance(key, str) and not key.strip())
        invalid = not missing and (
            not isinstance(key, str) or any(character.isspace() for character in key)
        )
        status = "missing" if missing else "invalid configuration" if invalid else "configured"
        self.stdout.write(f"OpenAI API key: {status}.")
        for label, name in (
            ("Organization override", "TASKER_AI_OPENAI_ORGANIZATION"),
            ("Project override", "TASKER_AI_OPENAI_PROJECT"),
        ):
            value = getattr(settings, name, "")
            present = isinstance(value, str) and bool(value.strip())
            self.stdout.write(f"{label}: {'configured' if present else 'not configured'}.")
        self.stdout.write("Offline check only: credentials have not been authenticated.")
        self.stdout.write("AI activation is unchanged; no database or provider request was made.")
        if missing:
            raise CommandError("Configure OPENAI_API_KEY privately in the application's environment.")
        if invalid:
            raise CommandError("OPENAI_API_KEY must be text without whitespace; replace it privately.")
