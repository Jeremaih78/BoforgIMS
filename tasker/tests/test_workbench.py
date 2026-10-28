"""Work actions are atomic, scoped, versioned and reuse task lifecycle policy."""
from datetime import date
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import RequestFactory, TestCase
from django.utils import timezone

from accounting.models import Company
from tasker.models import SavedWorkView, Task, TaskActivity, TaskChecklistItem, TaskTimeEntry, Team, TeamMembership, WorkbenchPreference
from tasker.services.focus import start_focus
from tasker.services.workbench import (
    WorkConflict, apply_work_actions, delete_work_view, save_work_preferences,
    save_work_view, work_version,
)
from tasker.workbench_models import normalize_work_filters


class WorkbenchServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user("work-owner", is_staff=True)
        cls.peer = users.objects.create_user("work-peer", is_staff=True)
        cls.manager = users.objects.create_user("work-manager", is_staff=True)
        cls.outsider = users.objects.create_user("work-outsider")
        for user in (cls.owner, cls.peer, cls.manager, cls.outsider):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        cls.company = Company.objects.create(name="Workbench sample company")
        cls.team = Team.objects.create(company=cls.company, name="Workbench sample team")
        cls.foreign = Team.objects.create(company=cls.company, name="Workbench unassigned team")
        TeamMembership.objects.create(team=cls.team, user=cls.owner)
        TeamMembership.objects.create(team=cls.team, user=cls.peer)
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")

    def task(self, **changes):
        data = {"owner": self.owner, "title": "A work item"}
        data.update(changes)
        return Task.objects.create(**data)

    def act(self, tasks, action, *, user=None, **values):
        return apply_work_actions(user=user or self.owner, task_ids=[task.pk for task in tasks], action=action,
                                  versions={str(task.pk): work_version(task) for task in tasks}, **values)

    def test_mixed_private_selection_denies_without_changing_any_task(self):
        mine = self.task()
        foreign = self.task(owner=self.peer, title="Peer personal work")
        count = TaskActivity.objects.count()
        with self.assertRaises(PermissionDenied):
            self.act([mine, foreign], "priority", value="CRITICAL")
        mine.refresh_from_db(); foreign.refresh_from_db()
        self.assertEqual((mine.priority, foreign.priority), ("MEDIUM", "MEDIUM"))
        self.assertEqual(TaskActivity.objects.count(), count)

    def test_role_capability_without_membership_does_not_delegate_team_work(self):
        task = self.task(owner=self.peer, company=self.company, team=self.team)
        with self.assertRaises(PermissionDenied):
            self.act([task], "priority", value="HIGH", user=self.outsider)
        task.refresh_from_db()
        self.assertEqual(task.priority, "MEDIUM")

    def test_real_manager_can_triage_authorized_team_members(self):
        task = self.task(owner=self.peer, company=self.company, team=self.team)
        self.act([task], "priority", value="CRITICAL", user=self.manager)
        task.refresh_from_db()
        self.assertEqual(task.priority, "CRITICAL")
        self.assertEqual(task.updated_by_id, self.manager.pk)
        self.assertTrue(task.activities.filter(description="Work action: priority").exists())

    def test_revoked_membership_is_rechecked_after_list_was_loaded(self):
        task = self.task(owner=self.peer, company=self.company, team=self.team)
        TeamMembership.objects.filter(pk=self.manager_membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            self.act([task], "archive", user=self.manager)
        self.assertFalse(Task.objects.get(pk=task.pk).archived)

    def test_stale_or_missing_version_rejects_the_complete_selection(self):
        first, second = self.task(), self.task(title="Other item")
        versions = {str(task.pk): work_version(task) for task in (first, second)}
        second.notes = "A newer working note"
        second.save(update_fields=["notes", "updated_at"])
        with self.assertRaises(WorkConflict):
            apply_work_actions(user=self.owner, task_ids=[first.pk, second.pk], action="archive", versions=versions)
        self.assertFalse(Task.objects.get(pk=first.pk).archived)
        with self.assertRaises(WorkConflict):
            apply_work_actions(user=self.owner, task_ids=[first.pk], action="archive", versions={})

    def test_unknown_deleted_or_invalid_identifiers_never_partially_apply(self):
        first, deleted = self.task(), self.task(title="Deleted item")
        deleted.delete(user=self.owner)
        for ids in ([first.pk, deleted.pk], [first.pk, 999999]):
            with self.subTest(ids=ids), self.assertRaises(PermissionDenied):
                apply_work_actions(user=self.owner, task_ids=ids, action="archive", versions={str(first.pk): work_version(first)})
        for ids in ([], [True], ["1;anything"], [first.pk] * 51):
            with self.subTest(ids_count=len(ids)), self.assertRaises(ValidationError):
                apply_work_actions(user=self.owner, task_ids=ids, action="archive", versions={})
        self.assertFalse(Task.objects.get(pk=first.pk).archived)

    def test_write_failure_rolls_back_all_tasks_and_activity(self):
        first, second = self.task(), self.task(title="Second item")
        activity_count = TaskActivity.objects.count()
        with patch("tasker.services.focus.close_task_timers", side_effect=[[], RuntimeError("synthetic failure")]):
            with self.assertRaises(RuntimeError):
                self.act([first, second], "archive")
        self.assertFalse(Task.objects.get(pk=first.pk).archived)
        self.assertFalse(Task.objects.get(pk=second.pk).archived)
        self.assertEqual(TaskActivity.objects.count(), activity_count)

    def test_explicit_due_change_and_clear_preserve_note_content(self):
        task = self.task(notes="Working notes survive inline triage", due_date=date(2026, 10, 28), due_time="10:00")
        self.act([task], "due_date", value="2026-11-01")
        task.refresh_from_db()
        self.assertEqual(task.due_date, date(2026, 11, 1))
        self.act([task], "due_date", value=None)
        task.refresh_from_db()
        self.assertIsNone(task.due_date)
        self.assertIsNone(task.due_time)
        self.assertEqual(task.notes, "Working notes survive inline triage")

    def test_archive_closes_existing_timer_without_starting_another(self):
        task = self.task()
        entry = start_focus(task=task, user=self.owner)
        task.refresh_from_db()
        self.act([task], "archive")
        entry.refresh_from_db()
        self.assertIsNotNone(entry.ended_at)
        self.assertEqual(TaskTimeEntry.objects.filter(user=self.owner, ended_at__isnull=True).count(), 0)

    def test_all_definitions_are_reviewed_before_any_bulk_completion(self):
        first = self.task()
        second = self.task(title="Defined outcome", definition_of_done="Demonstration checked and evidence recorded")
        with self.assertRaises(ValueError):
            self.act([first, second], "complete")
        self.assertEqual(Task.objects.get(pk=first.pk).status, "PENDING")
        self.assertEqual(Task.objects.get(pk=second.pk).status, "PENDING")
        self.act([first, second], "complete", accept_definition=True)
        self.assertEqual(Task.objects.get(pk=first.pk).status, "COMPLETED")
        self.assertEqual(Task.objects.get(pk=second.pk).progress, 100)

    def test_completion_reuses_existing_timer_and_reopen_policy(self):
        task = self.task(definition_of_done="Output accepted")
        entry = start_focus(task=task, user=self.owner)
        task.refresh_from_db()
        self.act([task], "complete", accept_definition=True)
        task.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(task.status, "COMPLETED")
        self.assertEqual(task.progress, 100)
        self.assertIsNotNone(entry.ended_at)
        self.assertIsNotNone(task.completed_at)
        self.act([task], "reopen")
        task.refresh_from_db()
        self.assertEqual(task.status, "PENDING")
        self.assertEqual(task.progress, 0)
        self.assertIsNone(task.completed_at)

    def test_reopened_checklist_progress_remains_distinct_from_acceptance(self):
        task = self.task(status="COMPLETED", progress=100, completed_at=timezone.now())
        TaskChecklistItem.objects.create(task=task, title="First step", is_completed=True)
        TaskChecklistItem.objects.create(task=task, title="Second step", is_completed=False)
        self.act([task], "reopen")
        task.refresh_from_db()
        self.assertEqual(task.progress, 50)
        self.assertEqual(task.status, "PENDING")

    def test_blocking_keeps_the_waiting_record_and_records_clear_reason(self):
        task = self.task()
        entry = start_focus(task=task, user=self.owner)
        task.refresh_from_db()
        self.act([task], "block", value="Awaiting supplier confirmation")
        task.refresh_from_db(); entry.refresh_from_db()
        self.assertEqual(task.status, "BLOCKED")
        self.assertEqual(task.blocked_reason, "Awaiting supplier confirmation")
        self.assertIsNotNone(entry.ended_at)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())

    def test_assignment_requires_real_membership_of_every_selected_scope(self):
        valid = self.task(company=self.company, team=self.team)
        invalid = self.task(company=self.company, team=self.foreign)
        with self.assertRaises((PermissionDenied, ValidationError)):
            self.act([valid, invalid], "assign", value=self.peer.pk, user=self.manager)
        self.assertEqual(Task.objects.get(pk=valid.pk).owner_id, self.owner.pk)
        self.act([valid], "assign", value=self.peer.pk, user=self.manager)
        valid.refresh_from_db()
        self.assertEqual(valid.owner_id, self.peer.pk)
        self.assertEqual(valid.company_id, self.company.pk)
        self.assertEqual(valid.team_id, self.team.pk)

    def test_cancelled_and_archived_work_cannot_be_bulk_completed(self):
        first = self.task()
        cancelled = self.task(title="Cancelled", status="CANCELLED")
        with self.assertRaises(ValidationError):
            self.act([first, cancelled], "complete", accept_definition=True)
        self.assertEqual(Task.objects.get(pk=first.pk).status, "PENDING")

    def test_malformed_action_values_are_validation_errors_without_writes(self):
        task = self.task()
        activity_count = TaskActivity.objects.count()
        for action, value in (("assign", "9" * 100), ("assign", 0), ("assign", True), ("block", "Bad\x00reason")):
            with self.subTest(action=action), self.assertRaises(ValidationError):
                self.act([task], action, value=value)
        self.assertEqual(TaskActivity.objects.count(), activity_count)
        self.assertEqual(Task.objects.get(pk=task.pk).status, "PENDING")


