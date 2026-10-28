"""Organization migration leaves legacy audiences and achievements unchanged."""
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class OrganizationLegacyMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.restore_targets = executor.loader.graph.leaf_nodes()
        executor.migrate([("tasker", "0011_aiusage_quota_reservations")])
        apps = executor.loader.project_state([("tasker", "0011_aiusage_quota_reservations")]).apps
        user_model = apps.get_model("auth", "User")
        self.employee = user_model.objects.create(username="migration-legacy-worker")
        task_model = apps.get_model("tasker", "Task")
        self.task = task_model.objects.create(title="Legacy owner's task", slug="legacy-owner-task", owner_id=self.employee.pk, notes="Synthetic owner notes", status="COMPLETED", progress=100)
        plan_model = apps.get_model("tasker", "DailyPlan")
        self.plan = plan_model.objects.create(owner_id=self.employee.pk, quick_notes="Synthetic private capture")

    def tearDown(self):
        MigrationExecutor(connection).migrate(self.restore_targets)
        super().tearDown()

    def test_no_invented_mapping_or_accepted_output_and_private_notes_preserved(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.restore_targets)
        apps = executor.loader.project_state(self.restore_targets).apps
        task = apps.get_model("tasker", "Task").objects.get(pk=self.task.pk)
        self.assertIsNone(task.company_id)
        self.assertIsNone(task.team_id)
        self.assertEqual(task.owner_id, self.employee.pk)
        self.assertEqual(task.notes, "Synthetic owner notes")
        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(apps.get_model("tasker", "DailyPlan").objects.get(pk=self.plan.pk).quick_notes, "Synthetic private capture")
        for name in ("Team", "TeamMembership", "OrganizationAccess", "Goal", "OutcomeMeasurement", "BaselineObservation"):
            self.assertEqual(apps.get_model("tasker", name).objects.count(), 0, name)
