"""Durability, owner boundaries, explicit scope and replay guarantees."""
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connections
from django.test import RequestFactory, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from accounting.models import Company
from tasker.capture_models import CaptureEvent, CaptureItem
from tasker.models import DailyPlan, Goal, Initiative, OrganizationAccess, Task, Team, TeamMembership
from tasker.services.captures import (
    CaptureConflict, CaptureReplayError, create_capture, import_legacy_notes,
    mutate_capture, optional_capture_proposal, promote_capture, update_capture,
    visible_captures_for,
)


class CaptureServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.owner = users.objects.create_user("capture-owner", is_staff=True)
        cls.peer = users.objects.create_user("capture-peer", is_staff=True)
        cls.stranger = users.objects.create_user("capture-stranger")
        cls.manager = users.objects.create_user("capture-manager", is_staff=True)
        permissions = Permission.objects.filter(content_type__app_label="tasker")
        for user in (cls.owner, cls.peer, cls.stranger, cls.manager):
            user.user_permissions.add(*permissions)
        cls.company = Company.objects.create(name="Capture company")
        cls.team = Team.objects.create(company=cls.company, name="Capture team")
        cls.foreign = Team.objects.create(company=cls.company, name="Different team")
        TeamMembership.objects.create(team=cls.team, user=cls.owner)
        cls.peer_membership = TeamMembership.objects.create(team=cls.team, user=cls.peer)
        TeamMembership.objects.create(team=cls.team, user=cls.manager, role=TeamMembership.Role.MANAGER)
        cls.goal = Goal.objects.create(company=cls.company, team=cls.team, title="Capture goal")

    def create(self, **values):
        data = {"user": self.owner, "client_key": uuid.uuid4(), "kind": "IDEA", "content": "Original private idea"}
        data.update(values)
        return create_capture(**data)

    def update(self, item, **values):
        data = {"user": self.owner, "pk": item.pk, "version": item.version,
                "kind": item.kind, "title": item.title, "content": item.content,
                "url": item.url, "tags": item.tags, "pinned": item.pinned}
        data.update(values)
        return update_capture(**data)

    def test_each_kind_is_private_unscheduled_and_provider_independent(self):
        with patch("tasker.ai.providers.openai.OpenAI") as provider:
            for kind in CaptureItem.Kind.values:
                item = self.create(kind=kind, url="https://example.com/resource" if kind == "LINK" else "")
                self.assertEqual(item.visibility, "PRIVATE")
                self.assertEqual(item.triage_state, "INBOX")
                self.assertIsNone(item.shared_team_id)
                self.assertIsNone(item.promoted_task_id)
                self.assertEqual(item.version, 1)
                self.assertEqual(item.original_text, "Original private idea")
            provider.assert_not_called()
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(Initiative.objects.count(), 0)

    def test_duplicate_creation_and_uncertain_retry_after_edit_make_one_item(self):
        key = uuid.uuid4()
        item = self.create(client_key=key)
        replay = self.create(client_key=key)
        self.assertEqual(item.pk, replay.pk)
        self.update(item, content="Edited later")
        self.assertEqual(self.create(client_key=key).pk, item.pk)
        self.assertEqual(CaptureItem.all_objects.count(), 1)
        self.assertEqual(CaptureEvent.objects.filter(action="capture.created").count(), 1)
        with self.assertRaises(CaptureReplayError):
            self.create(client_key=key, content="Different creation payload")

    def test_capture_identity_survives_soft_deletion_and_client_keys_are_owner_scoped(self):
        key = uuid.uuid4()
        item = self.create(client_key=key)
        mutate_capture(user=self.owner, pk=item.pk, version=1, action="delete")
        replay = self.create(client_key=key)
        self.assertTrue(replay.is_deleted)
        self.assertEqual(replay.pk, item.pk)
        self.assertNotEqual(self.create(user=self.peer, client_key=key).pk, item.pk)

    def test_save_conflict_retains_current_content_and_identical_retry_is_safe(self):
        item = self.create(content="Line one\r\nLine two")
        saved = self.update(item, content="New text\r\nSecond line")
        retry = self.update(item, version=1, content="New text\nSecond line")
        self.assertEqual(saved.version, retry.version)
        self.assertEqual(retry.version, 2)
        with self.assertRaises(CaptureConflict) as conflict:
            self.update(item, version=1, content="Stale overwrite")
        self.assertEqual(conflict.exception.current["content"], "New text\nSecond line")
        self.assertEqual(conflict.exception.version, 2)
        retry.refresh_from_db()
        self.assertEqual(retry.original_text, "Line one\r\nLine two")
        self.assertEqual(retry.content, "New text\nSecond line")

    def test_future_versions_and_boolean_versions_are_rejected(self):
        item = self.create()
        with self.assertRaises(CaptureConflict):
            self.update(item, version=50)
        with self.assertRaises(ValidationError):
            self.update(item, version=True)

    def test_private_captures_stay_private_from_managers_company_grants_and_superusers_in_inbox(self):
        item = self.create()
        OrganizationAccess.objects.create(company=self.company, user=self.manager, can_view_all=True, can_manage=True)
        self.assertFalse(visible_captures_for(self.manager).filter(pk=item.pk).exists())
        superuser = get_user_model().objects.create_superuser("capture-root", password="synthetic")
        self.assertFalse(visible_captures_for(superuser).filter(pk=item.pk).exists())
        self.assertFalse(visible_captures_for(self.peer).filter(pk=item.pk).exists())

    def test_explicit_team_sharing_follows_live_membership_but_never_grants_edit(self):
        item = self.create()
        shared = mutate_capture(user=self.owner, pk=item.pk, version=1, action="share", team=self.team)
        self.assertTrue(visible_captures_for(self.peer).filter(pk=item.pk).exists())
        self.assertFalse(visible_captures_for(self.stranger).filter(pk=item.pk).exists())
        with self.assertRaises(PermissionDenied):
            self.update(shared, user=self.peer, content="Coworker edit")
        TeamMembership.objects.filter(pk=self.peer_membership.pk).update(is_active=False)
        self.assertFalse(visible_captures_for(self.peer).filter(pk=item.pk).exists())
        private = mutate_capture(user=self.owner, pk=item.pk, version=shared.version, action="private")
        self.assertEqual(private.visibility, "PRIVATE")
        self.assertIsNone(private.shared_team_id)

    def test_unauthorized_share_is_rejected_without_changes(self):
        item = self.create()
        with self.assertRaises(PermissionDenied):
            mutate_capture(user=self.owner, pk=item.pk, version=1, action="share", team=self.foreign)
        item.refresh_from_db()
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertEqual(item.version, 1)

    def test_archiving_pinning_and_trash_are_versioned_recoverable_and_replay_safe(self):
        item = self.create(tags=["  Sales ", "sales", "Quick idea"])
        self.assertEqual(item.tags, ["sales", "quick idea"])
        pinned = mutate_capture(user=self.owner, pk=item.pk, version=1, action="pin")
        self.assertTrue(pinned.pinned)
        self.assertEqual(mutate_capture(user=self.owner, pk=item.pk, version=1, action="pin").version, 2)
        archived = mutate_capture(user=self.owner, pk=item.pk, version=2, action="archive")
        self.assertEqual(archived.triage_state, "ARCHIVED")
        deleted = mutate_capture(user=self.owner, pk=item.pk, version=3, action="delete")
        self.assertEqual(CaptureItem.objects.count(), 0)
        self.assertIsNotNone(deleted.deleted_at)
        restored = mutate_capture(user=self.owner, pk=item.pk, version=4, action="restore")
        self.assertIsNone(restored.deleted_at)
        self.assertEqual(restored.triage_state, "ARCHIVED")
        self.assertEqual(restored.original_text, "Original private idea")

    def test_shared_deleted_capture_is_visible_only_to_owner_in_trash(self):
        item = self.create()
        item = mutate_capture(user=self.owner, pk=item.pk, version=1, action="share", team=self.team)
        mutate_capture(user=self.owner, pk=item.pk, version=item.version, action="delete")
        self.assertFalse(visible_captures_for(self.peer, include_deleted=True).filter(pk=item.pk).exists())
        self.assertTrue(visible_captures_for(self.owner, include_deleted=True).filter(pk=item.pk).exists())

    def test_personal_promotion_is_unscheduled_once_and_preserves_source_privacy(self):
        item = self.create(title="Useful improvement")
        task = promote_capture(user=self.owner, pk=item.pk, version=1)
        replay = promote_capture(user=self.owner, pk=item.pk, version=1)
        self.assertEqual(task.pk, replay.pk)
        self.assertEqual(Task.objects.count(), 1)
        self.assertIsNone(task.due_date)
        self.assertIsNone(task.team_id)
        self.assertIn(item.get_absolute_url(), task.description)
        item.refresh_from_db()
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertEqual(item.triage_state, "PROMOTED")
        self.assertEqual(item.original_text, "Original private idea")
        self.assertFalse(visible_captures_for(self.manager).filter(pk=item.pk).exists())
        with self.assertRaises(CaptureReplayError):
            promote_capture(user=self.owner, pk=item.pk, version=1, target="initiative", goal=self.goal)

    def test_explicit_team_promotion_can_share_result_without_sharing_original(self):
        item = self.create()
        task = promote_capture(user=self.owner, pk=item.pk, version=1, team=self.team)
        self.assertEqual(task.team_id, self.team.pk)
        self.assertEqual(task.company_id, self.company.pk)
        item.refresh_from_db()
        self.assertEqual(item.visibility, "PRIVATE")
        self.assertFalse(visible_captures_for(self.manager).filter(pk=item.pk).exists())

    def test_promotion_requires_latest_version_task_capability_and_team_assignment_scope(self):
        item = self.create()
        self.update(item, title="Changed title")
        with self.assertRaises(CaptureConflict):
            promote_capture(user=self.owner, pk=item.pk, version=1)
        item.refresh_from_db()
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.owner, pk=item.pk, version=item.version, team=self.foreign)
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="add_task"))
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.owner, pk=item.pk, version=item.version)
        self.assertEqual(Task.objects.count(), 0)

    def test_promotion_replay_rechecks_capability_and_current_destination_visibility(self):
        item = self.create()
        task = promote_capture(user=self.owner, pk=item.pk, version=1)
        Task.objects.filter(pk=task.pk).update(owner=self.stranger)
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.owner, pk=item.pk, version=1)
        Task.objects.filter(pk=task.pk).update(owner=self.owner)
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="add_task"))
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.owner, pk=item.pk, version=1)
        self.assertEqual(Task.objects.count(), 1)

    @override_settings(TASKER_DIRECTION_ENABLED=True)
    def test_initiative_promotion_uses_existing_management_policy_and_is_idempotent(self):
        item = self.create(user=self.manager)
        initiative = promote_capture(user=self.manager, pk=item.pk, version=1, target="initiative", goal=self.goal)
        self.assertEqual(initiative.goal_id, self.goal.pk)
        self.assertEqual(promote_capture(user=self.manager, pk=item.pk, version=1, target="initiative", goal=self.goal).pk, initiative.pk)
        own_item = self.create()
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.owner, pk=own_item.pk, version=1, target="initiative", goal=self.goal)

    @override_settings(TASKER_DIRECTION_ENABLED=False)
    def test_initiative_promotion_stays_disabled_with_direction_feature(self):
        item = self.create(user=self.manager)
        with self.assertRaises(PermissionDenied):
            promote_capture(user=self.manager, pk=item.pk, version=1, target="initiative", goal=self.goal)

    def test_legacy_scratchpad_import_is_exact_private_and_unique_even_after_soft_deletion(self):
        original = "Old scratchpad\r\nStill recoverable\r\n"
        plan = DailyPlan.objects.create(owner=self.owner, quick_notes=original)
        plan.delete(user=self.owner)
        item = import_legacy_notes(user=self.owner, plan_pk=plan.pk)
        self.assertEqual(item.content, original)
        self.assertEqual(item.original_text, original)
        self.assertEqual(item.visibility, "PRIVATE")
        plan.refresh_from_db()
        self.assertEqual(plan.quick_notes, original)
        mutate_capture(user=self.owner, pk=item.pk, version=1, action="delete")
        self.assertEqual(import_legacy_notes(user=self.owner, plan_pk=plan.pk).pk, item.pk)
        self.assertEqual(CaptureItem.all_objects.count(), 1)
        with self.assertRaises(PermissionDenied):
            import_legacy_notes(user=self.peer, plan_pk=plan.pk)

    def test_model_rejects_mutated_original_owner_source_and_unsafe_link(self):
        item = self.create()
        item.original_text = "Rewritten history"
        with self.assertRaises(ValidationError):
            item.save()
        with self.assertRaises(ValidationError):
            self.create(kind="LINK", url="javascript:alert(1)")
        with self.assertRaises(ValidationError):
            self.create(tags={"private": "value"})

    def test_content_does_not_enter_audit_payload_or_optional_ai_proposals(self):
        item = self.create()
        self.update(item, content="Changed private text")
        payloads = list(CaptureEvent.objects.filter(capture=item).values_list("details", flat=True))
        self.assertNotIn("Original private idea", str(payloads))
        self.assertNotIn("Changed private text", str(payloads))
        self.assertFalse(optional_capture_proposal(item=item, user=self.owner)["available"])
        self.assertNotIn("content", optional_capture_proposal(item=item, user=self.owner))

    def test_staff_admin_is_owner_scoped_read_only_and_never_grants_private_manager_access(self):
        item = self.create()
        request = RequestFactory().get("/admin/tasker/captureitem/")
        request.user = self.manager
        model_admin = admin.site._registry[CaptureItem]
        self.assertFalse(model_admin.get_queryset(request).filter(pk=item.pk).exists())
        self.assertFalse(model_admin.has_view_permission(request, item))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_change_permission(request, item))
        self.assertFalse(model_admin.has_delete_permission(request, item))
        request.user = self.owner
        self.assertTrue(model_admin.get_queryset(request).filter(pk=item.pk).exists())
        self.assertTrue(model_admin.has_view_permission(request, item))
        self.assertIn("original_text", model_admin.get_readonly_fields(request, item))


    def test_readonly_admin_does_not_reveal_a_reassigned_destination_title(self):
        item = self.create()
        task = promote_capture(user=self.owner, pk=item.pk, version=item.version)
        Task.objects.filter(pk=task.pk).update(owner=self.stranger, title="Restricted destination after reassignment")
        self.client.force_login(self.owner)
        response = self.client.get(reverse("admin:tasker_captureitem_change", args=[item.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Restricted destination after reassignment")


class CaptureConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = get_user_model().objects.create_user("capture-concurrent-owner")
        self.owner.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker", codename__in=("view_task", "add_task")))

    def race(self, operation):
        barrier = Barrier(2)

        def worker():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.owner.pk)
                barrier.wait(timeout=15)
                return operation(user).pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(worker), executor.submit(worker)]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_create_with_one_client_key_produces_one_capture(self):
        key = uuid.uuid4()
        ids = self.race(lambda user: create_capture(user=user, client_key=key, kind="IDEA", content="Concurrent durable idea"))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(CaptureItem.all_objects.count(), 1)
        self.assertEqual(CaptureEvent.objects.filter(action="capture.created").count(), 1)

    def test_concurrent_promotion_produces_one_task_and_one_provenance_link(self):
        item = create_capture(user=self.owner, client_key=uuid.uuid4(), kind="IDEA", content="Promote this once")
        ids = self.race(lambda user: promote_capture(user=user, pk=item.pk, version=1))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(Task.objects.count(), 1)
        self.assertEqual(CaptureItem.objects.get(pk=item.pk).promoted_task_id, ids[0])
        self.assertEqual(CaptureEvent.objects.filter(action="capture.promoted").count(), 1)
