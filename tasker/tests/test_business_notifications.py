"""Targeted collections attention shares Tasker's governed in-app outbox."""
from datetime import date, datetime, time, timezone as datetime_timezone
from decimal import Decimal
import json

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings

from accounting.models import Company
from credit_control.models import CollectionTask, PromiseToPay
from customers.models import Customer
from tasker.job_models import OutboxJob
from tasker.models import Task, Team, TeamMembership
from tasker.notification_models import Notification, NotificationDelivery, NotificationPreference
from tasker.services import business_links, notifications


DAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 7, 10, tzinfo=datetime_timezone.utc)


def employee(name):
    user = get_user_model().objects.create_user(name)
    user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"),
        *Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("view_collectiontask", "view_promisetopay")))
    return user


@override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
class BusinessNotificationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner, cls.other, cls.manager = [employee(name) for name in
            ("business-attention-owner", "business-attention-other", "business-attention-manager")]
        cls.company = Company.objects.create(name="Business attention company")
        cls.team = Team.objects.create(name="Business attention team", company=cls.company)
        for user in (cls.owner, cls.other, cls.manager):
            TeamMembership.objects.create(team=cls.team, user=user, role="MANAGER" if user == cls.manager else "EMPLOYEE")
            NotificationPreference.objects.create(owner=user, respect_work_hours=False, suppress_during_focus=False)
        cls.customer = Customer.objects.create(name="PRIVATE_NOTIFICATION_CUSTOMER",
            email="private-notification@example.invalid", phone="PRIVATE_NOTIFICATION_PHONE")

    def setUp(self):
        self.source = self.collection()

    def collection(self, **changes):
        values = {"company": self.company, "customer": self.customer, "task_type": "FOLLOW_UP_DEBTOR",
            "assigned_to": self.owner, "created_by": self.manager, "due_date": DAY,
            "notes": "PRIVATE_NOTIFICATION_NOTES"}
        values.update(changes)
        return CollectionTask.objects.create(**values)

    def promise(self, **changes):
        values = {"company": self.company, "customer": self.customer, "assigned_to": self.owner,
            "created_by": self.manager, "promised_amount": Decimal("987654.32"), "promised_date": DAY,
            "notes": "PRIVATE_NOTIFICATION_PROMISE"}
        values.update(changes)
        return PromiseToPay.objects.create(**values)

    def sync(self, source=None):
        source = source or self.source
        return business_links.reconcile_business_source(
            source_type="promise_to_pay" if isinstance(source, PromiseToPay) else "collection_task", source_id=source.pk)

    def attention(self, link):
        return Notification.objects.get(business_link=link, cancelled_at__isnull=True)

    def deliver(self, notification):
        delivery = NotificationDelivery.objects.get(notification=notification)
        result = notifications.deliver_notification({"delivery_id": delivery.pk})
        delivery.refresh_from_db()
        notification.refresh_from_db()
        return result, delivery

    def assert_hidden(self, notification, user=None):
        user = user or self.owner
        self.assertFalse(notifications.visible_notifications_for(user, include_held=True).filter(pk=notification.pk).exists())
        with self.assertRaises(PermissionDenied):
            notifications.notification_context(notification, user)

    def test_repeated_sync_and_event_replay_queue_one_targeted_in_app_delivery(self):
        link = self.sync()
        first = self.attention(link)
        self.sync()
        replay = notifications.queue_business_link_update(link, link.events.get())
        self.assertEqual(first.pk, replay.pk)
        self.assertEqual(Notification.objects.filter(business_link=link).count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(notification=first).count(), 1)
        delivery = first.deliveries.get()
        self.assertEqual((first.owner_id, first.kind, first.source_revision), (self.owner.pk, "BUSINESS_ACTION", str(link.revision)))
        self.assertEqual((delivery.recipient_id, delivery.channel), (self.owner.pk, "IN_APP"))
        job = OutboxJob.objects.get(dedupe_key=f"delivery:{delivery.pk}")
        self.assertEqual(job.payload, {"delivery_id": delivery.pk})
        self.assertFalse(NotificationDelivery.objects.filter(notification=first, channel="EMAIL").exists())

    def test_manager_and_other_company_member_are_not_notification_recipients(self):
        link = self.sync()
        notification = self.attention(link)
        self.assertEqual(set(Notification.objects.filter(business_link=link).values_list("owner_id", flat=True)), {self.owner.pk})
        for user in (self.manager, self.other):
            with self.subTest(user=user.pk):
                self.assert_hidden(notification, user)
        result, delivery = self.deliver(notification)
        self.assertEqual(result, {"delivered": 1})
        self.assertEqual(delivery.state, "DELIVERED")
        self.assertTrue(notifications.visible_notifications_for(self.owner).filter(pk=notification.pk).exists())

    def test_no_creator_fallback_or_notification_for_unassigned_or_inactive_owner(self):
        unassigned = self.sync(self.collection(assigned_to=None))
        promise = self.sync(self.promise(assigned_to=None))
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        inactive = self.sync()
        for link in (unassigned, promise, inactive):
            self.assertFalse(Notification.objects.filter(business_link=link).exists())

    def test_tasker_only_employee_with_assignment_does_not_receive_source_notification(self):
        self.owner.user_permissions.remove(*Permission.objects.filter(content_type__app_label="credit_control"))
        link = self.sync()
        self.assertFalse(Notification.objects.filter(business_link=link).exists())

    def test_missing_current_company_scope_blocks_notification_generation(self):
        TeamMembership.objects.filter(team=self.team, user=self.owner).update(is_active=False)
        link = self.sync()
        self.assertFalse(Notification.objects.filter(business_link=link).exists())

    def test_reassignment_hides_old_notice_before_reconcile_and_replaces_only_owner(self):
        link = self.sync()
        old = self.attention(link)
        self.source.assigned_to = self.other
        self.source.save(update_fields={"assigned_to"})
        self.assert_hidden(old)
        self.sync()
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")
        replacement = self.attention(link)
        self.assertEqual(replacement.owner_id, self.other.pk)
        self.assertNotEqual(replacement.pk, old.pk)
        self.assertEqual(Notification.objects.filter(business_link=link, cancelled_at__isnull=True).count(), 1)

    def test_done_and_cancelled_source_close_delivery_without_asserting_payment_outcome(self):
        link = self.sync()
        old = self.attention(link)
        self.source.status = "DONE"
        self.source.save(update_fields={"status"})
        self.sync()
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")
        self.assertFalse(Notification.objects.filter(business_link=link, cancelled_at__isnull=True).exists())
        self.source.status = "CANCELLED"
        self.source.save(update_fields={"status"})
        self.sync()
        self.assertEqual(Notification.objects.filter(business_link=link).count(), 1)

    def test_bulk_status_change_is_removed_from_sql_counts_and_cancelled_at_dispatch(self):
        link = self.sync()
        old = self.attention(link)
        CollectionTask.objects.filter(pk=self.source.pk).update(status="IN_PROGRESS")
        self.assert_hidden(old)
        result, delivery = self.deliver(old)
        self.assertEqual((result, delivery.state), ({"cancelled": True}, "CANCELLED"))
        self.sync()
        replacement = self.attention(link)
        self.assertNotEqual(replacement.pk, old.pk)
        self.assertEqual(notifications.notification_context(replacement, self.owner)["title"], "Debtor follow-up")

    def test_revoked_permission_hides_stored_notice_and_repair_cancels_delivery(self):
        link = self.sync()
        old = self.attention(link)
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="credit_control", codename="view_collectiontask"))
        self.assert_hidden(old)
        self.assertEqual(notifications.reconcile_notifications(limit=10)["cancelled"], 1)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")

    def test_revoked_membership_hides_notice_and_dispatch_cancels_it(self):
        link = self.sync()
        old = self.attention(link)
        TeamMembership.objects.filter(team=self.team, user=self.owner).update(is_active=False)
        self.assert_hidden(old)
        result, delivery = self.deliver(old)
        self.assertEqual((result, delivery.state), ({"cancelled": True}, "CANCELLED"))

    def test_source_deletion_hides_notice_then_tombstone_cancels_only_linked_delivery(self):
        link = self.sync()
        old = self.attention(link)
        pk = self.source.pk
        self.source.delete()
        self.assert_hidden(old)
        business_links.reconcile_business_source(source_type="collection_task", source_id=pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")
        self.assertFalse(Notification.objects.filter(business_link=link, cancelled_at__isnull=True).exists())

    def test_new_canonical_check_suppresses_existing_standalone_promise_notice(self):
        promise = self.promise()
        standalone = self.sync(promise)
        old = self.attention(standalone)
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        self.assert_hidden(old)
        self.sync(promise)
        check_link = self.sync(check)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")
        self.assertEqual(self.attention(check_link).owner_id, self.owner.pk)
        self.assertEqual(Notification.objects.filter(cancelled_at__isnull=True, business_link__isnull=False).count(), 1)

    def test_deleting_promise_removes_check_action_notice_without_revision_bump(self):
        promise = self.promise()
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        link = self.sync(check)
        old = self.attention(link)
        previous_version = link.source_version
        promise.delete()
        check.refresh_from_db()
        self.assertEqual(str(check.revision), previous_version)
        self.assertIsNone(check.promise_to_pay_id)
        self.assert_hidden(old)
        self.sync(check)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")

    def test_flag_rollback_hides_and_cancels_business_attention_only(self):
        link = self.sync()
        old = self.attention(link)
        unrelated = notifications.create_notification(owner=self.owner, kind="WORK_ALLOCATION_CHANGED", dedupe_key="unrelated-attention")
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            self.assert_hidden(old)
            self.assertTrue(notifications.visible_notifications_for(self.owner, include_held=True).filter(pk=unrelated.pk).exists())
            business_links.reconcile_business_links(limit=10)
        old.refresh_from_db()
        unrelated.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)
        self.assertEqual(old.deliveries.get().state, "CANCELLED")
        self.assertIsNone(unrelated.cancelled_at)
        self.assertEqual(unrelated.deliveries.get().state, "QUEUED")

    def test_reenable_produces_new_receipt_and_notice_instead_of_reusing_cancelled_dedupe(self):
        link = self.sync()
        old = self.attention(link)
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            business_links.reconcile_business_links(limit=10)
        self.sync()
        replacement = self.attention(link)
        self.assertNotEqual(replacement.pk, old.pk)
        self.assertNotEqual(replacement.dedupe_key, old.dedupe_key)
        self.assertEqual(replacement.deliveries.get().state, "QUEUED")

    def test_source_context_and_delivery_jobs_never_copy_financial_or_contact_bodies(self):
        promise = self.promise()
        link = self.sync(promise)
        notification = self.attention(link)
        encoded = json.dumps({"context": notifications.notification_context(notification, self.owner),
            "notices": list(Notification.objects.filter(business_link=link).values()),
            "deliveries": list(NotificationDelivery.objects.filter(notification=notification).values()),
            "jobs": list(OutboxJob.objects.filter(kind="notification.deliver").values("payload"))}, default=str)
        for forbidden in ("PRIVATE_NOTIFICATION_CUSTOMER", "private-notification@example.invalid",
                "PRIVATE_NOTIFICATION_PHONE", "PRIVATE_NOTIFICATION_NOTES", "PRIVATE_NOTIFICATION_PROMISE", "987654.32"):
            self.assertNotIn(forbidden, encoded)
        context = notifications.notification_context(notification, self.owner)
        self.assertEqual(context["title"], "Review payment promise")
        self.assertIn(str(link.pk), context["url"])

    def test_business_attention_obeys_quiet_hours_and_keeps_pending_delivery(self):
        from unittest.mock import patch
        NotificationPreference.objects.filter(owner=self.owner).update(quiet_enabled=True,
            quiet_start=time(11), quiet_end=time(13), timezone_name="Africa/Harare")
        link = self.sync()
        notification = self.attention(link)
        with patch("django.utils.timezone.now", return_value=NOW):
            result, delivery = self.deliver(notification)
        self.assertEqual(result.reason, "quiet_hours")
        self.assertEqual(delivery.state, "HELD")
        self.assertIsNone(notification.published_at)
        self.assertEqual(delivery.next_attempt_at, NOW.replace(hour=11))

    def test_business_action_cannot_attach_an_unrelated_task_or_an_external_channel(self):
        link = self.sync()
        task = Task.objects.create(owner=self.owner, title="Independent task")
        with self.assertRaises(ValidationError):
            notifications.create_notification(owner=self.owner, kind="BUSINESS_ACTION", dedupe_key="invalid-mixed-business",
                business_link=link, source_revision=str(link.revision), task=task)
        with self.assertRaises(ValidationError):
            notifications.create_notification(owner=self.owner, kind="BUSINESS_ACTION", dedupe_key="invalid-missing-business")
        with self.assertRaises(ValidationError):
            notifications.enqueue_delivery(recipient=self.owner, channel="EMAIL", dedupe_key="invalid-business-email",
                notification=self.attention(link))

    def test_dedupe_key_cannot_be_reused_with_another_business_source(self):
        link = self.sync()
        first = self.attention(link)
        other = self.sync(self.collection())
        with self.assertRaises(ValueError):
            notifications.create_notification(owner=self.owner, kind="BUSINESS_ACTION", dedupe_key=first.dedupe_key,
                business_link=other, source_revision=str(other.revision))
        first.refresh_from_db()
        self.assertEqual(first.business_link_id, link.pk)
        self.assertEqual(first.source_revision, str(link.revision))

    def test_stale_transition_replay_cannot_cancel_a_newer_owner_notice(self):
        old_link = self.sync()
        old_event = old_link.events.get()
        self.source.assigned_to = self.other
        self.source.save(update_fields={"assigned_to"})
        current = self.sync()
        latest = self.attention(current)
        notifications.queue_business_link_update(old_link, old_event)
        latest.refresh_from_db()
        self.assertIsNone(latest.cancelled_at)
        self.assertEqual(latest.deliveries.get().state, "QUEUED")
