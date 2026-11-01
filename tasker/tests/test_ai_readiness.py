"""The operational check reads governance data and never contacts providers."""

from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from tasker.ai_budget_models import AIBudgetPolicy
from tasker.tests.test_ai_budgets import MoneyFixture


@override_settings(TASKER_AI_WORKFLOW_ENABLED=True,
                   TASKER_AI_OPENAI_API_KEY="offline-fixture-credential-never-print-this")
class AIReadinessCommandTests(MoneyFixture, TestCase):
    def run_check(self, *, blocked=False):
        output = StringIO()
        if blocked:
            with self.assertRaises(CommandError):
                call_command("check_tasker_ai_readiness", stdout=output)
        else:
            call_command("check_tasker_ai_readiness", stdout=output)
        return output.getvalue()

    def test_offline_configuration_pass_reports_safe_states_and_counts(self):
        output = self.run_check()
        self.assertIn("Blocker count: 0.", output)
        self.assertIn("Active enrollments: 1; eligible: 1; blocked: 0.", output)
        self.assertIn("Monday 00:00 Africa/Harare", output)
        self.assertIn("fixture-text-model-20261028", output)
        self.assertIn("AI activation and credentials are unchanged", output)
        self.assertNotIn("offline-fixture-credential-never-print-this", output)

    def test_missing_global_policy_is_blocked_with_an_exact_failure_count(self):
        AIBudgetPolicy.objects.all().delete()
        output = self.run_check(blocked=True)
        self.assertIn("Blocker count: 2.", output)
        self.assertIn("spending_policy_disabled_or_missing", output)
        self.assertIn("weekly_policy_unconfigured", output)

    def test_missing_credentials_and_expired_price_are_distinct_blockers(self):
        with override_settings(TASKER_AI_OPENAI_API_KEY=""):
            with patch("tasker.management.commands.check_tasker_ai_readiness.timezone.now",
                       return_value=timezone.now()+timedelta(days=31)):
                output = self.run_check(blocked=True)
        self.assertIn("Blocker count: 3.", output)
        self.assertIn("credentials_missing_or_invalid", output)
        self.assertIn("price_card_unconfigured_or_expired", output)
        self.assertIn("capability_or_output_bounds_unconfigured", output)

    def test_check_does_not_write_database_or_construct_an_external_client(self):
        before = (self.policy.is_enabled, self.configuration.is_enabled, self.card.is_active)
        with patch("tasker.ai.providers.openai.OpenAI", side_effect=AssertionError("Provider construction forbidden")) as provider:
            with patch("smtplib.SMTP", side_effect=AssertionError("Email forbidden")) as smtp:
                with patch("httpx.Client.send", side_effect=AssertionError("HTTP forbidden")) as http:
                    with CaptureQueriesContext(connection) as queries:
                        self.run_check()
        provider.assert_not_called()
        smtp.assert_not_called()
        http.assert_not_called()
        self.assertTrue(queries)
        for query in queries:
            sql = query["sql"].lstrip().upper()
            self.assertTrue(sql.startswith("SELECT"), sql[:50])
            self.assertNotIn("FOR UPDATE", sql)
        self.policy.refresh_from_db()
        self.configuration.refresh_from_db()
        self.card.refresh_from_db()
        self.assertEqual(before, (self.policy.is_enabled, self.configuration.is_enabled, self.card.is_active))

    def test_unready_active_enrollment_is_counted_without_employee_details(self):
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])
        output = self.run_check(blocked=True)
        self.assertIn("Active enrollments: 1; eligible: 0; blocked: 1.", output)
        self.assertIn("pilot_enrollment_unconfigured", output)
        self.assertNotIn(self.user.username, output)
        self.assertNotIn(self.company.name, output)

    def test_credential_looking_model_identifier_is_never_echoed(self):
        mistaken_value = "sk-offline-fixture-not-a-real-key"
        self.configuration.model = mistaken_value
        self.configuration.save()
        output = self.run_check(blocked=True)
        self.assertNotIn(mistaken_value, output)
        self.assertIn("invalid identifier (hidden)", output)

    def test_configured_credential_in_a_model_field_is_never_echoed_even_without_key_prefix(self):
        mistaken_value = "offline-fixture-credential-never-print-this"
        self.configuration.model = mistaken_value
        self.configuration.save()
        output = self.run_check(blocked=True)
        self.assertNotIn(mistaken_value, output)
        self.assertIn("model_identifier_invalid_or_missing", output)

    def test_charge_review_is_visible_without_exposing_provider_or_source_content(self):
        with override_settings(TASKER_AI_BUDGET_ENFORCED=True):
            _, reservation = self.admission()
        reservation.requires_review = True
        reservation.save(update_fields=["requires_review"])
        output = self.run_check(blocked=True)
        self.assertIn("Charges requiring review in the configured pilot: 1.", output)
        self.assertIn("Blocker count: 1.", output)
        self.assertIn("charge_review_required", output)
        self.assertNotIn(reservation.prompt_fingerprint, output)
