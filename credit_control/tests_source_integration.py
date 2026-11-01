"""Canonical collection authority, privacy boundaries and replay-safe work."""
from datetime import date, timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.contrib.admin import AdminSite
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.test import Client, RequestFactory, TestCase, TransactionTestCase
from django.urls import reverse

from accounting.models import Company, APPayment, JournalEntry
from customers.models import Customer
from sales.models import Payment
from tasker.models import OrganizationAccess, Team, TeamMembership
from credit_control.models import CollectionTask, PromiseToPay, SourceActionEvent
from credit_control.source_policies import (can_read_collection_task, can_read_promise,
    can_change_source, source_assignees, visible_collection_tasks_for, visible_promises_for)
from credit_control.services.tasker_sources import (SourceConflict, assign_promise,
    change_collection_task, get_collection_task, get_promise)
from credit_control.services.workflows import auto_create_collection_tasks
from credit_control.forms import CollectionTaskForm
from credit_control.admin import CollectionTaskAdmin, PromiseToPayAdmin


def employee(name, *, credit=True):
    user=get_user_model().objects.create_user(name)
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    if credit:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("view_collectiontask","change_collectiontask","add_collectiontask",
                "view_promisetopay","change_promisetopay","add_promisetopay")))
    return user


class CollectionSourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name="Source company")
        cls.foreign_company=Company.objects.create(name="Foreign source company")
        cls.team=Team.objects.create(company=cls.company,name="Source team")
        cls.manager=employee("source-manager")
        cls.worker=employee("source-worker")
        cls.recipient=employee("source-recipient")
        cls.tasker_only=employee("source-tasker-only",credit=False)
        cls.outsider=employee("source-outsider")
        for user in (cls.worker,cls.recipient,cls.tasker_only):
            TeamMembership.objects.create(team=cls.team,user=user)
        OrganizationAccess.objects.create(company=cls.company,user=cls.manager,can_manage=True)
        cls.customer=Customer.objects.create(name="Synthetic source customer")

    def setUp(self):
        self.item=CollectionTask.objects.create(company=self.company,customer=self.customer,
            task_type="FOLLOW_UP_DEBTOR",assigned_to=self.worker,due_date=date(2030,1,7),notes="PRIVATE_SOURCE_NOTE")
        self.promise=PromiseToPay.objects.create(company=self.company,customer=self.customer,
            promised_amount=Decimal("50"),promised_date=date(2030,1,7),assigned_to=self.worker,
            actual_paid_amount=Decimal("10"),notes="PRIVATE_PROMISE_NOTE")

    def action(self,action,*,user=None,**changes):
        return change_collection_task(**{"user":user or self.worker,"pk":self.item.pk,
            "expected_revision":self.item.revision,"client_key":uuid.uuid4(),"action":action,**changes})

    def url(self,name,**kwargs):
        return reverse("ims:credit_control:"+name,kwargs=kwargs)

    def test_tasker_role_alone_cannot_read_source_records(self):
        self.assertFalse(can_read_collection_task(self.tasker_only,self.item))
        self.assertFalse(can_read_promise(self.tasker_only,self.promise))
        with self.assertRaises(PermissionDenied): get_collection_task(user=self.tasker_only,pk=self.item.pk)
        with self.assertRaises(PermissionDenied): get_promise(user=self.tasker_only,pk=self.promise.pk)

    def test_credit_permission_alone_does_not_cross_companies(self):
        self.assertFalse(visible_collection_tasks_for(self.outsider).exists())
        self.assertFalse(visible_promises_for(self.outsider).exists())

    def test_company_membership_and_credit_permission_allow_source_read(self):
        self.assertTrue(can_read_collection_task(self.worker,self.item))
        self.assertTrue(can_read_promise(self.manager,self.promise))

    def test_revoked_company_membership_removes_assigned_source_access(self):
        TeamMembership.objects.filter(team=self.team,user=self.worker).update(is_active=False)
        self.assertFalse(can_read_collection_task(self.worker,self.item))
        self.assertFalse(can_read_promise(self.worker,self.promise))

    def test_soft_deleted_membership_and_grants_revoke_current_source_scope(self):
        TeamMembership.objects.filter(team=self.team,user=self.worker).delete()
        OrganizationAccess.objects.filter(company=self.company,user=self.manager).delete()
        self.assertFalse(can_read_collection_task(self.worker,self.item))
        self.assertFalse(can_read_promise(self.manager,self.promise))
        self.assertFalse(source_assignees(self.manager,self.company.pk,
            permission="credit_control.view_collectiontask").exists())

    def test_inactive_company_and_actor_are_denied(self):
        self.company.is_active=False; self.company.save()
        self.assertFalse(can_read_collection_task(self.worker,self.item))
        self.company.is_active=True; self.company.save()
        get_user_model().objects.filter(pk=self.worker.pk).update(is_active=False)
        self.assertFalse(can_change_source(self.worker,self.item))

    def test_null_company_legacy_is_owner_only_except_operational_admin(self):
        self.item.company=None; self.item.save()
        self.assertTrue(can_read_collection_task(self.worker,self.item))
        self.assertFalse(can_read_collection_task(self.manager,self.item))
        admin=employee("explicit-operational-admin",credit=False)
        group,_=Group.objects.get_or_create(name="Admin"); admin.groups.add(group)
        self.assertTrue(can_read_collection_task(admin,self.item))

    def test_unassigned_promise_does_not_infer_creator_as_owner(self):
        promise=PromiseToPay.objects.create(customer=self.customer,promised_amount=1,
            promised_date=date(2030,1,7),created_by=self.worker)
        self.assertIsNone(promise.assigned_to_id)
        self.assertFalse(can_read_promise(self.worker,promise))

    def test_start_done_reopen_cancel_are_source_authoritative(self):
        self.item=self.action("start"); self.assertEqual(self.item.status,"IN_PROGRESS")
        self.item=self.action("done"); self.assertEqual(self.item.status,"DONE"); self.assertTrue(self.item.completed_at)
        self.item=self.action("reopen"); self.assertEqual(self.item.status,"PENDING"); self.assertIsNone(self.item.completed_at)
        self.item=self.action("cancel"); self.assertEqual(self.item.status,"CANCELLED")

    def test_replayed_action_is_one_receipt_and_does_not_recomplete_reopened_task(self):
        key=uuid.uuid4(); revision=self.item.revision
        self.item=self.action("done",client_key=key)
        result=self.action("done",client_key=key,expected_revision=revision)
        self.assertEqual(result.status,"DONE")
        self.assertEqual(SourceActionEvent.objects.count(),1)
        self.item=self.action("reopen")
        result=self.action("done",client_key=key,expected_revision=revision)
        self.assertEqual(result.status,"PENDING")
        self.assertEqual(SourceActionEvent.objects.count(),2)

    def test_stale_revision_cannot_overwrite_current_source(self):
        old=self.item.revision
        self.item.priority="HIGH"; self.item.save()
        with self.assertRaises(SourceConflict): self.action("done",expected_revision=old)
        self.item.refresh_from_db(); self.assertEqual(self.item.status,"PENDING")

    def test_revision_changes_with_ordinary_source_save_update_fields(self):
        old=self.promise.revision
        self.promise.status="BROKEN"; self.promise.save(update_fields=["status"])
        self.promise.refresh_from_db(); self.assertNotEqual(old,self.promise.revision)
        old=self.item.revision; self.item.priority="HIGH"; self.item.save(update_fields=["priority"])
        self.item.refresh_from_db(); self.assertNotEqual(old,self.item.revision)

    def test_unsupported_and_financial_fields_are_rejected_without_writes(self):
        for values in ({"actual_paid_amount":"50"},{"status":"KEPT"},{"notes":"replace"},{"company_id":self.foreign_company.pk}):
            with self.subTest(values=values),self.assertRaises(ValidationError): self.action("metadata",values=values)
        self.assertFalse(SourceActionEvent.objects.exists())

    def test_non_mapping_work_payloads_cannot_be_coerced_into_actions(self):
        for values in ([],False,""):
            with self.subTest(values=values),self.assertRaises(ValidationError):
                self.action("done",values=values)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status,"PENDING")

    def test_done_does_not_post_money_or_change_promise_evidence(self):
        before=[Payment.objects.count(),APPayment.objects.count(),JournalEntry.objects.count()]
        promise_before=PromiseToPay.objects.filter(pk=self.promise.pk).values().get()
        self.action("done")
        self.assertEqual(before,[Payment.objects.count(),APPayment.objects.count(),JournalEntry.objects.count()])
        self.assertEqual(promise_before,PromiseToPay.objects.filter(pk=self.promise.pk).values().get())

    def test_source_worker_cannot_reassign_but_manager_can(self):
        values={"assigned_to_id":self.recipient.pk,"priority":"HIGH","due_date":"2030-01-08"}
        with self.assertRaises(PermissionDenied): self.action("metadata",values=values)
        item=self.action("metadata",user=self.manager,values=values)
        self.assertEqual((item.assigned_to_id,item.priority,str(item.due_date)),(self.recipient.pk,"HIGH","2030-01-08"))
        self.assertEqual(item.notes,"PRIVATE_SOURCE_NOTE")

    def test_assignment_requires_credit_read_capability_and_current_scope(self):
        for target in (self.tasker_only,self.outsider):
            with self.subTest(target=target.pk),self.assertRaises(ValidationError):
                self.action("metadata",user=self.manager,values={"assigned_to_id":target.pk})

    def test_promise_assignment_preserves_payment_status_and_updates_known_check_owner(self):
        check=CollectionTask.objects.create(company=self.company,customer=self.customer,
            promise_to_pay=self.promise,task_type="CHECK_PROMISE",assigned_to=self.worker,due_date=self.promise.promised_date)
        key=uuid.uuid4(); old=self.promise.revision
        result=assign_promise(user=self.manager,pk=self.promise.pk,assigned_to_id=self.recipient.pk,
            expected_revision=old,client_key=key)
        self.assertEqual((result.status,result.actual_paid_amount,result.assigned_to_id),("OPEN",Decimal("10"),self.recipient.pk))
        check.refresh_from_db(); self.assertEqual(check.assigned_to_id,self.recipient.pk)
        assign_promise(user=self.manager,pk=self.promise.pk,assigned_to_id=self.recipient.pk,expected_revision=old,client_key=key)
        self.assertEqual(SourceActionEvent.objects.count(),1)

    def test_check_owner_changes_through_promise_only(self):
        self.item.promise_to_pay=self.promise; self.item.task_type="CHECK_PROMISE"; self.item.save()
        with self.assertRaises(ValidationError): self.action("metadata",user=self.manager,values={"assigned_to_id":self.recipient.pk})

    def test_auto_creation_keeps_two_same_day_promises_distinct_and_replays_once(self):
        PromiseToPay.objects.create(company=self.company,customer=self.customer,promised_amount=25,
            promised_date=self.promise.promised_date,assigned_to=self.recipient)
        auto_create_collection_tasks(as_of_date=self.promise.promised_date)
        checks=CollectionTask.objects.filter(task_type="CHECK_PROMISE",promise_to_pay__isnull=False)
        self.assertEqual(checks.count(),2)
        self.assertEqual(set(checks.values_list("assigned_to_id",flat=True)),{self.worker.pk,self.recipient.pk})
        self.assertFalse(auto_create_collection_tasks(as_of_date=self.promise.promised_date))

    def test_one_canonical_check_per_promise_database_constraint(self):
        CollectionTask.objects.create(promise_to_pay=self.promise,task_type="CHECK_PROMISE",due_date=self.promise.promised_date)
        with self.assertRaises(IntegrityError),transaction.atomic():
            CollectionTask.objects.create(promise_to_pay=self.promise,task_type="CHECK_PROMISE",due_date=self.promise.promised_date+timedelta(days=1))

    def test_receipts_cannot_be_rewritten(self):
        self.action("done")
        event=SourceActionEvent.objects.get()
        with self.assertRaises(ValueError): event.save()
        with self.assertRaises(ValueError): event.delete()

    def test_wrong_key_payload_cannot_reuse_an_existing_receipt(self):
        key=uuid.uuid4(); revision=self.item.revision
        self.item=self.action("done",client_key=key)
        with self.assertRaises(SourceConflict): self.action("cancel",client_key=key,expected_revision=revision)

    def test_source_pages_are_owner_scoped_and_readonly_on_get(self):
        self.client.force_login(self.worker)
        before=CollectionTask.objects.filter(pk=self.item.pk).values().get()
        result=self.client.get(self.url("task_detail",pk=self.item.pk))
        self.assertContains(result,"Mark follow-up done")
        self.assertIn("no-store",result["Cache-Control"])
        self.assertEqual(before,CollectionTask.objects.filter(pk=self.item.pk).values().get())
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("task_detail",pk=self.item.pk)).status_code,403)
        self.assertEqual(self.client.get(self.url("promise_detail",pk=self.promise.pk)).status_code,403)

    def test_source_post_requires_csrf_and_current_write_permission(self):
        client=Client(enforce_csrf_checks=True); client.force_login(self.worker)
        self.assertEqual(client.post(self.url("task_action",pk=self.item.pk,action="done"),{
            "expected_revision":self.item.revision,"client_key":uuid.uuid4()}).status_code,403)
        self.client.force_login(self.worker)
        response=self.client.post(self.url("task_action",pk=self.item.pk,action="done"),{
            "expected_revision":self.item.revision,"client_key":uuid.uuid4()})
        self.assertEqual(response.status_code,302)
        self.item.refresh_from_db(); self.assertEqual(self.item.status,"DONE")

    def test_auto_generation_get_is_readonly_and_post_requires_operational_admin(self):
        self.client.force_login(self.worker)
        with patch("credit_control.views.auto_create_collection_tasks") as automatic:
            self.assertEqual(self.client.get(self.url("tasks"),{"run_auto":"1"}).status_code,200)
            automatic.assert_not_called()
            self.assertEqual(self.client.post(self.url("tasks"),{"run_auto":"1"}).status_code,403)
            automatic.assert_not_called()

    def test_scoped_source_lists_hide_other_company_titles_and_amounts(self):
        other_customer=Customer.objects.create(name="FOREIGN_CUSTOMER_SENTINEL")
        CollectionTask.objects.create(company=self.foreign_company,customer=other_customer,task_type="FOLLOW_UP_DEBTOR",due_date=date(2030,1,7),assigned_to=self.outsider)
        PromiseToPay.objects.create(company=self.foreign_company,customer=other_customer,promised_amount=9999,promised_date=date(2030,1,7),assigned_to=self.outsider)
        self.client.force_login(self.worker)
        self.assertNotContains(self.client.get(self.url("tasks")),other_customer.name)
        self.assertNotContains(self.client.get(self.url("promises")),other_customer.name)

    def test_promise_source_page_never_offers_payment_or_status_posting(self):
        self.client.force_login(self.worker)
        result=self.client.get(self.url("promise_detail",pk=self.promise.pk))
        self.assertContains(result,"Recorded promise")
        self.assertNotContains(result,'name="actual_paid_amount"')
        self.assertNotContains(result,'name="status"')

    def test_read_only_source_capability_cannot_post_work_or_responsibility(self):
        self.worker.user_permissions.remove(*Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("change_collectiontask","change_promisetopay")))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.post(self.url("task_action",pk=self.item.pk,action="metadata"),{}).status_code,403)
        self.assertEqual(self.client.post(self.url("promise_assign",pk=self.promise.pk),{}).status_code,403)

    def test_linked_check_creation_validates_source_identity_and_responsibility(self):
        data={"company":self.company.pk,"customer":self.customer.pk,"task_type":"CHECK_PROMISE",
            "promise_to_pay":self.promise.pk,"assigned_to":self.worker.pk,"due_date":"2030-01-07",
            "priority":"MEDIUM","status":"PENDING"}
        self.assertTrue(CollectionTaskForm(data,user=self.manager).is_valid())
        for field,value in (("company",self.foreign_company.pk),("task_type","FOLLOW_UP_DEBTOR"),
                ("assigned_to",self.recipient.pk)):
            with self.subTest(field=field):
                form=CollectionTaskForm({**data,field:value},user=self.manager)
                self.assertFalse(form.is_valid())
                self.assertIn(field if field!="task_type" else "promise_to_pay",form.errors)

    def test_unassigned_source_pages_render_for_company_manager(self):
        self.item.assigned_to=None; self.item.save()
        self.promise.assigned_to=None; self.promise.save()
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(self.url("task_detail",pk=self.item.pk)),"Not assigned")
        self.assertContains(self.client.get(self.url("promise_detail",pk=self.promise.pk)),"Not assigned")

    def admin_request(self,user):
        request=RequestFactory().get("/admin/credit_control/")
        request.user=user
        return request

    def test_alternate_admin_uses_live_scope_and_source_capability(self):
        foreign=CollectionTask.objects.create(company=self.foreign_company,task_type="FOLLOW_UP_DEBTOR",
            assigned_to=self.outsider,due_date=date(2030,1,7))
        model_admin=CollectionTaskAdmin(CollectionTask,AdminSite())
        request=self.admin_request(self.worker)
        self.assertEqual(list(model_admin.get_queryset(request)),[self.item])
        self.assertTrue(model_admin.has_change_permission(request,self.item))
        self.assertFalse(model_admin.has_view_permission(request,foreign))
        self.assertFalse(model_admin.has_change_permission(request,foreign))
        self.assertFalse(model_admin.has_delete_permission(request,foreign))
        self.assertFalse(model_admin.has_view_permission(self.admin_request(self.tasker_only),self.item))

    def test_alternate_admin_forms_restrict_responsibility_and_guard_revision(self):
        model_admin=CollectionTaskAdmin(CollectionTask,AdminSite())
        request=self.admin_request(self.manager)
        form_class=model_admin.get_form(request,self.item)
        form=form_class(instance=self.item)
        self.assertIn(self.recipient,form.fields["assigned_to"].queryset)
        self.assertNotIn(self.outsider,form.fields["assigned_to"].queryset)
        self.assertNotIn(self.tasker_only,form.fields["assigned_to"].queryset)
        previous=self.item.revision
        self.item.priority="HIGH"; self.item.save()
        data={"company":self.company.pk,"customer":self.customer.pk,"task_type":"FOLLOW_UP_DEBTOR",
            "assigned_to":self.worker.pk,"due_date":"2030-01-07","priority":"MEDIUM","status":"PENDING",
            "source_revision":previous}
        stale=form_class(data,instance=self.item)
        self.assertFalse(stale.is_valid())
        self.assertIn("This source record changed",str(stale.errors))

    def test_alternate_promise_admin_assignment_syncs_canonical_check_owner(self):
        check=CollectionTask.objects.create(company=self.company,customer=self.customer,promise_to_pay=self.promise,
            task_type="CHECK_PROMISE",assigned_to=self.worker,due_date=self.promise.promised_date)
        model_admin=PromiseToPayAdmin(PromiseToPay,AdminSite())
        request=self.admin_request(self.manager)
        form_class=model_admin.get_form(request,self.promise)
        form=form_class({"company":self.company.pk,"customer":self.customer.pk,
            "assigned_to":self.recipient.pk,"promised_amount":"50","promised_date":"2030-01-07",
            "actual_paid_amount":"10","status":"OPEN","source_revision":self.promise.revision},instance=self.promise)
        self.assertTrue(form.is_valid(),form.errors)
        obj=form.save(commit=False)
        model_admin.save_model(request,obj,form,True)
        check.refresh_from_db(); self.promise.refresh_from_db()
        self.assertEqual(check.assigned_to_id,self.recipient.pk)
        self.assertEqual((self.promise.actual_paid_amount,self.promise.status),(Decimal("10"),"OPEN"))

    def test_alternate_admin_view_only_forms_render_without_write_choices(self):
        self.worker.user_permissions.remove(*Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("change_collectiontask","change_promisetopay")))
        request=self.admin_request(get_user_model().objects.get(pk=self.worker.pk))
        model_admin=CollectionTaskAdmin(CollectionTask,AdminSite())
        self.assertTrue(model_admin.has_view_permission(request,self.item))
        self.assertFalse(model_admin.has_change_permission(request,self.item))
        form_class=model_admin.get_form(request,self.item,change=True)
        form_class(instance=self.item)

    def test_promise_assignment_requires_access_to_its_existing_check(self):
        from credit_control.source_forms import PromiseAssignmentForm
        check=CollectionTask.objects.create(company=self.company,customer=self.customer,promise_to_pay=self.promise,
            task_type="CHECK_PROMISE",assigned_to=self.worker,due_date=self.promise.promised_date)
        self.recipient.user_permissions.remove(Permission.objects.get(
            content_type__app_label="credit_control",codename="view_collectiontask"))
        with self.assertRaises(ValidationError):
            assign_promise(user=self.manager,pk=self.promise.pk,assigned_to_id=self.recipient.pk,
                expected_revision=self.promise.revision,client_key=uuid.uuid4())
        check.refresh_from_db(); self.promise.refresh_from_db()
        self.assertEqual((self.promise.assigned_to_id,check.assigned_to_id),(self.worker.pk,self.worker.pk))
        self.assertFalse(SourceActionEvent.objects.exists())
        form=PromiseAssignmentForm(user=self.manager,item=self.promise)
        self.assertNotIn(self.recipient,form.fields["assigned_to"].queryset)

    def test_malformed_cross_company_promise_lineage_cannot_reassign_other_source(self):
        check=CollectionTask.objects.create(company=self.foreign_company,customer=self.customer,
            promise_to_pay=self.promise,task_type="CHECK_PROMISE",assigned_to=self.outsider,
            due_date=self.promise.promised_date)
        with self.assertRaises(SourceConflict):
            assign_promise(user=self.manager,pk=self.promise.pk,assigned_to_id=self.recipient.pk,
                expected_revision=self.promise.revision,client_key=uuid.uuid4())
        check.refresh_from_db(); self.promise.refresh_from_db()
        self.assertEqual((self.promise.assigned_to_id,check.assigned_to_id),(self.worker.pk,self.outsider.pk))
        self.assertFalse(SourceActionEvent.objects.exists())

    def test_auto_creation_keeps_standalone_promise_when_owner_cannot_read_checks(self):
        self.worker.user_permissions.remove(Permission.objects.get(
            content_type__app_label="credit_control",codename="view_collectiontask"))
        auto_create_collection_tasks(as_of_date=self.promise.promised_date)
        self.assertFalse(CollectionTask.objects.filter(promise_to_pay=self.promise).exists())
        self.assertTrue(visible_promises_for(get_user_model().objects.get(pk=self.worker.pk)).filter(pk=self.promise.pk).exists())

    def test_admin_cannot_break_existing_promise_lineage_by_changing_customer(self):
        CollectionTask.objects.create(company=self.company,customer=self.customer,promise_to_pay=self.promise,
            task_type="CHECK_PROMISE",assigned_to=self.worker,due_date=self.promise.promised_date)
        other=Customer.objects.create(name="Another synthetic source customer")
        model_admin=PromiseToPayAdmin(PromiseToPay,AdminSite())
        form_class=model_admin.get_form(self.admin_request(self.manager),self.promise)
        form=form_class({"company":self.company.pk,"customer":other.pk,
            "assigned_to":self.worker.pk,"promised_amount":"50","promised_date":"2030-01-07",
            "actual_paid_amount":"10","status":"OPEN","source_revision":self.promise.revision},instance=self.promise)
        self.assertFalse(form.is_valid())
        self.assertIn("Review linked check mappings",str(form.errors))


