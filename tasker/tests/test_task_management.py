from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from tasker.models import Task, TaskActivity, TaskCategory, TaskChecklistItem, TaskComment, TaskTimeEntry


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES)
class TaskManagementTests(TestCase):
    def setUp(self):
        user_model = get_user_model()
        self.user = user_model.objects.create_user(username="employee", password="pass")
        self.other = user_model.objects.create_user(username="other-employee", password="pass")
        permission_names = (
            "view_task",
            "add_task",
            "change_task",
            "delete_task",
            "add_taskcomment",
            "add_taskchecklistitem",
            "change_taskchecklistitem",
            "add_tasktimeentry",
        )
        self.user.user_permissions.add(
            *Permission.objects.filter(content_type__app_label="tasker", codename__in=permission_names)
        )
        self.other.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_task")
        )
        self.client.force_login(self.user)

    def make_task(self, title="Own task", **kwargs):
        return Task.objects.create(
            title=title,
            owner=self.user,
            created_by=self.user,
            updated_by=self.user,
            **kwargs,
        )

    def valid_task_payload(self, **overrides):
        payload = {
            "title": "Prepare weekly stock report",
            "description": "Check inventory movement and exceptions.",
            "priority": Task.Priority.HIGH,
            "status": Task.Status.PENDING,
            "progress": "0",
            "estimated_minutes": "60",
            "actual_minutes": "0",
            "due_date": timezone.localdate().isoformat(),
            "due_time": "",
            "category": "",
            "parent_task": "",
            "colour": "",
            "notes": "",
        }
        payload.update(overrides)
        return payload

    def test_task_list_is_owner_scoped_and_searchable(self):
        matching = self.make_task("Prepare customer proposal", priority=Task.Priority.CRITICAL)
        self.make_task("Count warehouse stock", priority=Task.Priority.LOW)
        Task.objects.create(title="Other private proposal", owner=self.other)

        response = self.client.get(
            reverse("ims:tasker:task_list"),
            {"q": "proposal", "priority": Task.Priority.CRITICAL},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, matching.title)
        self.assertNotContains(response, "Count warehouse stock")
        self.assertNotContains(response, "Other private proposal")

    def test_view_all_permission_exposes_team_tasks(self):
        team_task = Task.objects.create(title="Team-visible task", owner=self.other)
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_all_tasks")
        )
        self.client.logout()
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))

        response = self.client.get(reverse("ims:tasker:task_list"))

        self.assertContains(response, team_task.title)

    def test_assigner_can_open_and_manage_task_they_delegated(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="assign_task")
        )
        self.client.logout()
        self.client.force_login(get_user_model().objects.get(pk=self.user.pk))
        payload = self.valid_task_payload(owner=str(self.other.pk), title="Delegated follow-up")

        response = self.client.post(reverse("ims:tasker:task_create"), payload)

        task = Task.objects.get(title="Delegated follow-up")
        self.assertEqual(task.owner, self.other)
        self.assertEqual(task.assigned_by, self.user)
        self.assertRedirects(response, task.get_absolute_url())
        self.assertEqual(self.client.get(task.get_absolute_url()).status_code, 200)

    def test_create_task_sets_ownership_audit_and_history(self):
        response = self.client.post(reverse("ims:tasker:task_create"), self.valid_task_payload())

        task = Task.objects.get(title="Prepare weekly stock report")
        self.assertRedirects(response, task.get_absolute_url())
        self.assertEqual(task.owner, self.user)
        self.assertEqual(task.assigned_by, self.user)
        self.assertEqual(task.created_by, self.user)
        self.assertEqual(task.estimated_duration, timedelta(hours=1))
        self.assertTrue(
            task.activities.filter(event_type=TaskActivity.EventType.CREATED).exists()
        )

    def test_edit_task_records_status_history(self):
        task = self.make_task()
        response = self.client.post(
            reverse("ims:tasker:task_edit", args=[task.slug]),
            self.valid_task_payload(
                title=task.title,
                status=Task.Status.IN_PROGRESS,
                progress="25",
            ),
        )

        self.assertRedirects(response, task.get_absolute_url())
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)
        self.assertEqual(task.progress, 25)
        self.assertIsNotNone(task.started_at)
        self.assertTrue(task.activities.filter(event_type=TaskActivity.EventType.STATUS).exists())

    def test_other_owner_cannot_open_task_detail(self):
        task = self.make_task()
        self.client.force_login(self.other)

        response = self.client.get(task.get_absolute_url())

        self.assertEqual(response.status_code, 404)

    def test_delete_is_recoverable_and_records_actor(self):
        task = self.make_task()

        response = self.client.post(reverse("ims:tasker:task_delete", args=[task.slug]))

        self.assertRedirects(response, reverse("ims:tasker:task_list"))
        self.assertFalse(Task.objects.filter(pk=task.pk).exists())
        deleted = Task.all_objects.get(pk=task.pk)
        self.assertEqual(deleted.deleted_by, self.user)
        self.assertTrue(
            TaskActivity.objects.filter(task=deleted, event_type=TaskActivity.EventType.DELETED).exists()
        )

    def test_checklist_toggle_updates_progress_and_history(self):
        task = self.make_task()
        add_url = reverse("ims:tasker:checklist_add", args=[task.slug])
        self.client.post(add_url, {"title": "Confirm physical stock"})
        self.client.post(add_url, {"title": "Export report"})
        first = TaskChecklistItem.objects.filter(task=task).order_by("position").first()

        response = self.client.post(
            reverse("ims:tasker:checklist_toggle", args=[task.slug, first.pk])
        )

        self.assertEqual(response.status_code, 302)
        task.refresh_from_db()
        first.refresh_from_db()
        self.assertTrue(first.is_completed)
        self.assertEqual(task.progress, 50)
        self.assertTrue(task.activities.filter(event_type=TaskActivity.EventType.CHECKLIST).exists())

    def test_comment_is_audited_and_appears_in_history(self):
        task = self.make_task()

        self.client.post(
            reverse("ims:tasker:comment_add", args=[task.slug]),
            {"body": "Waiting for the supplier response."},
        )

        comment = TaskComment.objects.get(task=task)
        self.assertEqual(comment.created_by, self.user)
        self.assertTrue(task.activities.filter(event_type=TaskActivity.EventType.COMMENT).exists())

    def test_time_entries_accumulate_actual_duration(self):
        task = self.make_task()
        url = reverse("ims:tasker:time_add", args=[task.slug])
        started = timezone.localtime().strftime("%Y-%m-%dT%H:%M")

        self.client.post(url, {"started_at": started, "duration_minutes": "25", "notes": "Research"})
        self.client.post(url, {"started_at": started, "duration_minutes": "35", "notes": "Drafting"})

        task.refresh_from_db()
        self.assertEqual(TaskTimeEntry.objects.filter(task=task).count(), 2)
        self.assertEqual(task.actual_duration, timedelta(hours=1))
        self.assertEqual(
            task.activities.filter(event_type=TaskActivity.EventType.TIME).count(), 2
        )

    def test_progress_completion_synchronizes_status(self):
        task = self.make_task()

        self.client.post(
            reverse("ims:tasker:progress_update", args=[task.slug]), {"progress": "100"}
        )

        task.refresh_from_db()
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertIsNotNone(task.completed_at)

    def test_archive_is_saved_hidden_by_default_and_available_by_filter(self):
        task = self.make_task("Archive me")
        self.client.post(
            reverse("ims:tasker:task_edit", args=[task.slug]),
            self.valid_task_payload(title=task.title, archived="on"),
        )
        task.refresh_from_db()
        self.assertTrue(task.archived)
        self.assertNotContains(self.client.get(reverse("ims:tasker:task_list")), task.title)
        self.assertContains(
            self.client.get(reverse("ims:tasker:task_list"), {"archived": "1"}), task.title
        )

    def test_category_status_filters_and_sorting_are_applied(self):
        category = TaskCategory.objects.create(name="Operations")
        later = self.make_task(
            "Zulu task", category=category, status=Task.Status.BLOCKED, blocked_reason="Supplier"
        )
        self.make_task("Alpha unrelated", status=Task.Status.PENDING)
        response = self.client.get(
            reverse("ims:tasker:task_list"),
            {"category": category.pk, "status": Task.Status.BLOCKED, "sort": "title"},
        )
        self.assertEqual(list(response.context["page_obj"].object_list), [later])

    def test_parent_and_category_are_saved_and_cycles_are_rejected(self):
        category = TaskCategory.objects.create(name="Sales")
        parent = self.make_task("Parent")
        response = self.client.post(
            reverse("ims:tasker:task_create"),
            self.valid_task_payload(title="Child", parent_task=str(parent.pk), category=str(category.pk)),
        )
        child = Task.objects.get(title="Child")
        self.assertRedirects(response, child.get_absolute_url())
        self.assertEqual(child.parent_task, parent)
        self.assertEqual(child.category, category)

        response = self.client.post(
            reverse("ims:tasker:task_edit", args=[parent.slug]),
            self.valid_task_payload(title=parent.title, parent_task=str(child.pk)),
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "hierarchy cannot contain a cycle")
        parent.refresh_from_db()
        self.assertIsNone(parent.parent_task)

    def test_due_time_and_blocked_status_require_supporting_data(self):
        due_response = self.client.post(
            reverse("ims:tasker:task_create"),
            self.valid_task_payload(title="Bad due time", due_date="", due_time="09:00"),
        )
        blocked_response = self.client.post(
            reverse("ims:tasker:task_create"),
            self.valid_task_payload(title="Missing blocker", status=Task.Status.BLOCKED),
        )
        self.assertEqual(due_response.status_code, 200)
        self.assertContains(due_response, "Choose a due date")
        self.assertEqual(blocked_response.status_code, 200)
        self.assertContains(blocked_response, "Record why this task is blocked")
        self.assertFalse(Task.objects.filter(title__in=("Bad due time", "Missing blocker")).exists())
