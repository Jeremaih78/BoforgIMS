"""Procurement source authority, observed stock need and review replay safety."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from threading import Barrier
import json
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import TestCase, TransactionTestCase

from accounting.models import APPayment, Company, JournalEntry
from inventory.models import Product, ProductUnit, StockMovement
from inventory.replenishment_models import ReplenishmentEvent, ReplenishmentPlan
from inventory.replenishment_policies import (can_change_plan, can_manage_company, can_read_plan,
    eligible_assignees, visible_plans_for)
from inventory.services.replenishment import (SourceConflict, change_plan, configure_plan,
    effective_plan_state, get_plan, product_stock_version, stock_facts, synchronize_plan)
from sales.models import Payment
from tasker.models import OrganizationAccess, OutcomeMeasurement, Task, Team, TeamMembership


DAY = date(2030, 1, 7)


def employee(name, *, inventory=True, manager=False):
    user = get_user_model().objects.create_user(name)
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    if inventory:
        names = ["view_product", "view_replenishmentplan", "change_replenishmentplan"]
        if manager:
            names.append("manage_replenishment")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="inventory", codename__in=names))
    return user


class ReplenishmentSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Replenishment source company")
        cls.foreign_company = Company.objects.create(name="Other replenishment company")
        cls.team = Team.objects.create(company=cls.company, name="Procurement source team")
        cls.worker = employee("replenishment-worker")
        cls.peer = employee("replenishment-peer")
        cls.manager = employee("replenishment-manager", manager=True)
        cls.outsider = employee("replenishment-outsider", manager=True)
        cls.tasker_only = employee("replenishment-tasker-only", inventory=False)
        for user in (cls.worker, cls.peer, cls.tasker_only):
            TeamMembership.objects.create(team=cls.team, user=user)
        OrganizationAccess.objects.create(company=cls.company, user=cls.manager, can_manage=True)

    def setUp(self):
        self.product = Product.objects.create(name="Synthetic procurement product", sku="SYNTH-PROCURE",
            quantity=10, reserved=8, reorder_level=2)
        self.plan = configure_plan(user=self.manager, product_id=self.product.pk, company_id=self.company.pk,
            assigned_to_id=self.worker.pk, is_enabled=True, expected_revision="new", client_key=uuid.uuid4(),
            notes="PRIVATE_PROCUREMENT_NOTE", next_review_on=DAY)

    def change(self, **changes):
        values = {"user":self.worker, "pk":self.plan.pk, "expected_revision":self.plan.revision,
            "expected_stock_version":product_stock_version(self.product), "client_key":uuid.uuid4(),
            "review_state":"REVIEWING", "next_review_on":DAY, "notes":self.plan.notes}
        values.update(changes)
        return change_plan(**values)

    def configure(self, **changes):
        values = {"user":self.manager, "product_id":self.product.pk, "company_id":self.company.pk,
            "assigned_to_id":self.plan.assigned_to_id, "is_enabled":self.plan.is_enabled,
            "expected_revision":self.plan.revision, "client_key":uuid.uuid4(),
            "next_review_on":self.plan.next_review_on, "notes":self.plan.notes}
        values.update(changes)
        return configure_plan(**values)

    def test_available_stock_reservations_and_equality_drive_need(self):
        self.assertEqual(stock_facts(self.product)["available"], 2)
        self.assertEqual(stock_facts(self.product)["need_state"], "LOW")
        self.product.reserved = 7
        self.assertEqual(stock_facts(self.product)["need_state"], "CLEAR")
        self.product.quantity, self.product.reserved, self.product.reorder_level = 0, 0, 0
        self.assertEqual(stock_facts(self.product)["need_state"], "LOW")

    def test_invalid_raw_stock_is_not_hidden_as_a_normal_stockout(self):
        for quantity, reserved, level in ((-1,0,2),(1,-1,2),(1,2,2),(1,0,-1)):
            with self.subTest(raw=(quantity,reserved,level)):
                self.product.quantity, self.product.reserved, self.product.reorder_level = quantity,reserved,level
                facts = stock_facts(self.product)
                self.assertEqual(facts["need_state"], "INVALID")
                self.assertIsNone(facts["available"])
                self.plan.product = self.product
                self.assertEqual(effective_plan_state(self.plan)["reason_code"], "stock_records_need_review")

    def test_inactive_and_non_inventory_products_have_no_active_procurement_action(self):
        for changes in ({"is_active":False}, {"is_active":True,"track_inventory":False}):
            with self.subTest(changes=changes):
                # Stock changes can bypass save hooks. Real source GETs load a
                # fresh Product rather than the caller's earlier cached copy.
                Product.objects.filter(pk=self.product.pk).update(**changes)
                fresh = get_plan(user=self.worker,pk=self.plan.pk)
                self.assertEqual(stock_facts(fresh.product)["need_state"],"INACTIVE")
                self.assertEqual(effective_plan_state(fresh)["status"],"UNAVAILABLE")

    def test_tasker_role_alone_does_not_grant_source_read_or_configuration(self):
        self.assertFalse(can_read_plan(self.tasker_only,self.plan))
        with self.assertRaises(PermissionDenied): get_plan(user=self.tasker_only,pk=self.plan.pk)
        with self.assertRaises(PermissionDenied): self.configure(user=self.tasker_only)

    def test_inventory_capability_alone_does_not_cross_company_scope(self):
        self.assertFalse(visible_plans_for(self.outsider).exists())
        self.assertFalse(can_manage_company(self.outsider,self.company.pk))
        with self.assertRaises(PermissionDenied): self.configure(user=self.outsider)

    def test_company_manager_needs_explicit_inventory_management_capability(self):
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="inventory",codename="manage_replenishment"))
        with self.assertRaises(PermissionDenied): self.configure()

    def test_revoked_and_soft_deleted_scope_removes_source_access(self):
        TeamMembership.objects.filter(team=self.team,user=self.worker).delete()
        self.assertFalse(can_read_plan(self.worker,self.plan))
        OrganizationAccess.objects.filter(company=self.company,user=self.manager).delete()
        self.assertFalse(can_manage_company(self.manager,self.company.pk))
        with self.assertRaises(PermissionDenied): self.change()

    def test_inactive_actor_or_company_cannot_read_or_progress(self):
        get_user_model().objects.filter(pk=self.worker.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied): self.change()
        Company.objects.filter(pk=self.company.pk).update(is_active=False)
        self.assertFalse(visible_plans_for(self.manager).exists())

    def test_staff_product_exception_does_not_grant_new_source_capability(self):
        staff = employee("replenishment-core-staff",inventory=False)
        staff.groups.add(Group.objects.get_or_create(name="Staff")[0])
        TeamMembership.objects.create(team=self.team,user=staff)
        self.assertFalse(can_read_plan(staff,self.plan))
        staff.user_permissions.add(Permission.objects.get(content_type__app_label="inventory",codename="view_replenishmentplan"))
        self.assertTrue(can_read_plan(staff,self.plan))

    def test_owner_can_progress_but_peer_cannot_act_on_assigned_work(self):
        self.assertTrue(can_change_plan(self.worker,self.plan))
        self.assertFalse(can_change_plan(self.peer,self.plan))
        with self.assertRaises(PermissionDenied): self.change(user=self.peer)
        updated = self.change(review_state="WAITING_DECISION")
        self.assertEqual(updated.review_state,"WAITING_DECISION")

    def test_owner_cannot_reassign_and_manager_can(self):
        with self.assertRaises(PermissionDenied): self.configure(user=self.worker,assigned_to_id=self.peer.pk)
        self.plan = self.configure(assigned_to_id=self.peer.pk)
        self.assertEqual(self.plan.assigned_to_id,self.peer.pk)
        with self.assertRaises(PermissionDenied): self.change()

    def test_eligible_owner_needs_current_scope_read_and_progress_capability(self):
        self.assertNotIn(self.outsider,eligible_assignees(self.manager,self.company.pk))
        self.assertNotIn(self.tasker_only,eligible_assignees(self.manager,self.company.pk))
        self.peer.user_permissions.remove(Permission.objects.get(content_type__app_label="inventory",codename="change_replenishmentplan"))
        with self.assertRaises(ValidationError): self.configure(assigned_to_id=self.peer.pk)

    def test_unassigned_mapping_never_infers_creator_as_responsible_employee(self):
        self.plan = self.configure(assigned_to_id=None)
        self.assertIsNone(self.plan.assigned_to_id)
        self.assertEqual(self.plan.created_by_id,self.manager.pk)
        self.assertEqual(effective_plan_state(self.plan)["status"],"UNASSIGNED")

    def test_disabled_mapping_is_unavailable_and_cannot_be_progressed(self):
        self.plan = self.configure(is_enabled=False)
        self.assertEqual(effective_plan_state(self.plan)["reason_code"],"responsibility_paused")
        with self.assertRaises(SourceConflict): self.change()

    def test_ordinary_save_rotates_revision_including_update_fields(self):
        previous = self.plan.revision
        self.plan.notes = "new standing source note"
        self.plan.save(update_fields=("notes",))
        self.plan.refresh_from_db()
        self.assertNotEqual(previous,self.plan.revision)

    def test_stale_review_revision_does_not_overwrite_source(self):
        revision = self.plan.revision
        self.plan.notes = "another person's source edit"
        self.plan.save()
        with self.assertRaises(SourceConflict): self.change(expected_revision=revision)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes,"another person's source edit")

    def test_bulk_reservation_change_without_timestamp_or_plan_revision_rejects_stale_decision(self):
        old_time, old_revision = self.product.updated_at,self.plan.revision
        Product.objects.filter(pk=self.product.pk).update(reserved=9)
        with self.assertRaises(SourceConflict): self.change()
        self.product.refresh_from_db(); self.plan.refresh_from_db()
        self.assertEqual(self.product.updated_at,old_time)
        self.assertEqual(self.plan.revision,old_revision)

    def test_replayed_progress_is_one_receipt_and_does_not_rewrite_later_state(self):
        key, old_revision = uuid.uuid4(), self.plan.revision
        first = self.change(client_key=key)
        self.plan = first
        second = self.change(review_state="AWAITING_STOCK")
        replay = self.change(client_key=key,expected_revision=old_revision)
        self.assertEqual(replay.review_state,second.review_state)
        self.assertEqual(ReplenishmentEvent.objects.filter(actor=self.worker,client_key=key).count(),1)

    def test_wrong_key_payload_cannot_reuse_receipt(self):
        key, old_revision = uuid.uuid4(), self.plan.revision
        self.change(client_key=key)
        with self.assertRaises(SourceConflict): self.change(client_key=key,expected_revision=old_revision,review_state="AWAITING_STOCK")

    def test_duplicate_configuration_replay_keeps_one_global_mapping(self):
        key, revision = uuid.uuid4(), self.plan.revision
        original = self.configure(client_key=key)
        replay = self.configure(client_key=key,expected_revision=revision)
        self.assertEqual(original.pk,replay.pk)
        self.assertEqual(ReplenishmentPlan.objects.filter(product=self.product).count(),1)
        self.assertEqual(ReplenishmentEvent.objects.filter(actor=self.manager,client_key=key).count(),1)

    def test_repeated_new_mapping_attempt_cannot_duplicate_global_product(self):
        with self.assertRaises(SourceConflict): self.configure(expected_revision="new")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ReplenishmentPlan.objects.bulk_create([ReplenishmentPlan(product=self.product,company=self.foreign_company)])

    def test_company_move_requires_both_company_authorities_and_an_eligible_owner(self):
        with self.assertRaises(PermissionDenied): self.configure(company_id=self.foreign_company.pk)
        foreign_team = Team.objects.create(company=self.foreign_company,name="Other procurement team")
        TeamMembership.objects.create(team=foreign_team,user=self.worker)
        OrganizationAccess.objects.create(company=self.foreign_company,user=self.manager,can_manage=True)
        moved = self.configure(company_id=self.foreign_company.pk)
        self.assertEqual(moved.company_id,self.foreign_company.pk)
        self.assertEqual(ReplenishmentPlan.objects.filter(product=self.product).count(),1)

    def test_direct_model_edits_cannot_retarget_global_product_or_bypass_company_move_service(self):
        another = Product.objects.create(name="Other synthetic product",sku="SYNTH-OTHER")
        self.plan.product = another
        with self.assertRaises(ValidationError): self.plan.save()
        self.plan.product, self.plan.company = self.product,self.foreign_company
        with self.assertRaises(ValidationError): self.plan.save()

    def test_live_reservation_release_clears_need_before_sweep_without_claiming_receipt(self):
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        fresh = get_plan(user=self.worker,pk=self.plan.pk)
        state = effective_plan_state(fresh)
        self.assertEqual((state["need_state"],state["status"],state["reason_code"]),("CLEAR","COMPLETED","need_cleared"))
        self.assertEqual(fresh.observed_need_state,"LOW")
        with self.assertRaises(SourceConflict): self.change(expected_stock_version=product_stock_version(fresh.product))

    def test_read_and_presentation_have_no_source_side_effects(self):
        before = (list(ReplenishmentPlan.objects.values()),list(ReplenishmentEvent.objects.values()))
        for _ in range(2): effective_plan_state(get_plan(user=self.worker,pk=self.plan.pk))
        self.assertEqual(before,(list(ReplenishmentPlan.objects.values()),list(ReplenishmentEvent.objects.values())))

    def test_observed_clear_then_low_resets_review_and_due_preserving_notes(self):
        self.plan = self.change(review_state="AWAITING_STOCK")
        old_cycle = self.plan.cycle
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        clear = synchronize_plan(self.plan.pk)
        self.assertEqual(clear.observed_need_state,"CLEAR")
        Product.objects.filter(pk=self.product.pk).update(reserved=8)
        reopened = synchronize_plan(self.plan.pk)
        self.assertEqual((reopened.review_state,reopened.observed_need_state),("NEEDS_REVIEW","LOW"))
        self.assertIsNone(reopened.next_review_on)
        self.assertNotEqual(reopened.cycle,old_cycle)
        self.assertEqual(reopened.notes,"PRIVATE_PROCUREMENT_NOTE")

    def test_repeated_unchanged_observation_preserves_revision_and_event_identity(self):
        count, revision = self.plan.events.count(),self.plan.revision
        for _ in range(3): synchronize_plan(self.plan.pk)
        self.plan.refresh_from_db()
        self.assertEqual((self.plan.events.count(),self.plan.revision),(count,revision))

    def test_changed_low_stock_does_not_erase_existing_followup_progress(self):
        self.plan = self.change(review_state="AWAITING_STOCK")
        Product.objects.filter(pk=self.product.pk).update(reserved=9)
        reviewed = synchronize_plan(self.plan.pk)
        self.assertEqual(reviewed.review_state,"AWAITING_STOCK")

    def test_inactive_owner_is_unassigned_without_silent_replacement(self):
        get_user_model().objects.filter(pk=self.worker.pk).update(is_active=False)
        fresh = get_plan(user=self.manager,pk=self.plan.pk)
        self.assertEqual(effective_plan_state(fresh)["status"],"UNASSIGNED")
        self.assertEqual(fresh.assigned_to_id,self.worker.pk)

    def test_progress_does_not_move_stock_or_post_financial_or_productivity_output(self):
        models = (StockMovement,ProductUnit,JournalEntry,APPayment,Payment,Task,OutcomeMeasurement)
        before = [model.objects.count() for model in models]
        stock = list(Product.objects.filter(pk=self.product.pk).values())
        self.change(review_state="WAITING_DECISION")
        synchronize_plan(self.plan.pk)
        self.assertEqual(before,[model.objects.count() for model in models])
        self.assertEqual(stock,list(Product.objects.filter(pk=self.product.pk).values()))

    def test_receipts_are_immutable_and_exclude_source_note_bodies(self):
        self.change()
        events = list(self.plan.events.all())
        self.assertNotIn("PRIVATE_PROCUREMENT_NOTE",json.dumps([event.details for event in events]))
        event = events[0]
        event.action = "changed"
        with self.assertRaises(ValidationError): event.save()
        with self.assertRaises(ValidationError): event.delete()

    def test_no_manual_completion_or_purchase_approval_state_exists(self):
        for state in ("COMPLETED","APPROVED","ORDER_PLACED"):
            with self.assertRaises(ValidationError): self.change(review_state=state)


class ReplenishmentSourceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.company = Company.objects.create(name="Concurrent replenishment company")
        self.manager = employee("concurrent-procurement-manager",manager=True)
        OrganizationAccess.objects.create(company=self.company,user=self.manager,can_manage=True)
        self.product = Product.objects.create(name="Concurrent global pool",sku="SYNTH-CONCURRENT",quantity=0,reorder_level=0)

    def race(self, *, keys, create=True, actors=None):
        barrier = Barrier(2)
        plan = None if create else configure_plan(user=self.manager,product_id=self.product.pk,
            company_id=self.company.pk,assigned_to_id=self.manager.pk,is_enabled=True,
            expected_revision="new",client_key=uuid.uuid4())
        def act(pair):
            key, actor_id = pair
            close_old_connections()
            try:
                with connection.cursor() as cursor: cursor.execute("SET lock_timeout = '5s'")
                actor = get_user_model().objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                try:
                    if create:
                        configure_plan(user=actor,product_id=self.product.pk,company_id=self.company.pk,
                            assigned_to_id=self.manager.pk,is_enabled=True,expected_revision="new",client_key=key)
                    else:
                        change_plan(user=actor,pk=plan.pk,expected_revision=plan.revision,
                            expected_stock_version=product_stock_version(self.product),client_key=key,review_state="REVIEWING")
                    return "saved"
                except SourceConflict:
                    return "conflict"
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(act,zip(keys,actors or [self.manager.pk,self.manager.pk])))

    def test_simultaneous_mapping_creation_keeps_one_global_responsibility(self):
        other = employee("concurrent-other-procurement-manager",manager=True)
        OrganizationAccess.objects.create(company=self.company,user=other,can_manage=True)
        self.assertCountEqual(self.race(keys=[uuid.uuid4(),uuid.uuid4()],
            actors=[self.manager.pk,other.pk]),["saved","conflict"])
        self.assertEqual(ReplenishmentPlan.objects.count(),1)

    def test_simultaneous_duplicate_creation_has_one_source_action_receipt(self):
        key = uuid.uuid4()
        self.assertEqual(self.race(keys=[key,key]),["saved","saved"])
        self.assertEqual(ReplenishmentEvent.objects.filter(actor=self.manager,client_key=key).count(),1)

    def test_simultaneous_progress_changes_do_not_both_overwrite_review(self):
        self.assertCountEqual(self.race(keys=[uuid.uuid4(),uuid.uuid4()],create=False),["saved","conflict"])

    def test_reciprocal_delegations_lock_users_in_one_order_without_deadlock(self):
        other = employee("reciprocal-procurement-manager",manager=True)
        OrganizationAccess.objects.create(company=self.company,user=other,can_manage=True)
        second_product = Product.objects.create(name="Second global pool",sku="SYNTH-SECOND-POOL",quantity=0)
        rows = []
        for actor, product in ((self.manager,self.product),(other,second_product)):
            rows.append(configure_plan(user=actor,product_id=product.pk,company_id=self.company.pk,
                assigned_to_id=actor.pk,is_enabled=True,expected_revision="new",client_key=uuid.uuid4()))
        barrier = Barrier(2)
        def delegate(values):
            actor_id, plan, recipient_id = values
            close_old_connections()
            try:
                with connection.cursor() as cursor: cursor.execute("SET lock_timeout = '5s'")
                actor = get_user_model().objects.get(pk=actor_id)
                barrier.wait(timeout=10)
                result = configure_plan(user=actor,product_id=plan.product_id,company_id=self.company.pk,
                    assigned_to_id=recipient_id,is_enabled=True,expected_revision=plan.revision,client_key=uuid.uuid4())
                return result.assigned_to_id
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(delegate,((self.manager.pk,rows[0],other.pk),(other.pk,rows[1],self.manager.pk))))
        self.assertEqual(result,[other.pk,self.manager.pk])
