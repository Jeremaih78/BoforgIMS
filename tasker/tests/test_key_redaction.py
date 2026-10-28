"""Synthetic temporary files only; never inspect the checkout's excluded files."""

import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools import redact_tasker_api_key as helper


class KeyRedactionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "tasker").mkdir()
        self.target = self.root / helper.TARGET
        self.secret = b"sk-proj-" + b"Ab7Q9z" * 8

    def run_cli(self, *args):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = helper.main(["--repo", str(self.root), *args])
        self.assertNotIn(self.secret.decode(), output.getvalue())
        return code, output.getvalue()

    def test_dry_run_is_default_and_does_not_modify_bytes(self):
        original = b"Owner draft\nOPENAI_API_KEY=" + self.secret + b"\nEnd\n"
        self.target.write_bytes(original)
        code, output = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), original)
        self.assertEqual(output, "DRY RUN: 1 matching OpenAI key token(s).\n")

    def test_atomic_write_preserves_bom_crlf_unicode_and_other_edits(self):
        original = b"\xef\xbb\xbf" + "Owner’s draft\r\n".encode() + self.secret + b"\r\nNext edit\r\n"
        self.target.write_bytes(original)
        code, output = self.run_cli("--write")
        self.assertEqual(code, 0)
        self.assertEqual(self.target.read_bytes(), original.replace(self.secret, helper.REPLACEMENT))
        self.assertEqual(output, "REDACTED: 1 matching OpenAI key token(s).\n")
        self.assertEqual(list(self.target.parent.glob(".tasker-key-cleanup-*")), [])

    def test_supported_token_types_only_are_replaced(self):
        tokens = [b"sk-" + b"Az37" * 9, b"sk-proj-" + b"Bz47" * 9, b"sk-svcacct-" + b"Cz57" * 9]
        original = b"\n".join(tokens) + b"\nOPENAI_API_KEY=<secret managed by host>\nShort: sk-short\n"
        cleaned, count = helper.redact_bytes(original)
        expected = original
        for token in tokens:
            expected = expected.replace(token, helper.REPLACEMENT)
        self.assertEqual((cleaned, count), (expected, 3))

    def test_no_match_does_not_replace_file(self):
        self.target.write_bytes(b"Preserve this documentation.\n")
        with patch.object(helper.os, "replace") as replace:
            self.assertEqual(self.run_cli("--write")[0], 0)
        replace.assert_not_called()

    def test_outside_root_or_different_target_is_refused_without_reading(self):
        for target in (Path("../outside.md"), Path("other.md")):
            with self.subTest(target=target), patch.object(helper, "TARGET", target), patch.object(helper, "_read") as read:
                self.assertEqual(self.run_cli("--write")[0], 2)
                read.assert_not_called()

    def test_symlink_is_refused_without_reading(self):
        with patch.object(helper, "_linked", side_effect=lambda path: path == self.target), patch.object(helper, "_read") as read:
            self.assertEqual(self.run_cli("--write")[0], 2)
            read.assert_not_called()

    def test_parent_symlink_and_junction_are_refused_without_reading(self):
        with patch.object(helper, "_linked", side_effect=lambda path: path == self.target.parent), patch.object(helper, "_read") as read:
            self.assertEqual(self.run_cli("--write")[0], 2)
            read.assert_not_called()

    def test_unknown_encoding_and_null_bytes_are_refused_unchanged(self):
        for original in (b"\xff" + self.secret, "Unicode".encode("utf-16"), b"\0" + self.secret):
            with self.subTest(original=original[:2]):
                self.target.write_bytes(original)
                self.assertEqual(self.run_cli("--write")[0], 2)
                self.assertEqual(self.target.read_bytes(), original)

    def test_oversized_file_is_refused(self):
        self.target.write_bytes(self.secret)
        with patch.object(helper, "MAX_BYTES", 10):
            self.assertEqual(self.run_cli("--write")[0], 2)
        self.assertEqual(self.target.read_bytes(), self.secret)

    def test_concurrent_edit_is_not_overwritten_and_temp_is_removed(self):
        self.target.write_bytes(self.secret)
        original_read = helper._read
        reads = 0

        def changed(path):
            nonlocal reads
            reads += 1
            if reads == 2:
                path.write_bytes(b"New owner edit\n")
            return original_read(path)

        with patch.object(helper, "_read", side_effect=changed):
            self.assertEqual(self.run_cli("--write")[0], 2)
        self.assertEqual(self.target.read_bytes(), b"New owner edit\n")
        self.assertEqual(list(self.target.parent.glob(".tasker-key-cleanup-*")), [])

    def test_exception_and_invalid_argument_output_never_echo_input(self):
        with patch.object(helper, "cleanup", side_effect=RuntimeError(self.secret.decode())):
            self.assertEqual(self.run_cli("--write")[0], 2)
        self.assertEqual(self.run_cli("--unknown=" + self.secret.decode())[0], 2)

    def test_residual_detection_refuses(self):
        with patch.object(helper, "TOKEN") as token:
            token.subn.return_value = (self.secret, 1)
            token.search.return_value = True
            with self.assertRaises(helper.CleanupRefused):
                helper.redact_bytes(self.secret)


if __name__ == "__main__":
    unittest.main()
