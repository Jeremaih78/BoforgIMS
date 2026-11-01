"""Replenishment projections share live inventory authority, not stock posting."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from decimal import Decimal
import json
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied
from django.db import close_old_connections, connection, connections
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import APPayment, Company, JournalEntry
from credit_control.models import CollectionTask
from inventory.models import Product, StockMovement, Supplier
from inventory.replenishment_models import ReplenishmentEvent, ReplenishmentPlan
from inventory.services.replenishment import change_plan, configure_plan, product_stock_version
from sales.models import Payment
from tasker.business_adapters import replenishment as adapter
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink
from tasker.goal_models import OutcomeMeasurement
from tasker.job_models import OutboxJob
from tasker.models import OrganizationAccess, Task, Team, TeamMembership
from tasker.notification_models import Notification, NotificationDelivery, NotificationPreference
from tasker.services import business_links, notifications
from tasker.services.jobs import LostJobLease, run_worker_once


DAY = date(2030, 1, 7)


def employee(name, *, inventory=True, tasker=True, manage=False):
    user = get_user_model().objects.create_user(name)
    if tasker:
        user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="view_task"))
    if inventory:
        names = ["view_product", "view_replenishmentplan", "change_replenishmentplan"]
        if manage:
            names.append("manage_replenishment")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="inventory", codename__in=names))
    return user


@override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=True, TASKER_COLLECTIONS_ADAPTER_ENABLED=False)
class ReplenishmentLinkTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.owner = employee("replenishment-link-owner")
        cls.other = employee("replenishment-link-other")
        cls.manager = employee("replenishment-link-manager", manage=True)
        cls.tasker_only = employee("replenishment-link-tasker-only", inventory=False)
        cls.source_only = employee("replenishment-link-source-only", tasker=False)
        cls.outsider = employee("replenishment-link-outsider")
        cls.company = Company.objects.create(name="Replenishment source company")
        cls.foreign_company = Company.objects.create(name="Foreign replenishment company")
        cls.team = Team.objects.create(company=cls.company, name="Procurement sample team")
        for user in (cls.owner, cls.other, cls.manager, cls.tasker_only, cls.source_only):
            TeamMembership.objects.create(team=cls.team, user=user)
        OrganizationAccess.objects.create(company=cls.company, user=cls.manager, can_manage=True)
        for user in (cls.owner, cls.other):
            NotificationPreference.objects.create(owner=user, respect_work_hours=False, suppress_during_focus=False)
        cls.supplier = Supplier.objects.create(name="PRIVATE_PROCUREMENT_SUPPLIER",
            email="private-procurement@example.invalid", phone="PRIVATE_PROCUREMENT_PHONE")

    def setUp(self):
        self.product = self.make_product()
        self.plan = ReplenishmentPlan.objects.create(product=self.product, company=self.company,
            assigned_to=self.owner, created_by=self.manager, is_enabled=True,
            notes="PRIVATE_PROCUREMENT_NOTES")

    def make_product(self, **changes):
        values = {"name":"Sample printing consumable", "sku":"REPL-"+uuid.uuid4().hex[:12],
            "supplier":self.supplier, "quantity":5, "reserved":3, "reorder_level":2,
            "price":Decimal("876543.21"), "avg_cost":Decimal("654321.1234"),
            "description":"PRIVATE_PROCUREMENT_DESCRIPTION"}
        values.update(changes)
        return Product.objects.create(**values)

    def make_plan(self, **changes):
        values = {"product":self.make_product(), "company":self.company,
            "assigned_to":self.owner, "is_enabled":True}
        values.update(changes)
        return ReplenishmentPlan.objects.create(**values)

    def sync(self, plan=None):
        return business_links.reconcile_business_source(source_type="replenishment_plan", source_id=(plan or self.plan).pk)

    def ids(self, user=None, **filters):
        return set(business_links.visible_business_links_for(user or self.owner, **filters).values_list("pk", flat=True))

    def attention(self, link):
        return Notification.objects.get(business_link=link, cancelled_at__isnull=True)

    def assert_hidden(self, notification, user=None):
        user = user or self.owner
        self.assertFalse(notifications.visible_notifications_for(user, include_held=True).filter(pk=notification.pk).exists())
        with self.assertRaises(PermissionDenied):
            notifications.notification_context(notification, user)

    def work(self, *, state="REVIEWING", next_review_on=None):
        self.plan.refresh_from_db(); self.product.refresh_from_db()
        return change_plan(user=self.owner, pk=self.plan.pk, expected_revision=self.plan.revision,
            expected_stock_version=product_stock_version(self.product), client_key=uuid.uuid4(),
            review_state=state, next_review_on=next_review_on, notes="PRIVATE_PROCUREMENT_UPDATED_NOTES")

    def test_replay_has_one_explicitly_owned_source_reference_event_and_reminder(self):
        link = self.sync()
        event = link.events.get()
        notification = self.attention(link)
        for _ in range(3):
            replay = self.sync()
            self.assertEqual((replay.pk, replay.revision), (link.pk, link.revision))
        self.assertEqual((link.source_app, link.source_type, link.action_key),
            ("inventory", "replenishment_plan", "replenish_stock"))
        self.assertEqual((link.owner_id, link.company_id), (self.owner.pk, self.company.pk))
        self.assertEqual((link.status, link.priority), ("OPEN", "HIGH"))
        self.assertEqual(link.events.get().pk, event.pk)
        self.assertEqual(Notification.objects.filter(business_link=link).count(), 1)
        self.assertEqual(NotificationDelivery.objects.filter(notification=notification).count(), 1)
        self.assertEqual(notification.owner_id, self.owner.pk)
        self.assertFalse(Notification.objects.filter(business_link=link, owner=self.manager).exists())

    def test_null_review_date_still_has_current_sql_proof_and_deliverable_attention(self):
        link = self.sync()
        self.assertIsNone(link.due_date)
        self.assertTrue(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())
        self.assertTrue(business_links.business_notification_available(user=self.owner, link=link, source_revision=link.revision))
        notification = self.attention(link)
        delivery = notification.deliveries.get()
        self.assertEqual(notifications.deliver_notification({"delivery_id":delivery.pk}), {"delivered":1})
        self.assertTrue(notifications.visible_notifications_for(self.owner).filter(pk=notification.pk).exists())

    def test_reserved_stock_drives_priority_and_live_python_sql_version_equality(self):
        link = self.sync()
        first = link.source_version
        old_updated = self.product.updated_at
        Product.objects.filter(pk=self.product.pk).update(reserved=5)
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at, old_updated)
        live = business_links.get_business_link(user=self.owner, pk=link.pk)
        row = business_links.visible_business_links_for(self.owner).get(pk=link.pk)
        expected = adapter.snapshot("replenishment_plan", self.plan.pk)["source_version"]
        self.assertEqual((live.priority, live.status), ("CRITICAL", "OPEN"))
        self.assertNotEqual(expected, first)
        self.assertEqual(row._business_source_version, expected)
        self.assertEqual(live.source_version, expected)
        self.assertTrue(live.source_stale)

    def test_source_progress_is_observed_without_independent_tasker_completion(self):
        link = self.sync()
        self.work(state="AWAITING_STOCK", next_review_on=DAY)
        current = business_links.get_business_link(user=self.owner, pk=link.pk)
        self.assertEqual((current.status, current.due_date), ("IN_PROGRESS", DAY))
        projected = self.sync()
        self.assertEqual((projected.pk, projected.status, projected.source_status),
            (link.pk, "IN_PROGRESS", "LOW:AWAITING_STOCK"))
        self.assertEqual(self.attention(projected).owner_id, self.owner.pk)

    def test_raw_reservation_release_hides_and_cancels_old_notice_without_purchase_claim(self):
        link = self.sync()
        notification = self.attention(link)
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        self.assertNotIn(link.pk, self.ids(state="active"))
        current = business_links.get_business_link(user=self.owner, pk=link.pk)
        self.assertEqual((current.status, current.reason_code), ("COMPLETED", "need_cleared"))
        self.assert_hidden(notification)
        delivery = notification.deliveries.get()
        self.assertEqual(notifications.deliver_notification({"delivery_id":delivery.pk}), {"cancelled":True})
        delivery.refresh_from_db()
        self.assertEqual(delivery.state, "CANCELLED")
        self.sync()
        self.assertFalse(Notification.objects.filter(business_link=link, cancelled_at__isnull=True).exists())
        self.assertFalse(StockMovement.objects.exists())
        self.assertFalse(OutcomeMeasurement.objects.exists())

    def test_raw_stock_or_product_metadata_change_hides_stale_reminder_before_repair(self):
        link = self.sync()
        original = self.attention(link)
        for values in ({"reserved":5}, {"reorder_level":3}, {"name":"Updated sample consumable"}):
            with self.subTest(values=values):
                Product.objects.filter(pk=self.product.pk).update(**values)
                self.assert_hidden(original)
                current = self.sync()
                original = self.attention(current)
                self.assertTrue(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())

    def test_low_clear_low_reuses_reference_with_new_observation_cycle_and_reminder(self):
        link = self.sync()
        first_notification = self.attention(link)
        self.plan.refresh_from_db(); first_cycle = self.plan.cycle
        self.work(state="AWAITING_STOCK", next_review_on=DAY)
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        cleared = self.sync()
        self.assertEqual((cleared.pk, cleared.status), (link.pk, "COMPLETED"))
        Product.objects.filter(pk=self.product.pk).update(reserved=5)
        reopened = self.sync()
        self.plan.refresh_from_db()
        self.assertEqual((reopened.pk, reopened.status, reopened.priority), (link.pk, "OPEN", "CRITICAL"))
        self.assertNotEqual(self.plan.cycle, first_cycle)
        self.assertEqual(self.plan.review_state, "NEEDS_REVIEW")
        self.assertIsNone(self.plan.next_review_on)
        self.assertNotEqual(self.attention(reopened).pk, first_notification.pk)
        count = reopened.events.count()
        self.sync()
        self.assertEqual(reopened.events.count(), count)
        self.assertEqual(BusinessRecordLink.objects.count(), 1)

    def test_reconciliation_creates_no_native_task_outcome_stock_or_financial_rows(self):
        models = (Task, OutcomeMeasurement, StockMovement, JournalEntry, APPayment, Payment)
        before = [model.objects.count() for model in models]
        original = (self.product.quantity, self.product.reserved, self.product.avg_cost)
        link = self.sync()
        self.work(state="WAITING_DECISION")
        self.sync()
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        self.sync()
        self.assertEqual([model.objects.count() for model in models], before)
        self.product.refresh_from_db()
        self.assertEqual((self.product.quantity, self.product.avg_cost), (original[0], original[2]))
        self.assertEqual(link.source_type, "replenishment_plan")

    def test_projection_event_job_and_notification_context_copy_no_private_procurement_details(self):
        link = self.sync()
        notification = self.attention(link)
        business_links.enqueue_business_source("replenishment_plan", self.plan.pk)
        payload = {"links":list(BusinessRecordLink.objects.values()),
            "events":list(BusinessLinkEvent.objects.values()),
            "jobs":list(OutboxJob.objects.values("payload")),
            "attention":notifications.notification_context(notification,self.owner)}
        encoded = json.dumps(payload, default=str)
        for value in ("PRIVATE_PROCUREMENT_NOTES", "PRIVATE_PROCUREMENT_SUPPLIER",
                "private-procurement@example.invalid", "PRIVATE_PROCUREMENT_PHONE",
                "PRIVATE_PROCUREMENT_DESCRIPTION", "876543.21", "654321.1234"):
            self.assertNotIn(value, encoded)

    def test_unassigned_or_inactive_owner_has_no_creator_fallback_or_notification(self):
        self.plan.assigned_to = None
        self.plan.save(update_fields=("assigned_to",))
        link = self.sync()
        self.assertEqual((link.owner_id, link.status), (None, "UNASSIGNED"))
        self.assertFalse(Notification.objects.filter(business_link=link).exists())
        self.plan.assigned_to = self.owner
        self.plan.save(update_fields=("assigned_to",))
        get_user_model().objects.filter(pk=self.owner.pk).update(is_active=False)
        self.assertEqual(self.sync().status, "UNASSIGNED")

    def test_tasker_capability_does_not_replace_source_permission_or_company_scope(self):
        link = self.sync()
        for user in (self.tasker_only,self.outsider):
            with self.subTest(user=user.pk):
                self.assertNotIn(link.pk,self.ids(user))
                with self.assertRaises(PermissionDenied):
                    business_links.get_business_link(user=user,pk=link.pk)
        with self.assertRaises(PermissionDenied):
            business_links.visible_business_links_for(self.source_only)

    def test_source_read_requires_both_product_and_procurement_capability(self):
        link = self.sync()
        for codename in ("view_product", "view_replenishmentplan"):
            with self.subTest(permission=codename):
                perm=Permission.objects.get(content_type__app_label="inventory",codename=codename)
                self.owner.user_permissions.remove(perm)
                self.assertNotIn(link.pk,self.ids())
                self.owner.user_permissions.add(perm)

    def test_soft_deleted_membership_or_grant_hides_retained_projection_and_attention(self):
        link = self.sync(); notification = self.attention(link)
        TeamMembership.objects.filter(team=self.team,user=self.owner).delete()
        self.assertNotIn(link.pk,self.ids())
        self.assert_hidden(notification)
        grant=OrganizationAccess.objects.create(company=self.company,user=self.owner,can_view_all=True)
        self.assertIn(link.pk,self.ids())
        grant.delete()
        self.assertNotIn(link.pk,self.ids())
        self.assert_hidden(notification)

    def test_inactive_company_immediately_removes_source_access_and_attention(self):
        link=self.sync(); notification=self.attention(link)
        Company.objects.filter(pk=self.company.pk).update(is_active=False)
        self.assertNotIn(link.pk,self.ids())
        self.assert_hidden(notification)
        with self.assertRaises(PermissionDenied):
            business_links.get_business_link(user=self.owner,pk=link.pk)
        self.assertEqual(self.sync().status,"UNAVAILABLE")

    def test_bulk_assignment_moves_personal_queue_and_targets_only_new_owner(self):
        link=self.sync(); old=self.attention(link)
        ReplenishmentPlan.objects.filter(pk=self.plan.pk).update(assigned_to=self.other)
        self.assertNotIn(link.pk,business_links.business_links_for_owner(self.owner).values_list("pk",flat=True))
        self.assertIn(link.pk,business_links.business_links_for_owner(self.other).values_list("pk",flat=True))
        self.assert_hidden(old)
        replacement=self.sync()
        self.assertEqual(self.attention(replacement).owner_id,self.other.pk)
        old.refresh_from_db()
        self.assertIsNotNone(old.cancelled_at)

    def test_raw_company_remapping_never_trusts_stored_company_or_owner(self):
        link=self.sync(); notification=self.attention(link)
        ReplenishmentPlan.objects.filter(pk=self.plan.pk).update(company=self.foreign_company)
        self.assertNotIn(link.pk,self.ids())
        self.assert_hidden(notification)
        with self.assertRaises(PermissionDenied):
            business_links.business_source_url(user=self.owner,link=link)

    def test_invalid_and_inactive_stock_are_unavailable_instead_of_false_stockouts(self):
        link=self.sync()
        for values in ({"reserved":6},{"reserved":-1},{"quantity":-1},
                {"reorder_level":-1},{"is_active":False},{"track_inventory":False}):
            with self.subTest(values=values):
                Product.objects.filter(pk=self.product.pk).update(quantity=5,reserved=3,reorder_level=2,
                    is_active=True,track_inventory=True)
                Product.objects.filter(pk=self.product.pk).update(**values)
                current=business_links.get_business_link(user=self.owner,pk=link.pk)
                self.assertEqual(current.status,"UNAVAILABLE")
                self.assertNotIn(link.pk,self.ids(state="active"))
                self.assertFalse(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())

    def test_zero_threshold_and_zero_available_stock_is_an_explicit_critical_need(self):
        Product.objects.filter(pk=self.product.pk).update(quantity=0,reserved=0,reorder_level=0)
        link=self.sync()
        self.assertEqual((link.status,link.priority),("OPEN","CRITICAL"))
        self.assertIn(link.pk,self.ids(state="active"))

    def test_valid_int32_stock_boundaries_do_not_overflow_action_or_notification_queries(self):
        maximum=2147483647
        for reserved, priority in ((maximum,"CRITICAL"),(maximum-1,"HIGH")):
            with self.subTest(reserved=reserved):
                Product.objects.filter(pk=self.product.pk).update(
                    quantity=maximum,reserved=reserved,reorder_level=maximum)
                link=self.sync()
                self.assertEqual((link.status,link.priority),("OPEN",priority))
                self.assertIn(link.pk,self.ids(state="active"))
                panel=business_links.business_actions_for(self.owner)
                self.assertEqual({item.pk for item in panel["items"]},{link.pk})
                self.assertTrue(business_links.business_notification_links_for(self.owner).filter(pk=link.pk).exists())
                notice=self.attention(link)
                self.assertTrue(notifications.visible_notifications_for(self.owner,include_held=True).filter(pk=notice.pk).exists())

    def test_replenishment_flag_retires_only_its_projection_then_reenables_with_new_notice(self):
        link=self.sync(); notice=self.attention(link)
        with override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=False):
            self.assertNotIn(link.pk,self.ids())
            self.assert_hidden(notice)
            business_links.reconcile_business_links(limit=10)
            link.refresh_from_db()
            self.assertEqual((link.status,link.reason_code),("UNAVAILABLE","adapter_disabled"))
        reopened=self.sync()
        self.assertEqual((reopened.pk,reopened.status),(link.pk,"OPEN"))
        self.assertNotEqual(self.attention(reopened).pk,notice.pk)

    def test_disabled_retirement_avoids_source_hydration_and_preserves_source_observation(self):
        link=self.sync(); notice=self.attention(link)
        source_before=ReplenishmentPlan.objects.values().get(pk=self.plan.pk)
        source_events=ReplenishmentEvent.objects.count()
        projection_events=link.events.count()
        source_version=link.source_version
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        stock_before=Product.objects.values("quantity","reserved","reorder_level","updated_at").get(pk=self.product.pk)
        with override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=False):
            with patch("tasker.business_adapters.replenishment.load_source",side_effect=AssertionError("Disabled retirement must not load a source")) as load, \
                    patch("tasker.business_adapters.replenishment.snapshot",side_effect=AssertionError("Disabled retirement must not hydrate a source snapshot")) as snapshot, \
                    patch("inventory.services.replenishment.synchronize_plan",side_effect=AssertionError("Disabled retirement must not observe source stock")) as synchronize:
                with CaptureQueriesContext(connection) as queries:
                    business_links.reconcile_business_links(limit=10)
                    business_links.reconcile_business_links(limit=10)
                load.assert_not_called(); snapshot.assert_not_called(); synchronize.assert_not_called()
            source_tables=("inventory_product","inventory_replenishmentplan","inventory_replenishmentevent")
            self.assertFalse(any(table in row["sql"].lower() for row in queries for table in source_tables))
        link.refresh_from_db(); notice.refresh_from_db()
        self.assertEqual((link.status,link.reason_code),("UNAVAILABLE","adapter_disabled"))
        self.assertEqual(link.source_version,source_version)
        self.assertEqual(link.events.count(),projection_events+1)
        self.assertIsNotNone(notice.cancelled_at)
        self.assertEqual(notice.deliveries.get().state,"CANCELLED")
        self.assertEqual(ReplenishmentPlan.objects.values().get(pk=self.plan.pk),source_before)
        self.assertEqual(ReplenishmentEvent.objects.count(),source_events)
        self.assertEqual(Product.objects.values("quantity","reserved","reorder_level","updated_at").get(pk=self.product.pk),stock_before)
        # Re-enabling observes current authoritative stock instead of reviving
        # the metadata intentionally retained by hidden-adapter retirement.
        current=self.sync()
        self.plan.refresh_from_db()
        self.assertEqual((current.status,current.reason_code),("COMPLETED","need_cleared"))
        self.assertEqual(self.plan.observed_need_state,"CLEAR")

    @override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
    def test_collections_and_replenishment_flags_remain_independent(self):
        self.owner.user_permissions.add(Permission.objects.get(content_type__app_label="credit_control",codename="view_collectiontask"))
        source=CollectionTask.objects.create(company=self.company,assigned_to=self.owner,
            task_type="FOLLOW_UP_DEBTOR",due_date=DAY)
        collection=business_links.reconcile_business_source(source_type="collection_task",source_id=source.pk)
        replenishment=self.sync()
        with override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False):
            self.assertIn(replenishment.pk,self.ids())
            self.assertNotIn(collection.pk,self.ids())
        with override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=False):
            self.assertIn(collection.pk,self.ids())
            self.assertNotIn(replenishment.pk,self.ids())
            business_links.reconcile_business_links(limit=10)
            collection.refresh_from_db()
            self.assertEqual(collection.status,"OPEN")

    def test_missing_source_cannot_leave_a_saved_business_reference_actionable(self):
        facts=adapter.snapshot("replenishment_plan",self.plan.pk)
        facts.update(source_record_id=self.plan.pk+100000)
        link=BusinessRecordLink.objects.create(**facts,source_digest="0"*64)
        repaired=business_links.reconcile_business_source(source_type="replenishment_plan",source_id=link.source_record_id)
        self.assertEqual((repaired.pk,repaired.status,repaired.reason_code),(link.pk,"UNAVAILABLE","source_missing"))
        self.assertNotIn(link.pk,self.ids())

    def test_canonical_source_url_uses_inventory_host_and_rechecks_scope(self):
        link=self.sync()
        expected=host_reverse("ims:inventory:replenishment_detail",kwargs={"pk":self.plan.pk},host="ims")
        self.assertEqual(business_links.business_source_url(user=self.owner,link=link),expected)
        TeamMembership.objects.filter(team=self.team,user=self.owner).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            business_links.business_source_url(user=self.owner,link=link)

    def test_tasker_management_title_cannot_configure_procurement_without_source_authority(self):
        group,_=Group.objects.get_or_create(name="Tasker Manager")
        self.tasker_only.groups.add(group)
        self.tasker_only.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        OrganizationAccess.objects.create(company=self.company,user=self.tasker_only,can_manage=True)
        with self.assertRaises(PermissionDenied):
            configure_plan(user=self.tasker_only,product_id=self.product.pk,company_id=self.company.pk,
                assigned_to_id=self.owner.pk,is_enabled=True,expected_revision=self.plan.revision,client_key=uuid.uuid4())

    def test_bounded_sweep_repairs_bulk_stock_changes_when_no_product_event_was_emitted(self):
        link=self.sync()
        OutboxJob.objects.all().delete()
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        for _ in range(5):
            result=business_links.reconcile_business_links(limit=1)
            self.assertLessEqual(result["scanned"],1)
        self.assertTrue(OutboxJob.objects.filter(kind="business.reconcile",
            payload={"source_type":"replenishment_plan","source_id":self.plan.pk}).exists())
        self.assertEqual(self.sync().status,"COMPLETED")
        self.assertEqual(BusinessRecordLink.objects.count(),1)

    @override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
    def test_limit_one_rotation_does_not_starve_either_adapter_or_later_products(self):
        plans=[self.plan]+[self.make_plan() for _ in range(4)]
        tasks=[CollectionTask.objects.create(company=self.company,assigned_to=self.owner,
            task_type="FOLLOW_UP_DEBTOR",due_date=DAY) for _ in range(3)]
        OutboxJob.objects.all().delete()  # Model the recovery path after lost hooks.
        for _ in range(60):
            result=business_links.reconcile_business_links(limit=1)
            self.assertLessEqual(result["scanned"],1)
        references=set(OutboxJob.objects.filter(kind="business.reconcile").values_list("payload__source_type","payload__source_id"))
        expected={("replenishment_plan",plan.pk) for plan in plans}
        expected|={("collection_task",task.pk) for task in tasks}
        self.assertTrue(expected.issubset(references),expected-references)

    def test_late_lease_loss_rolls_back_source_observation_projection_and_attention(self):
        before=ReplenishmentEvent.objects.count()
        with patch("tasker.services.jobs.assert_current_job_lease"), \
                patch("tasker.services.jobs.fence_current_job_effects",side_effect=LostJobLease):
            with self.assertRaises(LostJobLease):
                business_links.handle_business_source(payload={"source_type":"replenishment_plan","source_id":self.plan.pk})
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.observed_need_state,"UNKNOWN")
        self.assertEqual(ReplenishmentEvent.objects.count(),before)
        self.assertFalse(BusinessRecordLink.objects.exists())
        self.assertFalse(BusinessLinkEvent.objects.exists())
        self.assertFalse(Notification.objects.exists())

    def test_worker_failure_retries_reference_only_payload_without_duplicate_effects(self):
        job=business_links.enqueue_business_source("replenishment_plan",self.plan.pk)
        with patch("tasker.services.notifications.queue_business_link_update",side_effect=RuntimeError("PRIVATE_REPLENISHMENT_FAILURE")):
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"],"retry")
        self.plan.refresh_from_db(); job.refresh_from_db()
        self.assertEqual(self.plan.observed_need_state,"UNKNOWN")
        self.assertFalse(BusinessRecordLink.objects.exists())
        self.assertFalse(ReplenishmentEvent.objects.exists())
        self.assertNotIn("PRIVATE_REPLENISHMENT_FAILURE",str(job.result))
        self.assertEqual(job.payload,{"source_type":"replenishment_plan","source_id":self.plan.pk})
        with patch("django.utils.timezone.now",return_value=job.run_after+timedelta(seconds=1)):
            self.assertEqual(run_worker_once(limit=1)[0]["outcome"],"succeeded")
        self.assertEqual(BusinessRecordLink.objects.count(),1)
        self.assertEqual(BusinessLinkEvent.objects.count(),1)
        self.assertEqual(ReplenishmentEvent.objects.count(),1)


@override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=True,TASKER_COLLECTIONS_ADAPTER_ENABLED=False)
class ReplenishmentProjectionConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.owner=employee("replenishment-projection-race")
        self.company=Company.objects.create(name="Projection race company")
        self.team=Team.objects.create(company=self.company,name="Projection race team")
        TeamMembership.objects.create(team=self.team,user=self.owner)
        self.product=Product.objects.create(name="Projection race consumable",sku="REPL-PROJECTION-RACE",
            quantity=1,reserved=1,reorder_level=0)
        self.plan=ReplenishmentPlan.objects.create(product=self.product,company=self.company,
            assigned_to=self.owner,is_enabled=True)

    def test_concurrent_first_projection_has_one_source_observation_and_reference(self):
        if connection.vendor!="postgresql":
            self.skipTest("Source row-lock race requires PostgreSQL.")
        barrier=Barrier(2)
        def reconcile():
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                barrier.wait(timeout=10)
                return business_links.reconcile_business_source(source_type="replenishment_plan",source_id=self.plan.pk).pk
            finally:
                connections.close_all()
        with patch("tasker.services.notifications.queue_business_link_update"):
            with ThreadPoolExecutor(max_workers=2) as pool:
                results=list(pool.map(lambda _:reconcile(),range(2)))
        self.assertEqual(len(set(results)),1)
        self.assertEqual(BusinessRecordLink.objects.count(),1)
        self.assertEqual(BusinessLinkEvent.objects.count(),1)
        self.assertEqual(ReplenishmentEvent.objects.filter(action="need_observed").count(),1)
