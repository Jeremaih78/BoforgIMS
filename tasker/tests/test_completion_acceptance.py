"""Explicit outcome acceptance across manual task forms and progress updates."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from tasker.forms import TaskForm
from tasker.models import Task, TaskActivity, TaskChecklistItem
from tasker.services.focus import start_focus
from tasker.services.tasks import save_task, toggle_checklist_item, update_progress
from tasker.services.workbench import WorkConflict, work_version


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}


@override_settings(STORAGES=TEST_STORAGES)
class CompletionAcceptanceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("outcome-reviewer")
        cls.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))

    def setUp(self):
        self.client.force_login(self.user)

    def task(self, **changes):
        data = {"owner": self.user, "title": "Defined work", "definition_of_done": "Print quality checked and delivery recorded"}
        data.update(changes)
        return Task.objects.create(**data)

    def payload(self, task, **changes):
        data = {"owner": self.user.pk, "title": task.title, "description": task.description,
                "priority": task.priority, "status": task.status, "progress": str(task.progress),
                "notes": task.notes, "definition_of_done": task.definition_of_done,
                "work_version": work_version(task)}
        data.update(changes)
        return data

    def form(self, task, **changes):
        form = TaskForm(self.payload(task, **changes), instance=task, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        return form

    def test_task_form_renders_criteria_and_current_revision(self):
        task = self.task()
        response = self.client.get(reverse("ims:tasker:task_edit", args=[task.slug]))
        self.assertContains(response, "Definition of done")
        self.assertContains(response, 'name="accept_definition"')
        self.assertContains(response, work_version(task))
        self.assertEqual(response.context["form"].fields["definition_of_done"].max_length, 5000)

    def test_defined_task_requires_explicit_acceptance_in_manual_edit(self):
        task = self.task()
        form = self.form(task, status="COMPLETED")
        with self.assertRaises(ValidationError):
            save_task(form=form, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, "PENDING")
        saved = save_task(form=self.form(task, status="COMPLETED", accept_definition="on"), user=self.user)
        self.assertEqual(saved.status, "COMPLETED")
        self.assertEqual(saved.progress, 100)

    def test_stale_form_cannot_accept_or_replace_changed_criteria(self):
        task = self.task()
        form = self.form(task, status="COMPLETED", accept_definition="on")
        fresh = Task.objects.get(pk=task.pk)
        fresh.definition_of_done = "Use the revised quality review before delivery"
        fresh.save(update_fields=["definition_of_done", "updated_at"])
        with self.assertRaises(ValidationError):
            save_task(form=form, user=self.user)
        fresh.refresh_from_db()
        self.assertEqual(fresh.status, "PENDING")
        self.assertEqual(fresh.definition_of_done, "Use the revised quality review before delivery")

    def test_missing_revision_fails_closed_for_defined_legacy_edit(self):
        task = self.task()
        form = self.form(task, work_version="", title="Attempted update")
        with self.assertRaises(ValidationError):
            save_task(form=form, user=self.user)
        self.assertEqual(Task.objects.get(pk=task.pk).title, "Defined work")

    def test_omitted_criteria_are_preserved_for_clients_with_current_revision(self):
        task = self.task()
        data = self.payload(task, title="Updated title")
        del data["definition_of_done"]
        form = TaskForm(data, instance=task, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        saved = save_task(form=form, user=self.user)
        self.assertEqual(saved.definition_of_done, "Print quality checked and delivery recorded")

    def test_clearing_criteria_does_not_bypass_completion_review(self):
        task = self.task()
        form = self.form(task, definition_of_done="", status="COMPLETED")
        with self.assertRaises(ValidationError):
            save_task(form=form, user=self.user)
        self.assertEqual(Task.objects.get(pk=task.pk).status, "PENDING")

    def test_blank_legacy_task_can_still_complete_without_new_fields(self):
        task = self.task(definition_of_done="")
        data = self.payload(task, status="COMPLETED")
        del data["work_version"]
        del data["definition_of_done"]
        form = TaskForm(data, instance=task, user=self.user)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(save_task(form=form, user=self.user).status, "COMPLETED")

    def test_progress_acceptance_requires_current_revision_and_checkbox(self):
        task = self.task()
        version = work_version(task)
        with self.assertRaises(WorkConflict):
            update_progress(task=task, user=self.user, progress=100, accept_definition=True)
        with self.assertRaises(ValueError):
            update_progress(task=task, user=self.user, progress=100, expected_version=version)
        self.assertEqual(Task.objects.get(pk=task.pk).status, "PENDING")
        update_progress(task=task, user=self.user, progress=100, expected_version=version, accept_definition=True)
        self.assertEqual(Task.objects.get(pk=task.pk).status, "COMPLETED")

    def test_progress_stale_review_keeps_new_criteria_and_running_timer(self):
        task = self.task()
        entry = start_focus(task=task, user=self.user)
        task.refresh_from_db()
        version = work_version(task)
        task.definition_of_done = "New delivery acceptance criteria"
        task.save(update_fields=["definition_of_done", "updated_at"])
        with self.assertRaises(WorkConflict):
            update_progress(task=task, user=self.user, progress=100, expected_version=version, accept_definition=True)
        task.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(task.status, "IN_PROGRESS")
        self.assertIsNone(entry.ended_at)
        self.assertEqual(task.definition_of_done, "New delivery acceptance criteria")

    def test_progress_http_form_carries_review_and_closes_timer_on_acceptance(self):
        task = self.task()
        entry = start_focus(task=task, user=self.user)
        task.refresh_from_db()
        response = self.client.get(task.get_absolute_url())
        self.assertContains(response, task.definition_of_done)
        self.assertContains(response, 'name="work_version"')
        self.assertContains(response, 'name="accept_definition"')
        url = reverse("ims:tasker:progress_update", args=[task.slug])
        self.client.post(url, {"progress": "100", "work_version": work_version(task)})
        self.assertEqual(Task.objects.get(pk=task.pk).status, "IN_PROGRESS")
        self.client.post(url, {"progress": "100", "work_version": work_version(task), "accept_definition": "on"})
        task.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(task.status, "COMPLETED")
        self.assertIsNotNone(entry.ended_at)

    def test_completed_checklist_is_progress_without_outcome_acceptance(self):
        task = self.task()
        item = TaskChecklistItem.objects.create(task=task, title="Quality review")
        toggle_checklist_item(item=item, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.status, "PENDING")
        self.assertIsNone(task.completed_at)

    def test_criteria_changes_are_audited(self):
        task = self.task()
        save_task(form=self.form(task, definition_of_done="Customer accepted the checked delivery"), user=self.user)
        activity = TaskActivity.objects.filter(task=task, changes__has_key="definition_of_done").latest("created_at")
        self.assertEqual(activity.changes["definition_of_done"]["to"], "Customer accepted the checked delivery")
