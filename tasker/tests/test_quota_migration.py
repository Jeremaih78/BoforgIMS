"""Historical quota backfill keeps unknown retry usage conservative."""

from datetime import timedelta

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class AIQuotaHistoryMigrationTests(TransactionTestCase):
    migrate_from = ("tasker", "0010_alter_taskattachment_file")
    migrate_to = ("tasker", "0011_aiusage_quota_reservations")

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.restore_targets = executor.loader.graph.leaf_nodes()
        executor.migrate([self.migrate_from])
        old_apps = executor.loader.project_state([self.migrate_from]).apps
        self.old_usage = old_apps.get_model("tasker", "AIUsageLog")
        configuration = old_apps.get_model("tasker", "AIConfiguration")
        self.configuration = configuration.objects.create(
            name="Migration history fixture", provider="OPENAI", is_active=False, daily_token_limit=1000,
        )

    def tearDown(self):
        # Restore the current schema even when an assertion fails. This uses
        # only the disposable test DB selected by Django's test runner.
        MigrationExecutor(connection).migrate(self.restore_targets)
        super().tearDown()

    def test_retry_history_keeps_unknown_prior_attempts_charged_and_preserves_measured_usage(self):
        now = timezone.now()
        cases = (
            ("single_measured", 1, 12, 8, "SUCCEEDED", "", False, 20, False),
            ("retry_measured", 3, 12, 8, "SUCCEEDED", "", False, 1000, True),
            ("retry_above_cap", 2, 1000, 500, "SUCCEEDED", "", False, 1500, True),
            ("retry_deleted", 2, 12, 8, "SUCCEEDED", "", True, 1000, True),
            ("retry_failed", 2, 12, 8, "FAILED", "AIResponseValidationError", False, 1000, True),
            ("unknown_usage", 1, 0, 0, "FAILED", "AIProviderError", False, 1000, True),
            ("local_rejection", 3, 0, 0, "FAILED", "AIPermissionError", False, 0, False),
            ("disabled", 1, 0, 0, "DISABLED", "", False, 0, False),
        )
        records = []
        for name, attempts, input_tokens, output_tokens, status, error_type, deleted, expected, estimated in cases:
            row = self.old_usage.objects.create(
                configuration_id=self.configuration.pk, operation=name, attempts=attempts,
                input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=input_tokens + output_tokens,
                status=status, error_type=error_type, is_deleted=deleted,
                started_at=now - timedelta(minutes=5), completed_at=now,
            )
            records.append((row.pk, name, attempts, input_tokens + output_tokens, expected, estimated))
        executor = MigrationExecutor(connection)
        executor.migrate([self.migrate_to])
        usage = executor.loader.project_state([self.migrate_to]).apps.get_model("tasker", "AIUsageLog")
        for pk, name, attempts, measured, expected, estimated in records:
            with self.subTest(history=name):
                row = usage._base_manager.get(pk=pk)
                self.assertEqual(row.total_tokens, measured)
                self.assertEqual(row.attempts, attempts)
                self.assertEqual(row.quota_charged_tokens, expected)
                self.assertEqual(row.quota_usage_estimated, estimated)
                self.assertEqual(row.quota_reserved_tokens, 0)
                if expected:
                    self.assertEqual(row.quota_reserved_at, row.started_at)
                    self.assertIsNotNone(row.quota_settled_at)
                else:
                    self.assertIsNone(row.quota_reserved_at)