class SavedWorkViewTests(TestCase):
    def setUp(self):
        users = get_user_model()
        self.owner = users.objects.create_user("saved-work-owner", is_staff=True)
        self.peer = users.objects.create_user("saved-work-peer", is_staff=True)
        for user in (self.owner, self.peer):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))

    def test_saved_view_persists_only_supported_filters(self):
        view = save_work_view(user=self.owner, name="My urgent work", filters={"preset": "mine", "priority": "CRITICAL", "layout": "board", "q": " print "})
        view.refresh_from_db()
        self.assertEqual(view.filters, {"preset": "mine", "priority": "CRITICAL", "layout": "board", "q": "print"})
        self.assertEqual(view.owner_id, self.owner.pk)
        for bad in ({"notes__contains": "private"}, {"owner": "1 OR 1=1"}, {"preset": "other-company"}, {"q": "a" * 301}, {"layout": ["board"]}):
            with self.subTest(keys=list(bad)), self.assertRaises(ValidationError):
                save_work_view(user=self.owner, name="Rejected", filters=bad)
        self.assertEqual(SavedWorkView.objects.count(), 1)

    def test_saved_views_cannot_be_edited_or_deleted_by_another_owner(self):
        view = save_work_view(user=self.owner, name="Owner view", filters={"owner": str(self.owner.pk)})
        with self.assertRaises(PermissionDenied):
            save_work_view(user=self.peer, pk=view.pk, name="Stolen", filters={})
        with self.assertRaises(PermissionDenied):
            delete_work_view(user=self.peer, pk=view.pk)
        self.assertEqual(SavedWorkView.objects.get(pk=view.pk).owner_id, self.owner.pk)
        delete_work_view(user=self.owner, pk=view.pk)
        self.assertFalse(SavedWorkView.objects.filter(pk=view.pk).exists())
        self.assertTrue(SavedWorkView.all_objects.get(pk=view.pk).is_deleted)

    def test_null_characters_are_rejected_before_reaching_the_database(self):
        for name, filters in (("Bad\x00name", {}), ("Valid name", {"q": "Bad\x00search"})):
            with self.subTest(filters=filters), self.assertRaises(ValidationError):
                save_work_view(user=self.owner, name=name, filters=filters)
        self.assertFalse(SavedWorkView.objects.exists())

    def test_view_names_are_unique_per_owner_and_filters_never_hold_authority(self):
        save_work_view(user=self.owner, name="Waiting", filters={"preset": "waiting", "owner": str(self.peer.pk)})
        with self.assertRaises(ValidationError):
            save_work_view(user=self.owner, name="Waiting", filters={})
        save_work_view(user=self.peer, name="Waiting", filters={})
        self.assertEqual(SavedWorkView.objects.count(), 2)
        self.assertEqual(normalize_work_filters({"owner": self.peer.pk}), {"owner": str(self.peer.pk)})

    def test_saved_default_layout_and_preset_persist_without_changing_scope(self):
        pref = save_work_preferences(user=self.owner, preset="mine", layout="board")
        again = save_work_preferences(user=self.owner, preset="duesoon", layout="list")
        self.assertEqual(pref.pk, again.pk)
        self.assertEqual(WorkbenchPreference.objects.get(owner=self.owner).preset, "duesoon")
        with self.assertRaises(ValidationError):
            save_work_preferences(user=self.owner, preset="foreign", layout="list")

    def test_inactive_or_capability_less_accounts_cannot_save_preferences(self):
        self.owner.user_permissions.clear()
        with self.assertRaises(PermissionDenied):
            save_work_preferences(user=self.owner, preset="mine", layout="list")
        self.peer.is_active = False
        self.peer.save(update_fields=["is_active"])
        with self.assertRaises(PermissionDenied):
            save_work_view(user=self.peer, name="No access", filters={})

    def test_staff_admin_view_is_owner_scoped_and_read_only(self):
        owned = save_work_view(user=self.owner, name="Private view name", filters={})
        foreign = save_work_view(user=self.peer, name="Peer view name", filters={})
        request = RequestFactory().get("/admin/")
        request.user = self.owner
        model_admin = admin.site._registry[SavedWorkView]
        self.assertTrue(model_admin.get_queryset(request).filter(pk=owned.pk).exists())
        self.assertFalse(model_admin.get_queryset(request).filter(pk=foreign.pk).exists())
        self.assertFalse(model_admin.has_view_permission(request, foreign))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, owned))
        self.assertFalse(model_admin.has_delete_permission(request, owned))
