from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.contrib.staticfiles import finders
from django.test import TestCase, override_settings
from django.urls import reverse

from tasker.models import Task


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES)
class TaskerUIPolishTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ux-user", password="pass")
        self.user.user_permissions.add(
            *Permission.objects.filter(
                content_type__app_label="tasker",
                codename__in=("view_task", "add_task", "change_task"),
            )
        )
        self.client.force_login(self.user)

    def test_shared_ui_assets_fab_and_shortcuts_render_once(self):
        response = self.client.get(reverse("ims:tasker:index"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "tasker/css/ui.css")
        self.assertContains(response, "tasker/js/ui.js")
        self.assertContains(response, 'id="quickAddModal"', count=1)
        self.assertContains(response, 'class="tasker-fab"')
        self.assertContains(response, "Keyboard shortcuts")

    def test_floating_quick_add_is_hidden_without_add_permission(self):
        viewer = get_user_model().objects.create_user(username="ux-viewer", password="pass")
        viewer.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_task")
        )
        self.client.force_login(viewer)

        response = self.client.get(reverse("ims:tasker:index"))

        self.assertNotContains(response, 'class="tasker-fab"')
        self.assertNotContains(response, 'id="quickAddModal"')

    def test_global_quick_add_returns_to_safe_origin_page(self):
        task_list_url = reverse("ims:tasker:task_list")

        response = self.client.post(
            reverse("ims:tasker:index"),
            {
                "action": "quick_add",
                "title": "Captured from task list",
                "priority": Task.Priority.MEDIUM,
                "next": task_list_url,
            },
        )

        self.assertRedirects(response, task_list_url)
        self.assertTrue(Task.objects.filter(title="Captured from task list", owner=self.user).exists())

    def test_global_quick_add_rejects_external_redirect(self):
        response = self.client.post(
            reverse("ims:tasker:index"),
            {
                "action": "quick_add",
                "title": "Safe redirect task",
                "priority": Task.Priority.HIGH,
                "next": "https://attacker.example/redirect",
            },
        )

        self.assertRedirects(response, reverse("ims:tasker:index"))

    def test_task_list_exposes_search_shortcut_and_card_action(self):
        task = Task.objects.create(
            title="Keyboard-ready task",
            owner=self.user,
            created_by=self.user,
            updated_by=self.user,
        )

        response = self.client.get(reverse("ims:tasker:task_list"))

        self.assertContains(response, "data-task-search")
        self.assertContains(response, f'aria-label="Edit {task.title}"')

    def test_shared_static_assets_are_discoverable(self):
        self.assertIsNotNone(finders.find("tasker/css/ui.css"))
        self.assertIsNotNone(finders.find("tasker/js/ui.js"))
