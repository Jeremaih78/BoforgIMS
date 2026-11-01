"""Read-only, offline governance checks; never authenticate or enable AI."""

from copy import copy
from decimal import Decimal
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.db.models import Q
from django.utils import timezone

from tasker.ai.budgets import AIBudgetUnavailableError, week_bounds
from tasker.ai.capabilities import preflight_configuration
from tasker.ai_budget_models import AIBudgetEnrollment, AIBudgetPolicy, AICompanyBudget, AIPriceCard, AISpendReservation
from tasker.models import AIConfiguration


class Command(BaseCommand):
    help = "Inspect Tasker AI configuration and spending readiness offline, without enabling AI or displaying credentials."
    requires_system_checks = []
    requires_migrations_checks = False

    def handle(self, *args, **options):
        blockers = []
        def check(label, ready, code):
            self.stdout.write(f"{label}: {'ready' if ready else 'blocked'}.")
            if not ready:
                blockers.append(code)

        self.stdout.write("Tasker AI readiness: offline, read-only configuration checks.")
        check("Workflow switch", getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False) is True, "workflow_disabled")
        key = getattr(settings, "TASKER_AI_OPENAI_API_KEY", "")
        key_ready = isinstance(key, str) and bool(key.strip()) and not any(character.isspace() for character in key)
        check("Credential presence", key_ready, "credentials_missing_or_invalid")
        try:
            self._database_checks(check)
        except DatabaseError as exc:
            self.stdout.write("Database governance records: unavailable.")
            self._footer()
            raise CommandError("Apply the reviewed Tasker migrations before checking readiness; no configuration was changed.") from exc
        self.stdout.write(f"Blocker count: {len(blockers)}.")
        for code in blockers:
            self.stdout.write(f"- {code}")
        self._footer()
        if blockers:
            raise CommandError("Offline readiness is blocked; review the listed configuration states.")
        self.stdout.write(self.style.SUCCESS("Offline configuration checks passed. This does not authenticate credentials or guarantee request headroom."))

    def _database_checks(self, check):
        policy = AIBudgetPolicy.objects.filter(key=1).first()
        check("Global spending policy", policy is not None and policy.is_enabled, "spending_policy_disabled_or_missing")
        calendar_ready = False
        if policy is not None:
            candidate = copy(policy)
            candidate.is_enabled = True
            try:
                week_bounds(candidate)
                calendar_ready = True
            except AIBudgetUnavailableError:
                pass
        check("Explicit USD weekly calendar and employee ceiling", calendar_ready, "weekly_policy_unconfigured")
        if calendar_ready:
            self.stdout.write(f"Employee weekly ceiling: USD {policy.employee_weekly_limit:f} (maximum USD 0.50).")
            days = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
            self.stdout.write(f"Weekly reset: {days[policy.week_start_day]} {policy.reset_hour:02d}:{policy.reset_minute:02d} {policy.timezone_name}.")

        company_rows = AICompanyBudget.objects.all()
        ready_companies = company_rows.filter(is_enabled=True, charge_requester=True,
            company__is_active=True, weekly_limit__gt=0, weekly_limit__lte=Decimal("5"))
        enabled_count = company_rows.filter(is_enabled=True).count()
        ready_count = ready_companies.count()
        self.stdout.write(f"Company budget records: {company_rows.count()}; enabled: {enabled_count}; ready: {ready_count} (maximum USD 5 per week).")
        check("Enabled company ceilings and requester attribution", ready_count > 0 and enabled_count == ready_count, "company_budget_unconfigured")

        enrollments = AIBudgetEnrollment.objects.filter(is_active=True)
        permitted = Q(user__is_superuser=True) | Q(user__user_permissions__content_type__app_label="tasker", user__user_permissions__codename="use_ai") | Q(user__groups__permissions__content_type__app_label="tasker", user__groups__permissions__codename="use_ai")
        eligible = enrollments.filter(user__is_active=True, company__is_active=True,
            company_id__in=ready_companies.values("company_id")).filter(permitted).distinct()
        enrolled_count, eligible_count = enrollments.count(), eligible.count()
        self.stdout.write(f"Active enrollments: {enrolled_count}; eligible: {eligible_count}; blocked: {enrolled_count-eligible_count}.")
        check("Explicit pilot enrollment and AI permissions", eligible_count > 0 and enrolled_count == eligible_count, "pilot_enrollment_unconfigured")
        reviews = AISpendReservation.objects.filter(requires_review=True).filter(
            Q(company_id__in=ready_companies.values("company_id")) | Q(user_id__in=enrollments.values("user_id"))).count()
        self.stdout.write(f"Charges requiring review in the configured pilot: {reviews}.")
        check("Pilot charge evidence", reviews == 0, "charge_review_required")

        active = AIConfiguration.objects.filter(is_active=True)
        active_count = active.count()
        self.stdout.write(f"Active AI configurations: {active_count}.")
        configuration = active.first() if active_count == 1 else None
        check("Single enabled OpenAI configuration", bool(configuration and configuration.is_enabled and configuration.provider == "OPENAI"), "ai_configuration_disabled_or_ambiguous")
        safe_model = False
        if configuration:
            model = str(configuration.model)
            # A mistaken credential pasted into a model field is never echoed.
            secrets = (getattr(settings, name, "") for name in (
                "TASKER_AI_OPENAI_API_KEY", "TASKER_AI_OPENAI_ORGANIZATION", "TASKER_AI_OPENAI_PROJECT"))
            contains_secret = any(isinstance(value, str) and value.strip() and value.strip() in model for value in secrets)
            safe_model = (bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}", model))
                          and not model.lower().startswith(("sk-", "bearer")) and not contains_secret)
            self.stdout.write(f"Active model: {model if safe_model else 'invalid identifier (hidden)'}.")
        check("Model identifier", safe_model, "model_identifier_invalid_or_missing")
        now = timezone.now()
        card = AIPriceCard.objects.filter(provider=configuration.provider, model=configuration.model,
            service_tier="default", is_active=True, is_verified=True,
            verified_at__lte=now, valid_until__gt=now).first() if configuration else None
        check("Current matching verified price card", card is not None, "price_card_unconfigured_or_expired")
        profile_ready = False
        if configuration and card:
            report = preflight_configuration(configuration, profile=card.capability_profile, pricing_verified=True)
            profile_ready = report["ok"] and configuration.max_output_tokens <= card.max_output_tokens and not configuration.store_provider_responses
        check("Reviewed model capabilities and output price bounds", profile_ready, "capability_or_output_bounds_unconfigured")

    def _footer(self):
        self.stdout.write("No database writes, provider authentication, email or other external requests were made.")
        self.stdout.write("AI activation and credentials are unchanged; credential values are never displayed.")
