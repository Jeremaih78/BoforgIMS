from datetime import date, timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasker.models import DailyPlan, Task
from tasker.services.dashboard import build_today_dashboard


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


class TodayDashboardServiceTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="owner")
        self.other_user = user_model.objects.create_user(username="other")
        self.today = date(2026, 8, 4)

    def test_dashboard_is_owner_scoped_and_calculates_progress(self):
        completed = Task.objects.create(
            title="Completed today",
            owner=self.user,
            due_date=self.today,
            status=Task.Status.COMPLETED,
            progress=100,
        )
        pending = Task.objects.create(
            title="Pending today",
            owner=self.user,
            due_date=self.today,
            status=Task.Status.PENDING,
        )
        Task.objects.create(
            title="Someone else's task",
            owner=self.other_user,
            due_date=self.today,
            status=Task.Status.PENDING,
        )

        result = build_today_dashboard(user=self.user, today=self.today)

        self.assertEqual(result.total_count, 2)
        self.assertEqual(result.completed_count, 1)
        self.assertEqual(result.pending_count, 1)
        self.assertEqual(result.progress_percent, 50)
        self.assertEqual({task.pk for task in result.timeline}, {completed.pk, pending.pk})

    def test_top_priorities_and_carry_over_are_ranked(self):
        medium = Task.objects.create(
            title="Medium today",
            owner=self.user,
            priority=Task.Priority.MEDIUM,
            due_date=self.today,
        )
        carried = Task.objects.create(
            title="Carried high priority",
            owner=self.user,
            priority=Task.Priority.HIGH,
            due_date=self.today - timedelta(days=1),
            carry_forward=True,
        )

        result = build_today_dashboard(user=self.user, today=self.today)

        self.assertEqual(result.top_priorities[0], carried)
        self.assertIn(medium, result.top_priorities)
        self.assertEqual(result.carry_over_tasks, (carried,))

    def test_quick_notes_are_read_without_creating_a_plan(self):
        result = build_today_dashboard(user=self.user, today=self.today)
        self.assertEqual(result.quick_notes, "")
        self.assertFalse(DailyPlan.objects.exists())


@override_settings(STORAGES=TEST_STORAGES)
class TodayDashboardViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="dashboard-user", password="pass")
        permissions = Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("view_task", "add_task", "add_dailyplan", "change_dailyplan"),
        )
        self.user.user_permissions.add(*permissions)
        self.client.force_login(self.user)
        self.url = reverse("ims:tasker:index")

    def test_dashboard_displays_personal_ai_actions(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="use_ai")
        )
        response = self.client.get(self.url)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Today’s timeline")
        self.assertContains(response, "Plan My Day")
        self.assertContains(response, "Suggest Next Task")
        self.assertContains(response, "Prioritize Tasks")
        self.assertContains(response, "Summarize Day")
        self.assertNotContains(response, "Coming in Phase 2")

    def test_quick_add_creates_an_audited_task_for_today(self):
        response = self.client.post(
            self.url,
            {
                "action": "quick_add",
                "title": "Call priority customer",
                "priority": Task.Priority.HIGH,
                "due_time": "10:30",
                "estimated_minutes": "45",
                "category": "",
            },
        )

        self.assertRedirects(response, self.url)
        task = Task.objects.get(title="Call priority customer")
        self.assertEqual(task.owner, self.user)
        self.assertEqual(task.assigned_by, self.user)
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.updated_by, self.user)
        self.assertEqual(task.due_date, timezone.localdate())
        self.assertEqual(task.estimated_duration, timedelta(minutes=45))

    def test_quick_notes_create_then_update_todays_plan(self):
        self.client.post(self.url, {"action": "quick_notes", "quick_notes": "Confirm stock levels"})
        response = self.client.post(
            self.url, {"action": "quick_notes", "quick_notes": "Confirm stock and supplier ETA"}
        )

        self.assertRedirects(response, self.url)
        plan = DailyPlan.objects.get(owner=self.user, plan_date=timezone.localdate())
        self.assertEqual(plan.quick_notes, "Confirm stock and supplier ETA")
        self.assertEqual(plan.created_by, self.user)
        self.assertEqual(plan.updated_by, self.user)
        self.assertEqual(DailyPlan.objects.filter(owner=self.user).count(), 1)

    def test_quick_add_rejects_user_without_add_permission(self):
        limited_user = get_user_model().objects.create_user(username="limited-user", password="pass")
        limited_user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_task")
        )
        self.client.force_login(limited_user)

        response = self.client.post(
            self.url,
            {"action": "quick_add", "title": "Not allowed", "priority": Task.Priority.MEDIUM},
        )

        self.assertEqual(response.status_code, 403)
        self.assertFalse(Task.objects.filter(title="Not allowed").exists())
