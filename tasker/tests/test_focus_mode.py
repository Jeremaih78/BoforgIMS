from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from tasker.models import Task, TaskChecklistItem, TaskTimeEntry
from tasker.services.focus import ActiveTimerConflict, start_focus
from tasker.services.tasks import soft_delete_task, update_progress


class FocusModeTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="focus-user", password="test-pass")
        permissions = Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("view_task", "change_task", "change_taskchecklistitem"),
        )
        self.user.user_permissions.set(permissions)
        self.task = Task.objects.create(title="Prepare weekly figures", owner=self.user, created_by=self.user)
        self.client.force_login(self.user)

    def url(self, name, **kwargs):
        return reverse(f"ims:tasker:{name}", kwargs={"slug": self.task.slug, **kwargs})

    def test_start_pause_resume_and_stop_accumulate_time(self):
        response = self.client.post(self.url("focus_start"))
        self.assertRedirects(response, self.url("focus_mode"))
        entry = TaskTimeEntry.objects.get(task=self.task, ended_at__isnull=True)
        self.assertEqual(entry.entry_type, TaskTimeEntry.EntryType.FOCUS)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

        entry.started_at = timezone.now() - timedelta(minutes=5)
        entry.save(update_fields=["started_at"])
        self.client.post(self.url("focus_pause"))
        entry.refresh_from_db()
        self.task.refresh_from_db()
        self.assertIsNotNone(entry.ended_at)
        self.assertGreaterEqual(self.task.actual_duration, timedelta(minutes=4, seconds=59))

        self.client.post(self.url("focus_resume"))
        self.assertEqual(TaskTimeEntry.objects.filter(task=self.task, ended_at__isnull=True).count(), 1)
        self.client.post(self.url("focus_stop"))
        self.assertFalse(TaskTimeEntry.objects.filter(task=self.task, ended_at__isnull=True).exists())

    def test_complete_closes_timer_and_updates_task(self):
        self.client.post(self.url("focus_start"))
        response = self.client.post(self.url("focus_complete"))
        self.assertRedirects(response, reverse("ims:tasker:index"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.COMPLETED)
        self.assertEqual(self.task.progress, 100)
        self.assertIsNotNone(self.task.completed_at)
        self.assertFalse(TaskTimeEntry.objects.filter(task=self.task, ended_at__isnull=True).exists())

    def test_block_requires_reason_then_records_and_exits(self):
        self.client.post(self.url("focus_start"))
        response = self.client.post(self.url("focus_block"), {"reason": ""})
        self.assertRedirects(response, self.url("focus_mode"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.IN_PROGRESS)

        response = self.client.post(self.url("focus_block"), {"reason": "Waiting for signed approval"})
        self.assertRedirects(response, reverse("ims:tasker:index"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, Task.Status.BLOCKED)
        self.assertEqual(self.task.blocked_reason, "Waiting for signed approval")

    def test_notes_autosave_and_checklist_progress(self):
        response = self.client.post(self.url("focus_notes"), {"notes": "Decision captured"})
        self.assertJSONEqual(response.content, {"saved": True})
        item = TaskChecklistItem.objects.create(task=self.task, title="First step", created_by=self.user)
        response = self.client.post(self.url("focus_checklist_toggle", item_pk=item.pk))
        self.assertRedirects(response, self.url("focus_mode"))
        self.task.refresh_from_db()
        self.assertEqual(self.task.notes, "Decision captured")
        self.assertEqual(self.task.progress, 100)

    def test_user_cannot_access_another_users_focus_mode(self):
        stranger = get_user_model().objects.create_user(username="stranger")
        other = Task.objects.create(title="Private task", owner=stranger)
        response = self.client.get(reverse("ims:tasker:focus_mode", kwargs={"slug": other.slug}))
        self.assertEqual(response.status_code, 404)

    def test_only_one_running_timer_per_user(self):
        other = Task.objects.create(title="Second task", owner=self.user)
        start_focus(task=self.task, user=self.user)
        with self.assertRaises(ActiveTimerConflict):
            start_focus(task=other, user=self.user)
        with self.assertRaises(IntegrityError), transaction.atomic():
            TaskTimeEntry.objects.create(task=other, user=self.user, started_at=timezone.now())

    def test_focus_page_is_standalone_and_exposes_controls(self):
        self.client.post(self.url("focus_start"))
        response = self.client.get(self.url("focus_mode"))
        self.assertContains(response, "BOFORG FOCUS")
        self.assertContains(response, "Elapsed time")
        self.assertContains(response, "Complete")
        self.assertNotContains(response, 'class="sidebar')

    def test_non_focus_completion_closes_an_active_timer(self):
        start_focus(task=self.task, user=self.user)
        update_progress(task=self.task, user=self.user, progress=100)
        self.assertFalse(TaskTimeEntry.objects.filter(task=self.task, ended_at__isnull=True).exists())
        self.task.refresh_from_db()
        self.assertIsNotNone(self.task.actual_duration)

    def test_deleting_task_closes_active_timer(self):
        start_focus(task=self.task, user=self.user)
        soft_delete_task(task=self.task, user=self.user)
        self.assertFalse(TaskTimeEntry.objects.filter(user=self.user, ended_at__isnull=True).exists())
        self.assertTrue(Task.all_objects.get(pk=self.task.pk).is_deleted)
