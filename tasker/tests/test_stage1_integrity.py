"""Stage 1 dashboard, lifecycle, privacy and provider trust regressions."""

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from tasker.ai.exceptions import AIProviderError
from tasker.ai.providers.openai import OpenAIProvider
from tasker.forms import TaskForm
from tasker.models import AIConversation, AIMessage, DailyPlan, DailyReview, Task, TaskActivity, TaskChecklistItem
from tasker.services.dashboard import build_today_dashboard
from tasker.services.focus import block_task, close_task_timers, complete_focus, start_focus
from tasker.services.tasks import save_task, sync_checklist_progress, toggle_checklist_item, update_progress


class Stage1IntegrityTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="integrity-user", is_staff=True)
        self.other = get_user_model().objects.create_user(username="other-integrity-user")
        self.user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))

    def test_blocked_work_is_not_an_executable_top_priority(self):
        Task.objects.create(title="Waiting for approval", owner=self.user,
                            status=Task.Status.BLOCKED, blocked_reason="Approval",
                            due_date=timezone.localdate(), priority=Task.Priority.CRITICAL)
        self.assertEqual(build_today_dashboard(user=self.user).top_priorities, ())

    def test_undated_critical_work_is_included_in_top_priorities(self):
        task = Task.objects.create(title="Urgent outage", owner=self.user, priority=Task.Priority.CRITICAL)
        self.assertIn(task, build_today_dashboard(user=self.user).top_priorities)

    def test_completed_task_keeps_accepted_status_and_progress_after_checklist_edit(self):
        task = Task.objects.create(title="Accepted delivery", owner=self.user,
                                   status=Task.Status.COMPLETED, progress=100, completed_at=timezone.now())
        item = TaskChecklistItem.objects.create(task=task, title="Evidence", is_completed=True)
        toggle_checklist_item(item=item, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertEqual(task.progress, 100)
        self.assertIsNotNone(task.completed_at)

    def test_checklist_completion_is_not_accepted_task_completion(self):
        task = Task.objects.create(title="Needs acceptance", owner=self.user)
        item = TaskChecklistItem.objects.create(task=task, title="Prepare evidence")
        toggle_checklist_item(item=item, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertIsNone(task.completed_at)

    def test_stale_checklist_instances_toggle_latest_state(self):
        task = Task.objects.create(title="Shared steps", owner=self.user)
        item = TaskChecklistItem.objects.create(task=task, title="A step")
        stale = TaskChecklistItem.objects.get(pk=item.pk)
        toggle_checklist_item(item=item, user=self.user)
        toggle_checklist_item(item=stale, user=self.user)
        item.refresh_from_db()
        self.assertFalse(item.is_completed)

    def test_deleted_steps_do_not_inflate_checklist_progress(self):
        task = Task.objects.create(title="Active steps", owner=self.user)
        TaskChecklistItem.objects.create(task=task, title="Remaining")
        removed = TaskChecklistItem.objects.create(task=task, title="Removed", is_completed=True)
        removed.delete(user=self.user)
        self.assertEqual(sync_checklist_progress(task=task, user=self.user), 0)

    def test_blocking_through_edit_closes_the_running_timer(self):
        task = Task.objects.create(title="Report", owner=self.user)
        start_focus(task=task, user=self.user)
        task.refresh_from_db()
        form = TaskForm({"title": task.title, "owner": self.user.pk,
                         "priority": task.priority, "status": Task.Status.BLOCKED,
                         "blocked_reason": "Waiting for data", "progress": task.progress,
                         "notes": ""}, user=self.user, instance=task)
        self.assertTrue(form.is_valid(), form.errors)
        save_task(form=form, user=self.user)
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_closing_task_timers_preserves_the_audit_actor(self):
        task = Task.objects.create(title="Timed work", owner=self.user)
        entry = start_focus(task=task, user=self.user)
        entry.started_at = timezone.now() - timedelta(minutes=2)
        entry.save(update_fields=["started_at"])
        close_task_timers(task=task, actor=self.user)
        task.refresh_from_db()
        self.assertEqual(task.updated_by_id, self.user.pk)

    def test_blocking_running_focus_records_the_status_audit(self):
        task = Task.objects.create(title="Blocked timed work", owner=self.user)
        start_focus(task=task, user=self.user)
        block_task(task=task, user=self.user, reason="Waiting for approval")
        event = TaskActivity.objects.filter(task=task, event_type=TaskActivity.EventType.STATUS).latest("pk")
        self.assertEqual(event.changes["status"], {"from": Task.Status.IN_PROGRESS, "to": Task.Status.BLOCKED})
        self.assertEqual(event.created_by_id, self.user.pk)
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_completing_running_focus_records_the_status_audit(self):
        task = Task.objects.create(title="Accepted timed work", owner=self.user)
        start_focus(task=task, user=self.user)
        complete_focus(task=task, user=self.user)
        event = TaskActivity.objects.filter(task=task, event_type=TaskActivity.EventType.STATUS).latest("pk")
        self.assertEqual(event.changes["status"], {"from": Task.Status.IN_PROGRESS, "to": Task.Status.COMPLETED})
        self.assertEqual(event.created_by_id, self.user.pk)
        self.assertFalse(task.time_entries.filter(ended_at__isnull=True).exists())

    def test_reopening_clears_completion_and_stale_blocker(self):
        task = Task.objects.create(title="Reopened", owner=self.user, status=Task.Status.COMPLETED,
                                   completed_at=timezone.now(), progress=100, blocked_reason="Old blocker")
        update_progress(task=task, user=self.user, progress=50)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.IN_PROGRESS)
        self.assertIsNone(task.completed_at)
        self.assertEqual(task.blocked_reason, "")

    def test_staff_ai_content_admin_is_owner_scoped_and_immutable(self):
        own = AIConversation.objects.create(owner=self.user)
        foreign = AIConversation.objects.create(owner=self.other)
        own_message = AIMessage.objects.create(conversation=own, sequence=1, role="user", content="My draft")
        foreign_message = AIMessage.objects.create(conversation=foreign, sequence=1, role="user", content="Other draft")
        request = RequestFactory().get("/admin/")
        request.user = self.user
        conversations = admin.site._registry[AIConversation]
        messages = admin.site._registry[AIMessage]
        self.assertEqual(list(conversations.get_queryset(request)), [own])
        self.assertEqual(list(messages.get_queryset(request)), [own_message])
        self.assertFalse(conversations.has_change_permission(request, own))
        self.assertFalse(messages.has_change_permission(request, own_message))
        self.assertFalse(messages.get_queryset(request).filter(pk=foreign_message.pk).exists())

    def test_private_daily_notes_and_reflections_are_not_global_staff_admin_content(self):
        own = DailyPlan.objects.create(owner=self.user, quick_notes="Own private note")
        foreign = DailyPlan.objects.create(owner=self.other, quick_notes="Other private note")
        own_review = DailyReview.objects.create(plan=own, lessons_learned="Personal reflection")
        DailyReview.objects.create(plan=foreign, lessons_learned="Other reflection")
        request = RequestFactory().get("/admin/")
        request.user = self.user
        self.assertEqual(list(admin.site._registry[DailyPlan].get_queryset(request)), [own])
        self.assertEqual(list(admin.site._registry[DailyReview].get_queryset(request)), [own_review])

    def test_shared_task_notes_have_truthful_label(self):
        task = Task.objects.create(title="Shared task", owner=self.user, notes="Work context")
        self.client.force_login(self.user)
        response = self.client.get(reverse("ims:tasker:task_detail", args=[task.slug]))
        self.assertContains(response, "Shared task notes")
        self.assertNotContains(response, "Private context")

    def test_terminal_task_cannot_be_blocked_by_stale_focus_action(self):
        task = Task.objects.create(title="Already accepted", owner=self.user,
                                   status=Task.Status.COMPLETED, completed_at=timezone.now(), progress=100)
        with self.assertRaises(ValueError):
            block_task(task=task, user=self.user, reason="Stale browser action")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.COMPLETED)
        self.assertIsNotNone(task.completed_at)

    def test_cancelled_task_cannot_be_completed_by_stale_focus_action(self):
        task = Task.objects.create(title="Cancelled work", owner=self.user, status=Task.Status.CANCELLED)
        with self.assertRaises(ValueError):
            complete_focus(task=task, user=self.user)
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def test_single_and_bulk_admin_delete_close_running_timers(self):
        request = RequestFactory().post("/admin/")
        request.user = self.user
        model_admin = admin.site._registry[Task]
        for bulk in (False, True):
            with self.subTest(bulk=bulk):
                task = Task.objects.create(title=f"Admin deletion {bulk}", owner=self.user)
                entry = start_focus(task=task, user=self.user)
                if bulk:
                    model_admin.delete_queryset(request, Task.objects.filter(pk=task.pk))
                else:
                    model_admin.delete_model(request, task)
                entry.refresh_from_db()
                self.assertIsNotNone(entry.ended_at)
                self.assertTrue(Task.all_objects.get(pk=task.pk).is_deleted)

    def test_progress_cannot_complete_cancelled_or_archived_work(self):
        for changes in ({"status": Task.Status.CANCELLED}, {"archived": True}):
            with self.subTest(changes=changes):
                task = Task.objects.create(title="Ineligible progress", owner=self.user, **changes)
                with self.assertRaises(ValueError):
                    update_progress(task=task, user=self.user, progress=100)
                task.refresh_from_db()
                self.assertEqual(task.progress, 0)
                self.assertIsNone(task.completed_at)

    def test_stale_progress_post_returns_a_useful_error(self):
        task = Task.objects.create(title="Cancelled before progress save", owner=self.user,
                                   status=Task.Status.CANCELLED)
        self.client.force_login(self.user)
        response = self.client.post(reverse("ims:tasker:progress_update", args=[task.slug]),
                                    {"progress": "100"}, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reopen or unarchive this task")
        task.refresh_from_db()
        self.assertEqual(task.status, Task.Status.CANCELLED)

    def checklist_admin_fixture(self):
        task = Task.objects.create(title="Admin checklist progress", owner=self.user, progress=50)
        complete = TaskChecklistItem.objects.create(task=task, title="Done", is_completed=True)
        remaining = TaskChecklistItem.objects.create(task=task, title="Remaining")
        request = RequestFactory().post("/admin/")
        request.user = self.user
        return task, complete, remaining, request, admin.site._registry[TaskChecklistItem]

    def test_admin_checklist_save_keeps_live_progress_and_step_audit(self):
        task, _, remaining, request, model_admin = self.checklist_admin_fixture()
        remaining.is_completed = True
        model_admin.save_model(request, remaining, None, change=True)
        task.refresh_from_db()
        remaining.refresh_from_db()
        self.assertEqual(task.progress, 100)
        self.assertEqual(task.status, Task.Status.PENDING)
        self.assertEqual(remaining.completed_by_id, self.user.pk)
        self.assertIsNotNone(remaining.completed_at)

    def test_admin_checklist_delete_and_restore_recompute_live_progress(self):
        task, complete, _, request, model_admin = self.checklist_admin_fixture()
        with patch.object(model_admin, "message_user"):
            model_admin.soft_delete_selected(request, TaskChecklistItem.objects.filter(pk=complete.pk))
            task.refresh_from_db()
            self.assertEqual(task.progress, 0)
            model_admin.restore_selected(request, TaskChecklistItem.all_objects.filter(pk=complete.pk))
        task.refresh_from_db()
        self.assertEqual(task.progress, 50)

    def test_admin_inline_delete_recomputes_progress(self):
        task, complete, _, request, _ = self.checklist_admin_fixture()
        formset = SimpleNamespace(model=TaskChecklistItem, save=lambda commit: [],
                                  deleted_objects=[complete], save_m2m=lambda: None)
        admin.site._registry[Task].save_formset(request, SimpleNamespace(instance=task), formset, change=True)
        task.refresh_from_db()
        self.assertEqual(task.progress, 0)

    def test_removing_last_checklist_step_resets_unaccepted_progress(self):
        task = Task.objects.create(title="All steps removed", owner=self.user, progress=100)
        item = TaskChecklistItem.objects.create(task=task, title="Removed", is_completed=True)
        request = RequestFactory().post("/admin/")
        request.user = self.user
        admin.site._registry[TaskChecklistItem].delete_model(request, item)
        task.refresh_from_db()
        self.assertEqual(task.progress, 0)
        self.assertEqual(task.status, Task.Status.PENDING)


class ProviderCompletionTests(SimpleTestCase):
    def test_incomplete_or_failed_status_is_not_a_successful_result(self):
        for status in ("incomplete", "failed", "cancelled", "in_progress", "queued"):
            response = SimpleNamespace(status=status, output_text='{"valid":"looking"}', output=[])
            with self.subTest(status=status), self.assertRaises(AIProviderError):
                OpenAIProvider._result(response)

    def test_refusal_cannot_be_mixed_into_a_successful_json_result(self):
        response = SimpleNamespace(status="completed", output_text='{"valid":"looking"}',
                                   output=[SimpleNamespace(type="message", content=[
                                       SimpleNamespace(type="refusal", refusal="Controlled refusal")])])
        with self.assertRaises(AIProviderError):
            OpenAIProvider._result(response)

    def test_empty_completed_response_is_not_a_successful_result(self):
        with self.assertRaises(AIProviderError):
            OpenAIProvider._result(SimpleNamespace(status="completed", output_text="", output=[]))
