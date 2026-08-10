from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasker.ai.context import build_employee_day_context
from tasker.ai.gateway import AIUnavailableError, TaskerAIGateway
from tasker.models import DailyPlan, Task, TaskActivity, TaskTimeEntry
from tasker.services.dashboard import build_today_dashboard


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class TaskerArchitectureTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="architecture-user", first_name="Architecture"
        )
        self.other = get_user_model().objects.create_user(username="outside-user")

    def test_today_dashboard_uses_five_bounded_queries(self):
        Task.objects.create(title="Today", owner=self.user, due_date=timezone.localdate())
        Task.objects.create(
            title="Carry",
            owner=self.user,
            due_date=timezone.localdate() - timedelta(days=1),
            carry_forward=True,
        )

        with self.assertNumQueries(5):
            dashboard = build_today_dashboard(user=self.user)

        self.assertEqual(dashboard.total_count, 1)
        self.assertEqual(len(dashboard.carry_over_tasks), 1)

    def test_ai_context_is_read_only_owner_scoped_and_minimal(self):
        task = Task.objects.create(
            title="Owner task",
            description="x" * 5000,
            notes="Private note must stay out",
            owner=self.user,
            due_date=timezone.localdate(),
        )
        Task.objects.create(
            title="Other employee task",
            owner=self.other,
            due_date=timezone.localdate(),
        )
        plan = DailyPlan.objects.create(owner=self.user, focus_area="Customer delivery")
        plan.top_priorities.add(task)

        with self.assertNumQueries(3):
            context = build_employee_day_context(user=self.user)

        self.assertEqual(context.employee_id, self.user.pk)
        self.assertEqual(context.focus_area, "Customer delivery")
        self.assertEqual([snapshot.title for snapshot in context.tasks], ["Owner task"])
        self.assertEqual(context.top_priority_ids, (task.pk,))
        self.assertEqual(len(context.tasks[0].description), 4000)
        self.assertFalse(hasattr(context.tasks[0], "notes"))

    def test_ai_gateway_is_disabled_by_default(self):
        gateway = TaskerAIGateway()

        self.assertFalse(gateway.is_available)
        with self.assertRaisesMessage(AIUnavailableError, "No productivity AI provider"):
            gateway.plan_day(None)


@override_settings(STORAGES=TEST_STORAGES)
class TaskerIntegrationBoundaryTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="boundary-user", password="pass")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="tasker",
                codename__in=("view_task", "add_task"),
            )
        )
        self.client.force_login(self.user)

    def test_tasker_assets_are_scoped_by_resolver_not_path_assumption(self):
        tasker_response = self.client.get(reverse("ims:tasker:index"))
        ims_response = self.client.get(reverse("ims:dashboard"))

        self.assertContains(tasker_response, "tasker/css/ui.css")
        self.assertContains(tasker_response, f'data-create-url="{reverse("ims:tasker:task_create")}"')
        self.assertNotContains(ims_response, "tasker/css/ui.css")
        self.assertNotContains(ims_response, "tasker/js/ui.js")

    def test_task_detail_bounds_time_and_history_collections(self):
        task = Task.objects.create(
            title="Bounded detail",
            owner=self.user,
            created_by=self.user,
            updated_by=self.user,
        )
        for number in range(12):
            started_at = timezone.now()
            duration = timedelta(minutes=number + 1)
            TaskTimeEntry.objects.create(
                task=task,
                user=self.user,
                started_at=started_at,
                ended_at=started_at + duration,
                duration=duration,
            )
        for number in range(35):
            TaskActivity.objects.create(
                task=task,
                event_type=TaskActivity.EventType.UPDATED,
                description=f"Event {number}",
            )

        response = self.client.get(task.get_absolute_url())

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["time_entries"]), 8)
        self.assertEqual(len(response.context["activities"]), 30)
