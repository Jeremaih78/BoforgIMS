"""Canonical collections references, live scope, replay repair and lease fencing."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import json
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import Company
from credit_control.models import CollectionTask, PromiseToPay
from customers.models import Customer
from tasker.business_adapters import collections as adapter
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink, BusinessSyncCursor
from tasker.goal_models import OutcomeMeasurement
from tasker.job_models import OutboxJob
from tasker.models import OrganizationAccess, Task, Team, TeamMembership
from tasker.services import business_links
from tasker.services.jobs import LostJobLease, run_worker_once


DAY = date(2030, 1, 7)


def employee(name, *, source=True, tasker=True):
    user = get_user_model().objects.create_user(name)
    if tasker:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
    if source:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("view_collectiontask", "view_promisetopay")))
    return user


@override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
class BusinessLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = employee("business-owner")
        cls.other = employee("business-other")
        cls.viewer = employee("business-viewer")
        cls.tasker_only = employee("business-tasker-only", source=False)
        cls.source_only = employee("business-source-only", tasker=False)
        cls.company = Company.objects.create(name="Business source company")
        cls.foreign_company = Company.objects.create(name="Foreign business company")
        cls.team = Team.objects.create(name="Source follow-ups", company=cls.company)
        for user in (cls.owner, cls.other, cls.viewer, cls.tasker_only, cls.source_only):
            TeamMembership.objects.create(team=cls.team, user=user)
        cls.customer = Customer.objects.create(name="PRIVATE_CUSTOMER_SENTINEL",
            email="private-customer@example.invalid", phone="PRIVATE_PHONE_SENTINEL")

    def setUp(self):
        # Root's notification integration has separate end-to-end tests; here
        # this spy also proves a reconciliation no-op creates no extra event.
        self.notify = patch("tasker.services.notifications.queue_business_link_update")
        self.queue_notification = self.notify.start()
        self.addCleanup(self.notify.stop)
        self.source = self.collection()

    def collection(self, **changes):
        values = {"company": self.company, "task_type": "FOLLOW_UP_DEBTOR", "customer": self.customer,
            "assigned_to": self.owner, "created_by": self.viewer, "due_date": DAY,
            "notes": "PRIVATE_NOTES_SENTINEL"}
        values.update(changes)
        return CollectionTask.objects.create(**values)

    def promise(self, **changes):
        values = {"company": self.company, "customer": self.customer, "assigned_to": self.owner,
            "created_by": self.viewer, "promised_date": DAY, "promised_amount": Decimal("876543.21"),
            "notes": "PRIVATE_PROMISE_SENTINEL"}
        values.update(changes)
        return PromiseToPay.objects.create(**values)

    def sync(self, source=None):
        source = source or self.source
        return business_links.reconcile_business_source(
            source_type="promise_to_pay" if isinstance(source, PromiseToPay) else "collection_task", source_id=source.pk)

    def ids(self, user=None, **filters):
        return set(business_links.visible_business_links_for(user or self.owner, **filters).values_list("pk", flat=True))

    def test_replays_create_one_reference_and_receipt_without_duplicate_tasks_or_goal_claims(self):
        tasks, outcomes = Task.objects.count(), OutcomeMeasurement.objects.count()
        first = self.sync()
        second = self.sync()
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(first.revision, second.revision)
        self.assertEqual(BusinessRecordLink.objects.count(), 1)
        self.assertEqual(BusinessLinkEvent.objects.count(), 1)
        self.assertEqual(self.queue_notification.call_count, 1)
        self.assertEqual(Task.objects.count(), tasks)
        self.assertEqual(OutcomeMeasurement.objects.count(), outcomes)
        self.source.refresh_from_db()
        self.assertEqual(self.source.status, "PENDING")

    def test_copied_metadata_contains_no_amount_contact_customer_name_or_notes(self):
        self.sync()
        self.sync(self.promise())
        business_links.enqueue_business_source("collection_task", self.source.pk)
        encoded = json.dumps({"links": list(BusinessRecordLink.objects.values()),
            "events": list(BusinessLinkEvent.objects.values()),
            "jobs": list(OutboxJob.objects.filter(kind="business.reconcile").values("payload"))}, default=str)
        for forbidden in ("PRIVATE_CUSTOMER_SENTINEL", "private-customer@example.invalid", "PRIVATE_PHONE_SENTINEL",
                "PRIVATE_NOTES_SENTINEL", "PRIVATE_PROMISE_SENTINEL", "876543.21"):
            self.assertNotIn(forbidden, encoded)

    def test_creator_is_never_responsible_owner_and_unassigned_work_stays_unassigned(self):
        task = self.collection(assigned_to=None, created_by=self.owner)
        promise = self.promise(assigned_to=None, created_by=self.owner)
        for source in (task, promise):
            with self.subTest(kind=type(source).__name__):
                link = self.sync(source)
                self.assertIsNone(link.owner_id)
                self.assertEqual(link.status, "UNASSIGNED")
                self.assertEqual(link.reason_code, "responsible_owner_needed")
        self.assertEqual(business_links.business_actions_for(self.owner)["total"], 0)

    def test_inactive_responsible_employee_needs_assignment(self):
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        link = self.sync()
        self.assertEqual(link.status, "UNASSIGNED")
        self.assertEqual(link.owner_id, self.owner.pk)
        self.assertFalse(business_links.business_notification_available(
            user=self.owner, link=link, source_revision=link.revision))

    def test_collection_status_is_only_observed_from_source(self):
        link = self.sync()
        for source_status, expected in (("IN_PROGRESS", "IN_PROGRESS"), ("DONE", "COMPLETED"),
                ("PENDING", "OPEN"), ("CANCELLED", "CANCELLED")):
            with self.subTest(status=source_status):
                self.source.status = source_status
                self.source.save(update_fields={"status"})
                changed = self.sync()
                self.assertEqual(changed.pk, link.pk)
                self.assertEqual(changed.status, expected)
                self.assertEqual(changed.source_status, source_status)
        self.assertEqual(BusinessLinkEvent.objects.filter(link=link).count(), 5)
        self.assertFalse(OutcomeMeasurement.objects.exists())

    def test_promise_status_does_not_turn_follow_up_into_payment_posting(self):
        promise = self.promise()
        for source_status, expected in (("OPEN", "OPEN"), ("BROKEN", "OPEN"), ("PARTIALLY_KEPT", "OPEN"),
                ("KEPT", "COMPLETED"), ("CANCELLED", "CANCELLED")):
            with self.subTest(status=source_status):
                promise.status = source_status
                promise.save(update_fields={"status"})
                link = self.sync(promise)
                self.assertEqual(link.status, expected)
                self.assertEqual(link.priority, "HIGH" if source_status == "BROKEN" else "MEDIUM")
        promise.refresh_from_db()
        self.assertEqual(promise.actual_paid_amount, Decimal("0.00"))
        self.assertIsNone(promise.actual_paid_date)
        self.assertFalse(OutcomeMeasurement.objects.exists())

    def test_canonical_promise_check_covers_standalone_review_even_when_done(self):
        promise = self.promise()
        promise_link = self.sync(promise)
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        check_link = self.sync(check)
        covered = self.sync(promise)
        self.assertEqual(covered.pk, promise_link.pk)
        self.assertEqual((covered.status, covered.reason_code), ("CANCELLED", "covered_by_collection_task"))
        self.assertIn(check_link.pk, self.ids(state="active"))
        self.assertNotIn(promise_link.pk, self.ids(state="active"))
        check.status = "DONE"
        check.save(update_fields={"status"})
        self.sync(check)
        self.assertEqual(self.sync(promise).reason_code, "covered_by_collection_task")
        self.assertNotIn(promise_link.pk, self.ids(state="active"))
        promise.refresh_from_db()
        self.assertEqual(promise.status, "OPEN")
        self.assertEqual(promise.actual_paid_amount, Decimal("0.00"))

    def test_legacy_ambiguous_check_is_unavailable_and_does_not_infer_promise_identity(self):
        promise = self.promise()
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=None, due_date=promise.promised_date)
        link = self.sync(check)
        self.assertEqual((link.status, link.reason_code), ("UNAVAILABLE", "promise_reference_missing"))
        self.assertNotIn(link.pk, self.ids(state="active"))
        self.assertEqual(self.sync(promise).status, "OPEN")

    def test_deleting_check_reopens_standalone_review_and_deleted_promise_invalidates_check(self):
        promise = self.promise()
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        self.sync(check)
        covered = self.sync(promise)
        check_pk = check.pk
        check.delete()
        self.assertEqual(self.sync(promise).status, "OPEN")
        tombstone = business_links.reconcile_business_source(source_type="collection_task", source_id=check_pk)
        self.assertEqual((tombstone.status, tombstone.reason_code), ("UNAVAILABLE", "source_missing"))
        check = self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        check_link = self.sync(check)
        promise.delete()
        check.refresh_from_db()
        self.assertIsNone(check.promise_to_pay_id)
        self.assertEqual(self.sync(check).reason_code, "promise_reference_missing")
        self.assertNotIn(check_link.pk, self.ids(state="active"))
        self.assertEqual(covered.source_type, "promise_to_pay")

    def test_tasker_capability_does_not_grant_credit_control_access(self):
        link = self.sync()
        self.assertEqual(self.ids(self.tasker_only), set())
        with self.assertRaises(PermissionDenied):
            business_links.get_business_link(user=self.tasker_only, pk=link.pk)
        with self.assertRaises(PermissionDenied):
            business_links.business_source_url(user=self.tasker_only, link=link)
        with self.assertRaises(PermissionDenied):
            self.ids(self.source_only)

    def test_live_membership_revocation_denies_owner_history_and_source_route(self):
        link = self.sync()
        self.assertIn(link.pk, self.ids())
        TeamMembership.objects.filter(team=self.team, user=self.owner).update(is_active=False)
        self.assertEqual(self.ids(), set())
        with self.assertRaises(PermissionDenied):
            business_links.present_business_link(user=self.owner, link=link)
        with self.assertRaises(PermissionDenied):
            business_links.business_source_url(user=self.owner, link=link)

    def test_soft_deleted_membership_and_grant_do_not_keep_source_scope(self):
        link = self.sync()
        TeamMembership.objects.filter(team=self.team, user=self.owner).update(is_deleted=True)
        self.assertEqual(self.ids(), set())
        grant = OrganizationAccess.objects.create(user=self.owner, company=self.company, can_view_all=True)
        self.assertIn(link.pk, self.ids())
        OrganizationAccess.objects.filter(pk=grant.pk).update(is_deleted=True)
        self.assertEqual(self.ids(), set())

    def test_source_permission_revocation_is_checked_without_stale_user_permission_cache(self):
        link = self.sync()
        self.assertIn(link.pk, self.ids())
        self.owner.user_permissions.remove(Permission.objects.get(content_type__app_label="credit_control", codename="view_collectiontask"))
        self.assertEqual(self.ids(), set())
        self.assertFalse(business_links.business_notification_available(user=self.owner, link=link, source_revision=link.revision))

    def test_legacy_company_null_record_is_assigned_owner_only(self):
        link = self.sync(self.collection(company=None))
        self.assertIn(link.pk, self.ids())
        self.assertNotIn(link.pk, self.ids(self.viewer))
        self.assertNotIn(link.pk, self.ids(self.other))
        unassigned = self.sync(self.collection(company=None, assigned_to=None, created_by=self.owner))
        self.assertNotIn(unassigned.pk, self.ids())

    def test_foreign_company_and_inactive_company_are_hidden(self):
        foreign = self.sync(self.collection(company=self.foreign_company))
        self.assertNotIn(foreign.pk, self.ids())
        link = self.sync()
        Company.objects.filter(pk=self.company.pk).update(is_active=False)
        self.assertEqual(self.ids(), set())
        self.assertEqual((self.sync().status, self.sync().reason_code), ("UNAVAILABLE", "company_inactive"))
        with self.assertRaises(PermissionDenied):
            business_links.get_business_link(user=self.owner, pk=link.pk)

    def test_presentation_reads_live_owner_state_deadline_and_priority_without_writing(self):
        link = self.sync()
        revision, fingerprint, receipt_count = link.revision, link.source_digest, link.events.count()
        CollectionTask.objects.filter(pk=self.source.pk).update(assigned_to=self.other, status="DONE",
            due_date=DAY+timedelta(days=8), priority="URGENT")
        current = business_links.get_business_link(user=self.viewer, pk=link.pk)
        self.assertEqual((current.owner_id, current.status, current.due_date, current.priority),
            (self.other.pk, "COMPLETED", DAY+timedelta(days=8), "CRITICAL"))
        self.assertTrue(current.source_stale)
        self.assertEqual(current.owner.pk, self.other.pk)
        link.refresh_from_db()
        self.assertEqual((link.owner_id, link.status, link.due_date, link.revision, link.source_digest),
            (self.owner.pk, "OPEN", DAY, revision, fingerprint))
        self.assertEqual(link.events.count(), receipt_count)

    def test_mine_active_closed_filters_use_canonical_state_before_pagination(self):
        link = self.sync()
        other = self.sync(self.collection(assigned_to=self.other, status="DONE"))
        CollectionTask.objects.filter(pk=self.source.pk).update(assigned_to=self.other, status="DONE")
        CollectionTask.objects.filter(pk=other.source_record_id).update(assigned_to=self.owner, status="PENDING")
        self.assertEqual(self.ids(mine=True, state="active"), {other.pk})
        self.assertEqual(self.ids(mine=True, state="closed"), set())
        self.assertEqual(self.ids(self.other, mine=True, state="closed"), {link.pk})
        panel = business_links.business_actions_for(self.owner, limit=1)
        self.assertEqual((panel["total"], panel["omitted"]), (1, 0))
        self.assertEqual(panel["items"][0].pk, other.pk)

    def test_current_source_priority_and_deadline_sort_before_limit(self):
        low = self.sync(self.collection(priority="LOW", due_date=DAY-timedelta(days=5)))
        urgent = self.sync(self.collection(priority="LOW", due_date=DAY+timedelta(days=5)))
        CollectionTask.objects.filter(pk=urgent.source_record_id).update(priority="URGENT")
        rows = business_links.visible_business_links_for(self.owner, state="active", mine=True)
        self.assertEqual(list(rows.values_list("pk", flat=True)[:1]), [urgent.pk])
        self.assertNotEqual(low.pk, urgent.pk)

    def test_source_url_is_canonical_and_cannot_become_a_user_supplied_redirect(self):
        source = self.collection(notes="https://malicious.example.invalid/")
        link = self.sync(source)
        expected = host_reverse("ims:credit_control:task_detail", kwargs={"pk": source.pk}, host="ims")
        self.assertEqual(business_links.business_source_url(user=self.owner, link=link), expected)
        self.assertNotIn("malicious", business_links.get_business_link(user=self.owner, pk=link.pk).source_url)

    def test_notification_requires_current_owner_revision_and_generic_facts(self):
        link = self.sync()
        available = lambda user=self.owner, revision=link.revision: business_links.business_notification_available(
            user=user, link=link, source_revision=revision)
        self.assertTrue(available())
        self.assertFalse(available(self.other))
        self.assertFalse(available(revision="outdated"))
        CollectionTask.objects.filter(pk=self.source.pk).update(status="DONE")
        self.assertFalse(available())
        CollectionTask.objects.filter(pk=self.source.pk).update(status="PENDING", due_date=DAY+timedelta(days=1))
        self.assertFalse(available())  # Legacy bulk updates did not bump revision.

    def test_notification_sql_excludes_every_stale_generic_source_field_before_pagination(self):
        link = self.sync()
        rows = lambda: business_links.business_notification_links_for(self.owner)
        self.assertTrue(rows().filter(pk=link.pk).exists())
        changes = ({"status": "IN_PROGRESS"}, {"assigned_to_id": self.other.pk},
            {"company_id": self.foreign_company.pk}, {"due_date": DAY+timedelta(days=1)},
            {"priority": "URGENT"}, {"task_type": "SEND_REMINDER"},
            {"updated_at": self.source.updated_at+timedelta(seconds=1)})
        original = {field: getattr(self.source, field) for item in changes for field in item}
        for values in changes:
            with self.subTest(values=tuple(values)):
                CollectionTask.objects.filter(pk=self.source.pk).update(**values)
                self.assertFalse(rows().filter(pk=link.pk).exists())
                CollectionTask.objects.filter(pk=self.source.pk).update(**{field: original[field] for field in values})
                self.assertTrue(rows().filter(pk=link.pk).exists())

    def test_promise_notification_sql_hides_new_canonical_coverage_without_promise_save(self):
        promise = self.promise()
        link = self.sync(promise)
        self.assertTrue(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())
        self.collection(task_type="CHECK_PROMISE", promise_to_pay=promise)
        self.assertFalse(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())

    def test_deleted_source_is_hidden_then_reconciled_as_unavailable_once(self):
        link = self.sync()
        pk = self.source.pk
        self.source.delete()
        self.assertEqual(self.ids(), set())
        with self.assertRaises(PermissionDenied):
            business_links.get_business_link(user=self.owner, pk=link.pk)
        first = business_links.reconcile_business_source(source_type="collection_task", source_id=pk)
        second = business_links.reconcile_business_source(source_type="collection_task", source_id=pk)
        self.assertEqual((first.status, first.reason_code), ("UNAVAILABLE", "source_missing"))
        self.assertEqual(first.revision, second.revision)
        self.assertIsNone(first.owner_id)
        self.assertEqual(first.events.count(), 2)

    def test_missing_unknown_source_does_not_create_tombstone_or_job(self):
        self.assertIsNone(business_links.reconcile_business_source(source_type="collection_task", source_id=2147483647))
        self.assertIsNone(business_links.enqueue_business_source("collection_task", 2147483647))
        self.assertFalse(BusinessRecordLink.objects.exists())

    def test_job_deduplication_uses_source_and_projection_generation_only(self):
        job = business_links.enqueue_business_source("collection_task", self.source.pk)
        replay = business_links.enqueue_business_source("collection_task", self.source.pk)
        self.assertEqual(job.pk, replay.pk)
        self.assertEqual(job.payload, {"source_type": "collection_task", "source_id": self.source.pk})
        self.assertIsNone(job.owner_id)
        self.sync()
        self.assertIsNone(business_links.enqueue_business_source("collection_task", self.source.pk))

    def test_disabled_flag_blocks_generation_and_hides_existing_references(self):
        link = self.sync()
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            self.assertFalse(business_links.visible_business_links_for(self.owner).exists())
            self.assertFalse(business_links.business_notification_links_for(self.owner).exists())
            self.assertIsNone(business_links.enqueue_business_source("collection_task", self.source.pk))
            other = self.collection()
            self.assertIsNone(business_links.reconcile_business_source(source_type="collection_task", source_id=other.pk))
            self.assertEqual(business_links.reconcile_business_links(limit=1)["disabled"], 1)
            link.refresh_from_db()
            self.assertEqual((link.status, link.reason_code), ("UNAVAILABLE", "adapter_disabled"))
        self.assertEqual(BusinessRecordLink.objects.count(), 1)

    def test_rollback_and_reenable_do_not_reuse_terminal_job_or_cancelled_event(self):
        job = business_links.enqueue_business_source("collection_task", self.source.pk)
        link = self.sync()
        original_event = link.events.get()
        OutboxJob.objects.filter(pk=job.pk).update(state="SUCCEEDED")
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            business_links.reconcile_business_links(limit=10)
        replacement = business_links.enqueue_business_source("collection_task", self.source.pk)
        self.assertNotEqual(replacement.pk, job.pk)
        reopened = self.sync()
        self.assertEqual(reopened.status, "OPEN")
        self.assertEqual(reopened.events.count(), 3)
        self.assertNotEqual(reopened.events.first().pk, original_event.pk)

    def test_job_completed_while_disabled_before_first_projection_gets_fresh_successor(self):
        job = business_links.enqueue_business_source("collection_task", self.source.pk)
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
        self.assertFalse(BusinessRecordLink.objects.exists())
        job.refresh_from_db()
        replacement = business_links.enqueue_business_source("collection_task", self.source.pk)
        replay = business_links.enqueue_business_source("collection_task", self.source.pk)
        self.assertEqual(job.state, "SUCCEEDED")
        self.assertNotEqual(replacement.pk, job.pk)
        self.assertEqual(replacement.pk, replay.pk)
        self.assertEqual(replacement.dedupe_key, job.dedupe_key+":repair:"+str(job.pk))
        self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
        self.assertEqual(BusinessRecordLink.objects.count(), 1)
        job.refresh_from_db()
        self.assertEqual((job.state, job.attempts), ("SUCCEEDED", 1))

    def test_repeated_bulk_cycles_repair_without_reusing_successful_noop_jobs(self):
        business_links.enqueue_business_source("collection_task", self.source.pk)
        self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
        link = self.sync()
        initial_revision = link.revision
        previous = None
        for _ in range(3):
            CollectionTask.objects.filter(pk=self.source.pk).update(status="IN_PROGRESS")
            pending = business_links.enqueue_business_source("collection_task", self.source.pk)
            if previous is not None:
                self.assertNotEqual(pending.pk, previous.pk)
                self.assertTrue(pending.dedupe_key.endswith(":repair:"+str(previous.pk)))
                # No ordering by a wall clock is needed to identify the chain's
                # leaf, even when multiple completed repairs share timestamps.
                OutboxJob.objects.filter(pk__in=(pending.pk, previous.pk)).update(created_at=previous.created_at)
            self.assertEqual(business_links.enqueue_business_source("collection_task", self.source.pk).pk, pending.pk)
            CollectionTask.objects.filter(pk=self.source.pk).update(status="PENDING")
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
            previous = pending
        link.refresh_from_db()
        self.assertEqual(link.revision, initial_revision)
        CollectionTask.objects.filter(pk=self.source.pk).update(status="IN_PROGRESS")
        pending = business_links.enqueue_business_source("collection_task", self.source.pk)
        self.assertNotEqual(pending.pk, previous.pk)
        self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
        link.refresh_from_db()
        self.assertEqual(link.status, "IN_PROGRESS")
        self.assertEqual(link.events.count(), 2)

    def test_repair_does_not_reset_failed_attempt_budget_or_intentional_cancellation(self):
        job = business_links.enqueue_business_source("collection_task", self.source.pk)
        for state in ("FAILED", "CANCELLED"):
            with self.subTest(state=state):
                OutboxJob.objects.filter(pk=job.pk).update(state=state, attempts=job.max_attempts)
                current = business_links.enqueue_business_source("collection_task", self.source.pk)
                self.assertEqual((current.pk, current.state, current.attempts), (job.pk, state, job.max_attempts))
                self.assertEqual(OutboxJob.objects.filter(kind="business.reconcile").count(), 1)

    def test_missed_bulk_change_is_repaired_even_without_source_revision_bump(self):
        link = self.sync()
        previous_version = link.source_version
        CollectionTask.objects.filter(pk=self.source.pk).update(status="IN_PROGRESS", assigned_to=self.other)
        business_links.reconcile_business_links(limit=6)
        job = OutboxJob.objects.filter(kind="business.reconcile", payload__source_id=self.source.pk,
            payload__source_type="collection_task").order_by("-created_at").first()
        self.assertIsNotNone(job)
        self.sync()
        link.refresh_from_db()
        self.assertEqual((link.status, link.owner_id, link.source_version), ("IN_PROGRESS", self.other.pk, previous_version))

    def test_bounded_round_robin_event_repair_does_not_starve_later_sources_or_deletions(self):
        for _ in range(8):
            self.collection()
        promises = [self.promise() for _ in range(3)]
        deleted = self.sync()
        pk = self.source.pk
        self.source.delete()
        for _ in range(35):
            result = business_links.reconcile_business_links(limit=2)
            self.assertLessEqual(result["scanned"], 2)
        refs = set(OutboxJob.objects.filter(kind="business.reconcile").values_list("payload__source_type", "payload__source_id"))
        expected = {("collection_task", value) for value in CollectionTask.objects.values_list("pk", flat=True)}
        expected |= {("promise_to_pay", promise.pk) for promise in promises}
        expected.add(("collection_task", pk))
        self.assertTrue(expected.issubset(refs))
        self.assertTrue(BusinessSyncCursor.objects.filter(key="collections").exists())
        self.assertEqual(deleted.source_record_id, pk)

    def test_reconciliation_receipts_are_immutable_and_identity_cannot_be_retargeted(self):
        link = self.sync()
        event = link.events.get()
        event.status = "COMPLETED"
        with self.assertRaises(ValidationError):
            event.save()
        with self.assertRaises(ValidationError):
            event.delete()
        link.source_record_id += 1
        with self.assertRaises(ValidationError):
            link.save()

    def test_invalid_sources_payloads_and_unbounded_limits_are_rejected(self):
        for kind, pk in (("sales_invoice", 1), ("collection_task", True), ("collection_task", 0),
                ("collection_task", "1"), ("collection_task", 2**64)):
            with self.subTest(kind=kind, pk=pk), self.assertRaises(ValidationError):
                business_links.enqueue_business_source(kind, pk)
        for limit in (0, 1001, True):
            with self.subTest(limit=limit), self.assertRaises(ValidationError):
                business_links.reconcile_business_links(limit=limit)
        for payload in ({"source_type": "collection_task", "source_id": self.source.pk, "notes": "private"}, {}, []):
            with self.subTest(payload=payload), self.assertRaises(ValidationError):
                business_links.handle_business_source(payload=payload)

    def test_handler_late_lease_loss_rolls_back_reference_and_receipt(self):
        with patch("tasker.services.jobs.assert_current_job_lease"), \
                patch("tasker.services.jobs.fence_current_job_effects", side_effect=LostJobLease):
            with self.assertRaises(LostJobLease):
                business_links.handle_business_source(payload={"source_type": "collection_task", "source_id": self.source.pk})
        self.assertFalse(BusinessRecordLink.objects.exists())
        self.assertFalse(BusinessLinkEvent.objects.exists())

    def test_worker_retry_rolls_back_partial_observation_then_replays_safely(self):
        job = business_links.enqueue_business_source("collection_task", self.source.pk)
        with patch("tasker.services.notifications.queue_business_link_update", side_effect=RuntimeError("PRIVATE_FAILURE_SENTINEL")):
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "retry")
        self.assertFalse(BusinessRecordLink.objects.exists())
        self.assertFalse(BusinessLinkEvent.objects.exists())
        job.refresh_from_db()
        self.assertNotIn("PRIVATE_FAILURE_SENTINEL", str(job.result))
        with patch("django.utils.timezone.now", return_value=job.run_after+timedelta(seconds=1)):
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"], "succeeded")
        self.assertEqual(BusinessRecordLink.objects.count(), 1)
        self.assertEqual(BusinessLinkEvent.objects.count(), 1)


@override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
class BusinessLinkConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner = employee("business-race-owner")
        self.source = CollectionTask.objects.create(task_type="FOLLOW_UP_DEBTOR", due_date=DAY,
            assigned_to=self.owner, priority="HIGH")

    def test_concurrent_first_reconciliation_has_one_reference_and_observation(self):
        if connection.vendor != "postgresql":
            self.skipTest("Row-lock race requires PostgreSQL.")
        barrier = Barrier(2)
        def reconcile():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return business_links.reconcile_business_source(source_type="collection_task", source_id=self.source.pk).pk
            finally:
                connections.close_all()
        with patch("tasker.services.notifications.queue_business_link_update"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: reconcile(), range(2)))
        self.assertEqual(len(set(results)), 1)
        self.assertEqual(BusinessRecordLink.objects.count(), 1)
        self.assertEqual(BusinessLinkEvent.objects.count(), 1)
