from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db.models.deletion import ProtectedError
from django.test import TestCase, override_settings
from django.urls import reverse

from tasker.models import DailyPlan, Task, TaskCategory


class TaskerModelTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="tasker-user", password="test-pass")

    def test_task_slug_is_generated_and_unique(self):
        first = Task.objects.create(title="Prepare proposal", owner=self.user, created_by=self.user)
        second = Task.objects.create(title="Prepare proposal", owner=self.user, created_by=self.user)

        self.assertEqual(first.slug, "prepare-proposal")
        self.assertEqual(second.slug, "prepare-proposal-2")

    def test_soft_delete_hides_task_and_restore_recovers_it(self):
        task = Task.objects.create(title="Follow up", owner=self.user)
        task.delete(user=self.user)

        self.assertFalse(Task.objects.filter(pk=task.pk).exists())
        deleted_task = Task.all_objects.get(pk=task.pk)
        self.assertEqual(deleted_task.deleted_by, self.user)

        deleted_task.restore(user=self.user)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_daily_plan_is_unique_per_owner_and_date(self):
        plan = DailyPlan.objects.create(owner=self.user)
        self.assertEqual(plan.plan_date, plan.created_at.date())

    def test_daily_plan_history_protects_owner_from_deletion(self):
        DailyPlan.objects.create(owner=self.user)
        with self.assertRaises(ProtectedError):
            self.user.delete()

    def test_category_is_soft_deleted(self):
        category = TaskCategory.objects.create(name="Operations")
        TaskCategory.objects.filter(pk=category.pk).delete(user=self.user)
        self.assertFalse(TaskCategory.objects.filter(pk=category.pk).exists())

    def test_duration_fields_accept_positive_values(self):
        task = Task.objects.create(
            title="Stock count", owner=self.user, estimated_duration=timedelta(hours=2)
        )
        self.assertEqual(task.estimated_duration, timedelta(hours=2))


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class TaskerViewTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="viewer", password="test-pass")

    def test_landing_page_requires_authentication(self):
        response = self.client.get(reverse("ims:tasker:index"))
        self.assertEqual(response.status_code, 302)

    def test_landing_page_requires_task_permission(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse("ims:tasker:index"))
        self.assertEqual(response.status_code, 403)

    def test_permitted_user_can_open_landing_page(self):
        permission = Permission.objects.get(codename="view_task", content_type__app_label="tasker")
        self.user.user_permissions.add(permission)
        self.client.force_login(self.user)

        response = self.client.get(reverse("ims:tasker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Boforg AI Tasker")
