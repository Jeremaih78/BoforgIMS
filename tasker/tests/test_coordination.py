"""Shared requests retain commitments, scope and replay-safe history."""
from datetime import datetime, time, timedelta
import uuid
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase

from accounting.models import Company
from tasker.coordination_models import CoordinationEvent, CoordinationRequest
from tasker.job_models import OutboxJob
from tasker.models import Task, TaskTimeEntry, Team, TeamMembership, WorkPolicy
from tasker.notification_models import Notification, NotificationDelivery
from tasker.services.coordination import (CoordinationConflict, acknowledge_request, cancel_request,
    create_request, dispatch_event, reconcile_coordination, resolve_request, resolver_candidates,
    update_request, visible_requests_for)
from tasker.services.focus import start_focus
from tasker.services.workbench import work_version


class CoordinationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = get_user_model().objects.create_user("coord-owner")
        cls.manager = get_user_model().objects.create_user("coord-manager")
        cls.peer = get_user_model().objects.create_user("coord-peer")
        cls.other_manager = get_user_model().objects.create_user("coord-other-manager")
        for user in (cls.owner, cls.manager, cls.peer, cls.other_manager):
            user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        cls.company = Company.objects.create(name="Coordination sample company")
        cls.team = Team.objects.create(company=cls.company, name="Sample work team")
        cls.other_team = Team.objects.create(company=cls.company, name="Separate team")
        TeamMembership.objects.create(team=cls.team, user=cls.owner)
        TeamMembership.objects.create(team=cls.team, user=cls.peer)
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.other_team, user=cls.other_manager, role="MANAGER")
        for user in (cls.owner, cls.manager, cls.peer, cls.other_manager):
            WorkPolicy.objects.create(owner=user, configured=True, weekdays=list(range(7)), start_time=time(8), end_time=time(17), buffer_minutes=30)

    def setUp(self):
        self.now = datetime(2030, 1, 7, 10, tzinfo=ZoneInfo("Africa/Harare"))
        self.clock = patch("django.utils.timezone.now", return_value=self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
            title="Sample customer quotation", estimated_duration=timedelta(minutes=60),
            definition_of_done="Sample scope accepted")

    def raise_request(self, **changes):
        data = {"user": self.owner, "task": self.task, "kind": "BLOCKER", "resolver": self.manager,
            "reason": "Supplier approval is pending", "requested_action": "Confirm the sample stock allocation",
            "expected_response_at": self.now+timedelta(hours=1), "client_key": uuid.uuid4(),
            "expected_task_version": work_version(Task.objects.get(pk=self.task.pk))}
        data.update(changes)
        return create_request(**data)

    def event(self, item, event="raised"):
        return dispatch_event({"request_id": item.pk, "revision": str(item.revision), "event": event})

    def test_raising_blocker_stops_existing_timer_and_records_required_shared_context(self):
        start_focus(task=self.task, user=self.owner)
        item = self.raise_request()
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "BLOCKED")
        self.assertFalse(TaskTimeEntry.objects.filter(ended_at__isnull=True).exists())
        self.assertEqual(item.resolver, self.manager)
        self.assertEqual(item.requested_action, "Confirm the sample stock allocation")
        self.assertEqual(CoordinationEvent.objects.filter(request=item, action="raised").count(), 1)
        self.assertEqual(OutboxJob.objects.filter(kind="coordination.dispatch").count(), 1)

    def test_create_retry_reuses_request_event_job_and_rejects_changed_key_payload(self):
        key = uuid.uuid4()
        first = self.raise_request(client_key=key)
        second = self.raise_request(client_key=key, expected_task_version="stale-proof")
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(CoordinationRequest.objects.count(), 1)
        self.assertEqual(CoordinationEvent.objects.filter(action="raised").count(), 1)
        with self.assertRaises(CoordinationConflict):
            self.raise_request(client_key=key, requested_action="Different action")

    def test_missing_action_reason_response_or_stale_task_cannot_block_work(self):
        for change in ({"reason": " "}, {"requested_action": ""}, {"expected_response_at": self.now},
                       {"expected_response_at": self.now.replace(tzinfo=None)}, {"expected_response_at": "tomorrow"},
                       {"expected_task_version": "stale"}, {"expected_task_version": None}):
            with self.subTest(change=change), self.assertRaises((ValidationError, CoordinationConflict)):
                self.raise_request(**change)
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "PENDING")
        self.assertFalse(CoordinationRequest.objects.exists())

    def test_resolver_choices_do_not_grant_peer_or_cross_team_source_access(self):
        ids = set(resolver_candidates(task=self.task, user=self.owner).values_list("pk", flat=True))
        self.assertIn(self.manager.pk, ids)
        self.assertNotIn(self.peer.pk, ids)
        self.assertNotIn(self.other_manager.pk, ids)
        with self.assertRaises(ValidationError):
            self.raise_request(resolver=self.peer)
        self.assertFalse(CoordinationRequest.objects.exists())

    def test_unrelated_employee_roster_does_not_crowd_out_new_owner_and_manager(self):
        users = get_user_model().objects.bulk_create([
            get_user_model()(username=f"coord-unrelated-employee-{index}", is_active=True)
            for index in range(105)])
        TeamMembership.objects.bulk_create([TeamMembership(team=self.team, user=user) for user in users])
        late_owner = get_user_model().objects.create_user("coord-late-owner")
        late_manager = get_user_model().objects.create_user("coord-late-manager")
        for user in (late_owner, late_manager):
            user.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        TeamMembership.objects.create(team=self.team, user=late_owner)
        TeamMembership.objects.create(team=self.team, user=late_manager, role="MANAGER")
        task = Task.objects.create(owner=late_owner, company=self.company, team=self.team,
            title="Sample work after a larger employee roster")
        ids = list(resolver_candidates(task=task, user=late_owner).values_list("pk", flat=True))
        self.assertEqual(ids[0], late_owner.pk)
        self.assertIn(late_manager.pk, ids)
        self.assertIn(self.manager.pk, ids)
        self.assertFalse(set(ids) & {user.pk for user in users})

    def test_acknowledgement_retains_waiting_task_and_stops_unacknowledged_escalation(self):
        item = self.raise_request(escalation_policy="UNACKNOWLEDGED")
        self.event(item)
        item = acknowledge_request(user=self.manager, request_id=item.pk, expected_revision=item.revision)
        self.task.refresh_from_db()
        self.assertEqual(item.status, "ACKNOWLEDGED")
        self.assertEqual(self.task.status, "BLOCKED")
        self.assertIsNotNone(item.acknowledged_at)
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=2)):
            self.event(item, "escalate")
        self.assertFalse(Notification.objects.filter(kind="ESCALATION").exists())
        self.assertTrue(Notification.objects.filter(request=item, kind="REQUEST", cancelled_at__isnull=False).exists())

    def test_handoff_receipt_and_resolution_preserve_original_owner_and_outcome(self):
        item = self.raise_request(kind="HANDOFF")
        item = acknowledge_request(user=self.manager, request_id=item.pk, expected_revision=item.revision)
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.owner.pk)
        self.assertEqual(self.task.status, "BLOCKED")
        item = resolve_request(user=self.manager, request_id=item.pk, expected_revision=item.revision,
            resolution="Requested sample allocation confirmed")
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.owner.pk)
        self.assertEqual(self.task.status, "PENDING")
        self.assertIsNone(self.task.completed_at)
        self.assertEqual(self.task.progress, 0)

    def test_resolution_never_clears_a_new_unrelated_blocker(self):
        item = self.raise_request()
        self.task.refresh_from_db()
        self.task.blocked_reason = "A different compliance review is pending"
        self.task.save()
        resolve_request(user=self.manager, request_id=item.pk, expected_revision=item.revision, resolution="Original question answered")
        self.task.refresh_from_db()
        self.assertEqual(self.task.status, "BLOCKED")
        self.assertEqual(self.task.blocked_reason, "A different compliance review is pending")

    def test_resolving_one_request_does_not_drop_another_active_commitment(self):
        for first_kind in ("BLOCKER", "HANDOFF"):
            with self.subTest(first_resolved=first_kind):
                self.task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
                    title=f"Sample overlapping requests: resolve {first_kind} first")
                blocker = self.raise_request()
                handoff = self.raise_request(kind="HANDOFF", reason="Await the demonstration handoff")
                first, last = (blocker, handoff) if first_kind == "BLOCKER" else (handoff, blocker)
                resolve_request(user=self.manager, request_id=first.pk, expected_revision=first.revision,
                    resolution="First requested action returned")
                self.task.refresh_from_db()
                self.assertEqual(self.task.status, "BLOCKED")
                self.assertEqual(self.task.blocked_reason, last.reason)
                resolve_request(user=self.manager, request_id=last.pk, expected_revision=last.revision,
                    resolution="Remaining requested action returned")
                self.task.refresh_from_db()
                self.assertEqual(self.task.status, "PENDING")
                self.assertEqual(self.task.blocked_reason, "")
                self.assertEqual(self.task.owner_id, self.owner.pk)
                self.assertIsNone(self.task.completed_at)
                self.assertEqual(self.task.progress, 0)

    def test_only_current_resolver_can_ack_and_stale_update_keeps_new_assignment(self):
        item = self.raise_request()
        with self.assertRaises(PermissionDenied):
            acknowledge_request(user=self.owner, request_id=item.pk, expected_revision=item.revision)
        prior = item.revision
        updated = update_request(user=self.owner, request_id=item.pk, expected_revision=prior, resolver=self.owner,
            reason=item.reason, requested_action=item.requested_action, expected_response_at=item.expected_response_at)
        with self.assertRaises(CoordinationConflict):
            update_request(user=self.owner, request_id=item.pk, expected_revision=prior, resolver=self.manager,
                reason=item.reason, requested_action=item.requested_action, expected_response_at=item.expected_response_at)
        self.assertEqual(updated.resolver_id, self.owner.pk)

    def test_duplicate_and_out_of_order_dispatches_do_not_repeat_alerts(self):
        item = self.raise_request()
        old_payload = {"request_id": item.pk, "revision": str(item.revision), "event": "raised"}
        self.event(item); self.event(item)
        self.assertEqual(Notification.objects.filter(request=item, kind="REQUEST").count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(notification__request=item).count(), 1)
        item = acknowledge_request(user=self.manager, request_id=item.pk, expected_revision=item.revision)
        self.assertTrue(dispatch_event(old_payload)["obsolete"])
        self.event(item, "acknowledged"); self.event(item, "acknowledged")
        self.assertEqual(Notification.objects.filter(request=item, kind="ACKNOWLEDGED").count(), 1)

    def test_reconciliation_cancels_revoked_resolver_and_never_reopens_completed_task(self):
        item = self.raise_request()
        self.event(item)
        self.manager_membership.is_active = False
        self.manager_membership.save()
        result = reconcile_coordination()
        item.refresh_from_db(); self.task.refresh_from_db()
        self.assertEqual(result["cancelled"], 1)
        self.assertEqual(item.status, "CANCELLED")
        self.assertEqual(self.task.status, "BLOCKED")
        self.assertFalse(visible_requests_for(self.manager).filter(pk=item.pk).exists())
        self.assertTrue(NotificationDelivery.objects.filter(notification__request=item, state="CANCELLED").exists())

    def test_expected_response_escalation_targets_current_manager_once(self):
        second = get_user_model().objects.create_user("coord-second-manager")
        second.user_permissions.set(Permission.objects.filter(content_type__app_label="tasker"))
        TeamMembership.objects.create(team=self.team, user=second, role="MANAGER")
        item = self.raise_request(escalation_policy="UNACKNOWLEDGED")
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=2)):
            self.event(item, "escalate"); self.event(item, "escalate")
        item.refresh_from_db()
        self.assertEqual(item.escalated_to_id, second.pk)
        self.assertEqual(Notification.objects.filter(kind="ESCALATION", owner=second).count(), 1)
        self.assertEqual(CoordinationEvent.objects.filter(action="escalated").count(), 1)

    def test_completed_source_cannot_dispatch_a_request_or_escalation_before_repair(self):
        item = self.raise_request(escalation_policy="UNACKNOWLEDGED")
        # A missed invalidation must not make a stale source actionable.
        Task.objects.filter(pk=self.task.pk).update(status="COMPLETED", progress=100, completed_at=self.now)
        self.assertTrue(self.event(item)["obsolete"])
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=2)):
            self.assertTrue(self.event(item, "escalate")["obsolete"])
        self.assertFalse(Notification.objects.exists())

    def test_reconciliation_rotates_a_bounded_page_of_open_requests(self):
        self.raise_request()
        self.raise_request(kind="HANDOFF", reason="Sample handoff needs confirmation")
        reconcile_coordination(now=self.now, limit=1)
        reconcile_coordination(now=self.now, limit=1)
        self.assertEqual(CoordinationRequest.objects.filter(last_checked_at=self.now).count(), 2)

    def test_cancel_is_reasoned_and_does_not_complete_the_task(self):
        item = self.raise_request()
        item = cancel_request(user=self.owner, request_id=item.pk, expected_revision=item.revision,
            reason="The original owner will handle this directly")
        self.task.refresh_from_db()
        self.assertEqual(item.status, "CANCELLED")
        self.assertEqual(self.task.status, "PENDING")
        self.assertIsNone(self.task.completed_at)
