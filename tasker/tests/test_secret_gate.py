"""Synthetic-only tests: never inspect the real index, .env or excluded README."""

import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import Mock, patch

from tools.check_staged_secrets import inspect_staged, main, report_findings, scan_text


class StagedSecretGateTests(unittest.TestCase):
    def test_api_key_and_named_token_are_detected(self):
        key = "sk-proj-" + "Ab7Q" * 12
        self.assertTrue(scan_text("client = Client(api_key='" + key + "')"))
        self.assertTrue(scan_text("SERVICE_ACCESS_TOKEN=" + "aB37" * 10))

    def test_known_placeholders_and_environment_references_are_allowed(self):
        for value in ("your_api_key_here", "<replace-me>", "${OPENAI_API_KEY}", "REDACTED", "sk-proj-" + "x" * 40):
            with self.subTest(value=value):
                self.assertFalse(scan_text("OPENAI_API_KEY=" + value))
        self.assertFalse(scan_text('api_key = os.environ.get("OPENAI_API_KEY")'))

    def test_private_key_marker_and_provider_tokens_are_detected(self):
        private_marker = "-----BEGIN " + "PRIVATE KEY-----"
        self.assertTrue(scan_text(private_marker + "\n" + "A" * 64))
        self.assertTrue(scan_text("ghp_" + "a9B" * 12))

    def test_excluded_paths_block_without_reading_their_contents(self):
        paths = [".env", "config/.env.production", "tasker/README_AI_Extensions.md"]
        reader = Mock(side_effect=AssertionError("Forbidden content read"))
        findings = inspect_staged(paths, reader)
        self.assertEqual(len(findings), 3)
        reader.assert_not_called()

    def test_env_example_is_scanned_and_only_placeholders_are_allowed(self):
        reader = Mock(return_value=b"OPENAI_API_KEY=your_api_key_here\n")
        self.assertEqual(inspect_staged([".env.example"], reader), [])
        reader.assert_called_once_with(".env.example")
        key = "sk-" + "Az7Q" * 10
        self.assertTrue(inspect_staged([".env.example"], lambda _path: key.encode()))

    def test_output_never_contains_secret_values_or_reader_exception_text(self):
        secret = "sk-proj-" + "Qz9A" * 12
        findings = inspect_staged(["settings.py"], lambda _path: secret.encode())
        out = io.StringIO()
        report_findings(findings, out)
        self.assertIn("settings.py", out.getvalue())
        self.assertNotIn(secret, out.getvalue())

        def failed_read(_path):
            raise RuntimeError(secret)

        out = io.StringIO()
        report_findings(inspect_staged(["missing.py"], failed_read), out)
        self.assertNotIn(secret, out.getvalue())

    def test_binary_is_skipped_but_utf16_text_is_checked(self):
        self.assertEqual(inspect_staged(["image.bin"], lambda _path: b"\x00\x01\x02"), [])
        key = "sk-" + "Ta8Z" * 10
        self.assertTrue(inspect_staged(["config.txt"], lambda _path: key.encode("utf-16")))

    def test_cli_blocks_sensitive_rename_source_before_any_blob_read(self):
        names = b"tasker/README_AI_Extensions.md\0renamed.txt\0"
        with patch("tools.check_staged_secrets._git", return_value=names) as git:
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main([]), 1)
        self.assertEqual(git.call_count, 1)
        self.assertIn("--cached", git.call_args.args)
        self.assertIn("--no-renames", git.call_args.args)

    def test_secret_in_filename_is_redacted_too(self):
        secret = "sk-" + "Tx7A" * 10
        output = io.StringIO()
        report_findings(inspect_staged([secret + ".txt"], lambda _path: secret.encode()), output)
        self.assertNotIn(secret, output.getvalue())

    def test_python_runtime_values_are_not_literal_credentials(self):
        expressions = (
            ("SECRET_KEY", "get_random_secret_key()"),
            ("api_key", "self.environment.api_key"),
            ("lease_token", "models.UUIDField(null=True, blank=True, editable=False)"),
            ("lease_token", "job.lease_token"),
            ("context_token", "_current_lease.set(lease)"),
            ("TOKEN", "re.compile(rb'[A-Za-z_][A-Za-z0-9_]*')"),
        )
        for name, expression in expressions:
            with self.subTest(name=name):
                source = f"{name} = {expression}\n"
                self.assertEqual(inspect_staged(["module.py"], lambda _path: source.encode()), [])
        source = "client = OpenAI(" + "api_key" + "=" + "self.environment.api_key)\n"
        self.assertFalse(scan_text(source, source_path="module.py"))

    def test_python_literal_credentials_remain_blocked_in_all_bindings(self):
        candidate = "opaque-" + "sample" * 6
        cases = (
            f"TOKEN = {candidate!r}",
            f"PASSWORD: str = {candidate!r}",
            f"self.client_secret = {candidate!r}",
            f"client = OpenAI(api_key={candidate!r})",
            f"settings = {{'access_token': {candidate!r}}}",
            f"AUTH_TOKEN = {candidate[:10]!r} + {candidate[10:]!r}",
        )
        for source in cases:
            with self.subTest(binding=source.split("=", 1)[0]):
                self.assertIn("credential assignment", scan_text(source, source_path="module.py"))

    def test_python_embedded_config_and_plain_unquoted_values_remain_checked(self):
        candidate = "opaque-" + "sample" * 6
        configuration = "PASSWORD=" + candidate
        source = "payload = " + repr(configuration)
        self.assertIn("credential assignment", scan_text(source, source_path="module.py"))
        self.assertIn("credential assignment", scan_text(configuration, source_path="settings.ini"))
        self.assertIn("credential assignment", scan_text(configuration))

    def test_python_string_boundaries_and_escaped_placeholder_are_respected(self):
        prefix = "prefix\\n" + 'OPENAI_API_KEY="'
        source = "original = " + repr(prefix.encode()) + " + self.secret + " + repr(b'"\\r\\n')
        self.assertFalse(scan_text(source, source_path="module.py"))
        placeholder = "OPENAI_API_KEY=" + "your_api_key_here\n"
        source = "reader = Mock(return_value=" + repr(placeholder.encode()) + ")"
        self.assertFalse(scan_text(source, source_path="module.py"))
        source = "with patch.object(helper, 'TOKEN') as token:\n    token.subn.return_value = (self.secret, 1)\n"
        self.assertFalse(scan_text(source, source_path="module.py"))

    def test_invalid_python_fails_back_to_plain_assignment_detection(self):
        candidate = "opaque-" + "sample" * 6
        source = "PASSWORD=" + candidate + "\ninvalid Python !\n"
        self.assertIn("credential assignment", scan_text(source, source_path="module.py"))

    def test_python_comments_with_literal_credentials_remain_blocked(self):
        candidate = "opaque-" + "sample" * 6
        source = "# PASSWORD=" + candidate + "\nvalue = 1\n"
        self.assertIn("credential assignment", scan_text(source, source_path="module.py"))

    def test_python_syntax_handling_does_not_exempt_provider_tokens(self):
        candidate = "sk-proj-" + "Abc1" * 12
        source = "TOKEN = re.compile(" + repr(candidate) + ")"
        self.assertIn("API-key/token pattern", scan_text(source, source_path="module.py"))


if __name__ == "__main__":
    unittest.main()
