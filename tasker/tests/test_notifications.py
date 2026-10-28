"""Attention is targeted, deferred, deduplicated and reauthorized at delivery."""
from datetime import time, timedelta, timezone as datetime_timezone
import uuid
from unittest.mock import patch

from django.core.exceptions import PermissionDenied
from django.test import TestCase

from tasker.coordination_models import CoordinationRequest
from tasker.job_models import OutboxJob
from tasker.models import Availability, Task, WorkPolicy
from tasker.notification_models import Notification, NotificationDelivery, NotificationPreference
from tasker.services.coordination import create_request, dispatch_event, resolve_request
from tasker.services.focus import start_focus
from tasker.services.jobs import JobDeferred, run_worker_once
from tasker.services.notifications import (create_notification, deliver_notification, enqueue_delivery,
    mark_notification, notification_context, reconcile_notifications, save_notification_preferences,
    visible_notifications_for)
from tasker.services.workbench import work_version
from tasker.tests import test_coordination as coordination_fixture


class NotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        coordination_fixture.CoordinationTests.setUpTestData.__func__(cls)

    def setUp(self):
        coordination_fixture.CoordinationTests.setUp(self)

    def request(self, task=None):
        task = task or self.task
        # Rendered actions carry the persisted source proof. The test clock is
        # in Harare while PostgreSQL returns equivalent timestamps in UTC.
        task = Task.objects.get(pk=task.pk)
        return create_request(user=self.owner, task=task, kind="BLOCKER", resolver=self.manager,
            reason="Shared sample stock approval", requested_action="Confirm the sample stock allocation",
            expected_response_at=self.now+timedelta(hours=1), client_key=uuid.uuid4(),
            expected_task_version=work_version(task))

    def alert(self, task=None):
        item = self.request(task)
        dispatch_event({"request_id": item.pk, "revision": str(item.revision), "event": "raised"})
        notification = Notification.objects.get(request=item, kind="REQUEST")
        return item, notification, NotificationDelivery.objects.get(notification=notification)

    def delivery(self, delivery):
        return deliver_notification({"delivery_id": delivery.pk})

    def test_worker_delivers_once_with_canonical_action_to_only_the_resolver(self):
        item, notification, delivery = self.alert()
        run_worker_once(worker_id="notification-test", limit=20)
        delivery.refresh_from_db(); notification.refresh_from_db()
        self.assertEqual(delivery.state, "DELIVERED")
        self.assertEqual(delivery.attempt_count, 1)
        self.assertIsNotNone(notification.published_at)
        self.assertEqual(list(visible_notifications_for(self.manager)), [notification])
        self.assertFalse(visible_notifications_for(self.peer).exists())
        context = notification_context(notification, self.manager)
        self.assertIn(self.task.title, context["title"])
        self.assertEqual(context["details"], item.requested_action)
        self.assertIn(str(item.pk), context["url"])
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.attempt_count, 1)

    def test_quiet_hours_persist_held_ledger_and_job_without_consuming_failure_attempts(self):
        NotificationPreference.objects.create(owner=self.manager, quiet_enabled=True, quiet_start=time(9), quiet_end=time(11))
        item, notification, delivery = self.alert()
        run_worker_once(worker_id="quiet-test", limit=20)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "HELD")
        self.assertEqual(delivery.error_code, "quiet_hours")
        job = OutboxJob.objects.get(dedupe_key=f"delivery:{delivery.pk}")
        self.assertEqual(job.state, "QUEUED")
        self.assertEqual(job.attempts, 0)
        self.assertEqual(job.run_after.hour, 9)  # 11:00 Africa/Harare in UTC.
        self.assertFalse(visible_notifications_for(self.manager).exists())
        self.assertTrue(visible_notifications_for(self.manager, include_held=True).filter(pk=notification.pk).exists())
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=1)):
            run_worker_once(worker_id="quiet-test", limit=20)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "DELIVERED")

    def test_overnight_quiet_window_uses_employee_zone(self):
        NotificationPreference.objects.create(owner=self.manager, timezone_name="Africa/Harare", quiet_enabled=True,
            quiet_start=time(22), quiet_end=time(7), respect_work_hours=False)
        item, notification, delivery = self.alert()
        with patch("django.utils.timezone.now", return_value=self.now.replace(hour=23)):
            result = self.delivery(delivery)
        self.assertIsInstance(result, JobDeferred)
        self.assertEqual(result.run_after.astimezone(self.now.tzinfo).hour, 7)

    def test_focus_and_fixed_commitments_hold_routine_attention(self):
        own_task = Task.objects.create(owner=self.manager, title="Manager focus sample")
        start_focus(task=own_task, user=self.manager)
        item, notification, delivery = self.alert()
        result = self.delivery(delivery)
        self.assertIsInstance(result, JobDeferred)
        delivery.refresh_from_db()
        self.assertEqual(delivery.error_code, "focus_active")
        from tasker.services.focus import stop_focus
        stop_focus(task=own_task, user=self.manager)
        Availability.objects.create(owner=self.manager, kind="LEAVE", starts_at=self.now-timedelta(minutes=5),
            ends_at=self.now+timedelta(hours=2), private_note="Private sample leave context")
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.error_code, "unavailable_window")
        self.assertNotIn("Private sample", delivery.error_code)

    def test_unknown_work_hours_remain_held_without_inventing_a_schedule(self):
        WorkPolicy.objects.filter(owner=self.manager).update(configured=False)
        item, notification, delivery = self.alert()
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "HELD")
        self.assertEqual(delivery.error_code, "work_hours_unknown")
        self.assertFalse(notification.published_at)

    def test_read_is_not_acknowledgement_and_ack_is_not_resolution(self):
        item, notification, delivery = self.alert()
        self.delivery(delivery)
        read = mark_notification(user=self.manager, notification_id=notification.pk)
        item.refresh_from_db()
        self.assertIsNotNone(read.read_at)
        self.assertIsNone(read.acknowledged_at)
        self.assertEqual(item.status, "OPEN")
        marked = mark_notification(user=self.manager, notification_id=notification.pk, acknowledge=True)
        item.refresh_from_db(); self.task.refresh_from_db()
        self.assertIsNotNone(marked.acknowledged_at)
        self.assertEqual(item.status, "ACKNOWLEDGED")
        self.assertEqual(self.task.status, "BLOCKED")

    def test_foreign_notification_read_cannot_ack_or_reveal_source(self):
        item, notification, delivery = self.alert()
        with self.assertRaises(PermissionDenied):
            mark_notification(user=self.peer, notification_id=notification.pk, acknowledge=True)
        with self.assertRaises(PermissionDenied):
            notification_context(notification, self.peer)
        item.refresh_from_db()
        self.assertEqual(item.status, "OPEN")

    def test_resolution_cancels_pending_delivery_and_stale_dispatch_cannot_recreate_it(self):
        item, notification, delivery = self.alert()
        old = {"request_id": item.pk, "revision": str(item.revision), "event": "raised"}
        resolve_request(user=self.manager, request_id=item.pk, expected_revision=item.revision,
            resolution="Sample stock approval received")
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")
        self.assertTrue(dispatch_event(old)["obsolete"])
        self.assertFalse(visible_notifications_for(self.manager, include_held=True).exists())

    def test_send_time_membership_and_reassignment_checks_cancel_obsolete_alerts(self):
        item, notification, delivery = self.alert()
        self.manager_membership.is_active = False
        self.manager_membership.save()
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")
        self.assertFalse(visible_notifications_for(self.manager, include_held=True).exists())
        self.assertNotIn(self.task.title, delivery.error_code)

    def test_digest_groups_authorized_updates_and_retains_delivery_evidence(self):
        NotificationPreference.objects.create(owner=self.manager, digest_mode="HOURLY")
        first, note1, delivery1 = self.alert()
        second_task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
            title="Second coordination sample")
        second, note2, delivery2 = self.alert(second_task)
        result = self.delivery(delivery1)
        self.assertIsInstance(result, JobDeferred)
        self.assertFalse(visible_notifications_for(self.manager).exists())
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=1)):
            self.delivery(delivery1)
            self.delivery(delivery2)
        parent = Notification.objects.get(kind="DIGEST", owner=self.manager)
        self.assertEqual(parent.summary_count, 2)
        self.assertEqual(list(visible_notifications_for(self.manager)), [parent])
        self.assertEqual(NotificationDelivery.objects.filter(notification__in=(note1, note2), state="DELIVERED").count(), 2)
        self.assertEqual(len(notification_context(parent, self.manager)["items"]), 2)

    def test_preference_save_is_versioned_and_preserves_concurrent_choice(self):
        data = {"user": self.manager, "timezone_name": "Africa/Harare", "quiet_enabled": True,
            "quiet_start": time(22), "quiet_end": time(7), "respect_work_hours": True,
            "suppress_during_focus": True, "digest_mode": "HOURLY", "expected_revision": "default-v1"}
        record = save_notification_preferences(**data)
        self.assertEqual(record.digest_mode, "HOURLY")
        with self.assertRaises(ValueError):
            save_notification_preferences(**{**data, "digest_mode": "IMMEDIATE"})
        self.assertEqual(NotificationPreference.objects.get(owner=self.manager).digest_mode, "HOURLY")

    def test_delivery_key_cannot_be_repurposed_for_another_recipient(self):
        item, notification, delivery = self.alert()
        with self.assertRaises((ValueError, PermissionDenied)):
            enqueue_delivery(recipient=self.peer, channel="EMAIL", dedupe_key=delivery.dedupe_key, notification=notification)
        self.assertEqual(NotificationDelivery.objects.count(), 1)

    def test_missing_event_reconciliation_reuses_existing_alert_and_delivery(self):
        item, notification, delivery = self.alert()
        result = reconcile_notifications()
        self.assertEqual(result["queued"], 1)
        self.assertEqual(Notification.objects.count(), 1)
        self.assertEqual(NotificationDelivery.objects.count(), 1)
        self.assertEqual(OutboxJob.objects.filter(dedupe_key=f"delivery:{delivery.pk}").count(), 1)

    def test_cached_context_rechecks_current_source_after_reassignment(self):
        item, notification, delivery = self.alert()
        notification_context(notification, self.manager)  # Prime its original source.
        Task.objects.filter(pk=self.task.pk).update(owner=self.peer)
        self.assertFalse(visible_notifications_for(self.manager, include_held=True).exists())
        with self.assertRaises(PermissionDenied):
            notification_context(notification, self.manager)
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")

    def test_completed_source_is_hidden_and_cancelled_without_waiting_for_repair(self):
        item, notification, delivery = self.alert()
        Task.objects.filter(pk=self.task.pk).update(status="COMPLETED", progress=100, completed_at=self.now)
        self.assertFalse(visible_notifications_for(self.manager, include_held=True).exists())
        with self.assertRaises(PermissionDenied):
            notification_context(notification, self.manager)
        self.delivery(delivery)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")

    def test_digest_cancels_an_obsolete_source_before_deferring_attention(self):
        NotificationPreference.objects.create(owner=self.manager, digest_mode="HOURLY", quiet_enabled=True,
            quiet_start=time(9), quiet_end=time(11))
        item, notification, delivery = self.alert()
        Task.objects.filter(pk=self.task.pk).update(status="COMPLETED", progress=100, completed_at=self.now)
        self.assertNotIsInstance(self.delivery(delivery), JobDeferred)
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")

    def test_digest_hour_is_canonical_across_equivalent_worker_clock_zones(self):
        NotificationPreference.objects.create(owner=self.manager, digest_mode="HOURLY")
        item, notification, delivery = self.alert()
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=1)):
            self.delivery(delivery)
        second_task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
            title="Later-arriving sample in the same digest hour")
        second, second_note, second_delivery = self.alert(second_task)
        with patch("django.utils.timezone.now", return_value=(self.now+timedelta(hours=1)).astimezone(datetime_timezone.utc)):
            self.delivery(second_delivery)
        digest = Notification.objects.get(kind="DIGEST", owner=self.manager)
        self.assertEqual(digest.summary_count, 2)
        self.assertEqual(len(notification_context(digest, self.manager)["items"]), 2)

    def test_digest_context_and_visibility_remove_reassigned_children(self):
        NotificationPreference.objects.create(owner=self.manager, digest_mode="HOURLY")
        first, first_note, first_delivery = self.alert()
        second_task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
            title="Second current digest source")
        second, second_note, second_delivery = self.alert(second_task)
        with patch("django.utils.timezone.now", return_value=self.now+timedelta(hours=1)):
            self.delivery(first_delivery)
        digest = Notification.objects.get(kind="DIGEST", owner=self.manager)
        Task.objects.filter(pk=self.task.pk).update(owner=self.peer)
        self.assertEqual(len(notification_context(digest, self.manager)["items"]), 1)
        Task.objects.filter(pk=second_task.pk).update(owner=self.peer)
        self.assertFalse(visible_notifications_for(self.manager, include_held=True).exists())
        with self.assertRaises(PermissionDenied):
            notification_context(digest, self.manager)

    def test_attention_key_cannot_be_repurposed_for_another_source_revision(self):
        item, notification, delivery = self.alert()
        with self.assertRaises(ValueError):
            create_notification(owner=self.manager, kind=notification.kind, task=self.task, request=item,
                dedupe_key=notification.dedupe_key, source_revision="another-revision")
        self.assertEqual(Notification.objects.count(), 1)

    def test_notification_reconciliation_rotates_a_bounded_page(self):
        self.alert()
        second_task = Task.objects.create(owner=self.owner, company=self.company, team=self.team,
            title="Second delivery reconciliation sample")
        self.alert(second_task)
        reconcile_notifications(now=self.now, limit=1)
        reconcile_notifications(now=self.now, limit=1)
        self.assertEqual(NotificationDelivery.objects.filter(last_checked_at=self.now).count(), 2)
