"""Synthetic Git replies only; never read the actual credential document/history."""

import codecs
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from tools import audit_tasker_key_history as audit


class KeyHistoryAuditTests(unittest.TestCase):
    def test_counts_matching_blobs_without_returning_content_or_duplicate_blobs(self):
        secret = "sk-proj-" + "Za9Q7b" * 8
        blob = ("Historical draft\n" + secret).encode("utf-16")
        revision_a, revision_b, object_id = b"a" * 40, b"b" * 40, b"c" * 40

        def git(_repo, *args):
            if args[0] == "rev-list":
                return revision_a + b"\n" + revision_b + b"\n"
            if args[0] == "ls-tree":
                return b"100644 blob " + object_id + b"\ttasker/README_AI_Extensions.md\0"
            if args[:2] == ("cat-file", "-s"):
                return str(len(blob)).encode()
            if args[:2] == ("cat-file", "blob"):
                return blob
            self.fail("Unexpected Git action")

        with patch.object(audit, "_git", side_effect=git) as git_call:
            self.assertEqual(audit.audit_history("synthetic-repo"), (2, 1, 1))
        self.assertEqual(sum(call.args[1:3] == ("cat-file", "blob") for call in git_call.call_args_list), 1)

    def test_unknown_encoding_is_refused_and_bom_text_is_decoded(self):
        self.assertEqual(audit._text(codecs.BOM_UTF8 + b"draft"), "draft")
        self.assertEqual(audit._text("draft".encode("utf-32")), "draft")
        with self.assertRaises(audit.AuditRefused):
            audit._text(b"binary\0input")
        with self.assertRaises(UnicodeDecodeError):
            audit._text(b"\xff")

    def test_cli_never_echoes_reader_exceptions_or_key_material(self):
        secret = "sk-" + "Zq7A" * 10
        output = io.StringIO()
        with patch.object(audit, "audit_history", side_effect=RuntimeError(secret)), redirect_stdout(output):
            self.assertEqual(audit.main(), 2)
        self.assertNotIn(secret, output.getvalue())
        self.assertIn("assessment incomplete", output.getvalue())

    def test_cli_reports_only_counts_and_scope(self):
        output = io.StringIO()
        with patch.object(audit, "audit_history", return_value=(3, 2, 1)), redirect_stdout(output):
            self.assertEqual(audit.main(), 0)
        self.assertIn("Historical revisions: 3; unique document blobs: 2; key-pattern blobs: 1", output.getvalue())
        self.assertIn("no history was changed", output.getvalue())
