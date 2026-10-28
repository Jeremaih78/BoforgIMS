"""Synthetic-only credential checks: no real keys, database or provider calls."""

import io
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings


class CredentialDiagnosticTests(SimpleTestCase):
    def run_check(self):
        output = io.StringIO()
        error = io.StringIO()
        call_command("check_tasker_ai_credentials", stdout=output, stderr=error)
        return output.getvalue(), error.getvalue()

    def test_configured_values_are_never_disclosed_and_no_provider_is_created(self):
        key = "synthetic-credential-for-presence-check"
        organization = "synthetic-private-organization"
        project = "synthetic-private-project"
        with override_settings(
            TASKER_AI_OPENAI_API_KEY=key,
            TASKER_AI_OPENAI_ORGANIZATION=organization,
            TASKER_AI_OPENAI_PROJECT=project,
        ), patch("tasker.ai.providers.openai.OpenAI") as client, patch(
            "tasker.ai.providers.openai.OpenAIProvider"
        ) as provider:
            output, error = self.run_check()
        for value in (key, organization, project):
            self.assertNotIn(value, output + error)
        self.assertIn("OpenAI API key: configured.", output)
        self.assertIn("Organization override: configured.", output)
        self.assertIn("Project override: configured.", output)
        self.assertIn("credentials have not been authenticated", output)
        self.assertIn("AI activation is unchanged", output)
        self.assertEqual(error, "")
        client.assert_not_called()
        provider.assert_not_called()

    def test_missing_and_whitespace_only_keys_fail_without_database_access(self):
        for key in (None, "", " \t\r\n"):
            with self.subTest(key_is_none=key is None), override_settings(
                TASKER_AI_OPENAI_API_KEY=key,
                TASKER_AI_OPENAI_ORGANIZATION="",
                TASKER_AI_OPENAI_PROJECT="",
            ):
                output = io.StringIO()
                with self.assertRaisesMessage(CommandError, "Configure OPENAI_API_KEY privately"):
                    call_command("check_tasker_ai_credentials", stdout=output)
                self.assertIn("OpenAI API key: missing.", output.getvalue())
                self.assertIn("Project override: not configured.", output.getvalue())

    def test_malformed_pasted_key_is_not_echoed_in_output_or_error(self):
        key = "synthetic-private-value"
        for malformed in (" " + key, key + "\n", key + " " + key, 123):
            with self.subTest(is_text=isinstance(malformed, str)), override_settings(
                TASKER_AI_OPENAI_API_KEY=malformed
            ):
                output = io.StringIO()
                error = io.StringIO()
                with self.assertRaises(CommandError) as caught:
                    call_command("check_tasker_ai_credentials", stdout=output, stderr=error)
                combined = output.getvalue() + error.getvalue() + str(caught.exception)
                self.assertNotIn(key, combined)
                self.assertNotIn(str(malformed), combined)
                self.assertIn("OpenAI API key: invalid configuration.", combined)