class CollectionSourceConcurrencyTests(TransactionTestCase):
    def setUp(self):
        self.company=Company.objects.create(name="Concurrent source company")
        self.worker=employee("concurrent-source-worker")
        self.team=Team.objects.create(company=self.company,name="Concurrent source team")
        TeamMembership.objects.create(team=self.team,user=self.worker)
        self.item=CollectionTask.objects.create(company=self.company,task_type="FOLLOW_UP_DEBTOR",
            assigned_to=self.worker,due_date=date(2030,1,7))

    def race(self,keys):
        barrier=Barrier(2)
        def act(key):
            close_old_connections()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET lock_timeout = '5s'")
                actor=get_user_model().objects.get(pk=self.worker.pk)
                barrier.wait(timeout=10)
                try:
                    change_collection_task(user=actor,pk=self.item.pk,expected_revision=self.item.revision,
                        client_key=key,action="done")
                except SourceConflict:
                    return "conflict"
                return "saved"
            finally:
                connection.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(act,keys))

    def test_simultaneous_distinct_actions_cannot_both_overwrite_source(self):
        self.assertCountEqual(self.race([uuid.uuid4(),uuid.uuid4()]),["saved","conflict"])
        self.item.refresh_from_db()
        self.assertEqual(self.item.status,"DONE")
        self.assertEqual(SourceActionEvent.objects.count(),1)

    def test_simultaneous_duplicate_actions_keep_one_source_receipt(self):
        key=uuid.uuid4()
        self.assertEqual(self.race([key,key]),["saved","saved"])
        self.assertEqual(SourceActionEvent.objects.count(),1)
