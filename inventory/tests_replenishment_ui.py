"""Canonical procurement review UI authority, stock proofs and draft retention."""
from datetime import date
from decimal import Decimal
import uuid

from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.test import Client, RequestFactory, TestCase
from django.urls import reverse

from accounting.models import Company, JournalEntry
from inventory.models import Product, StockMovement
from inventory.replenishment_models import ReplenishmentEvent, ReplenishmentPlan
from inventory.replenishment_admin import ReplenishmentPlanAdmin
from inventory.services.replenishment import product_stock_version
from tasker.models import OrganizationAccess, Team, TeamMembership


def employee(name, *, source=True, change=True, manager=False):
    user=get_user_model().objects.create_user(name)
    if source:
        names=["view_product","view_replenishmentplan"]
        if change:
            names.append("change_replenishmentplan")
        if manager:
            names.append("manage_replenishment")
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="inventory",codename__in=names))
    return user


class ReplenishmentSourceUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name="Replenishment source UI company")
        cls.foreign=Company.objects.create(name="FOREIGN_REPLENISHMENT_COMPANY")
        cls.team=Team.objects.create(company=cls.company,name="Procurement UI team")
        cls.worker=employee("replenishment-ui-worker")
        cls.peer=employee("replenishment-ui-peer")
        cls.readonly=employee("replenishment-ui-readonly",change=False)
        cls.manager=employee("replenishment-ui-manager",manager=True)
        cls.outsider=employee("replenishment-ui-outsider")
        cls.tasker_only=employee("replenishment-ui-tasker-only",source=False)
        cls.tasker_only.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        for user in (cls.worker,cls.peer,cls.readonly):
            TeamMembership.objects.create(team=cls.team,user=user)
        OrganizationAccess.objects.create(company=cls.company,user=cls.manager,can_manage=True)

    def setUp(self):
        self.product=Product.objects.create(name="UI sample ink",sku="REPLENISHMENT-UI-INK",
            quantity=10,reserved=8,reorder_level=4,price=Decimal("765432.10"))
        self.plan=ReplenishmentPlan.objects.create(product=self.product,company=self.company,
            assigned_to=self.worker,is_enabled=True,observed_need_state="LOW",notes="PRIVATE_STANDING_PROCUREMENT_NOTES")
        self.client.force_login(self.worker)

    def url(self,name="replenishment_detail",**kwargs):
        return reverse("ims:inventory:"+name,kwargs=kwargs or ({"pk":self.plan.pk} if name in (
            "replenishment_detail","replenishment_action","replenishment_configure_existing") else {}))

    def progress(self,**changes):
        data={"expected_revision":str(self.plan.revision),"expected_stock_version":product_stock_version(self.product),
            "client_key":str(uuid.uuid4()),"review_state":"REVIEWING","next_review_on":"2030-01-07",
            "notes":"Source review draft"}
        data.update(changes)
        return data

    def mapping(self,**changes):
        data={"expected_revision":str(self.plan.revision),"client_key":str(uuid.uuid4()),
            "product":str(self.product.pk),"company":str(self.company.pk),"assigned_to":str(self.peer.pk),
            "is_enabled":"on","next_review_on":"2030-01-08","notes":self.plan.notes}
        data.update(changes)
        return data

    def test_low_stock_list_puts_stockouts_before_partial_shortages(self):
        product=Product.objects.create(name="ZZ urgent paper",sku="REPLENISHMENT-UI-STOCKOUT",
            quantity=1,reserved=1,reorder_level=0)
        urgent=ReplenishmentPlan.objects.create(product=product,company=self.company,
            assigned_to=self.worker,is_enabled=True)
        response=self.client.get(self.url("replenishment_list"))
        self.assertEqual(response.context["items"][0].pk,urgent.pk)
        self.assertEqual(response.context["items"][1].pk,self.plan.pk)

    def test_readable_source_shows_current_reservation_adjusted_stock_and_owner(self):
        result=self.client.get(self.url())
        self.assertContains(result,"UI sample ink")
        self.assertEqual(result.context["item"].current["available"],2)
        self.assertContains(result,"replenishment-ui-worker")
        self.assertContains(result,"Save procurement review")
        self.assertContains(result,"PRIVATE_STANDING_PROCUREMENT_NOTES")
        self.assertIn("no-store",result["Cache-Control"])

    def test_source_gets_do_not_write_observations_decisions_or_stock(self):
        before=(list(ReplenishmentPlan.objects.values()),ReplenishmentEvent.objects.count(),
            list(Product.objects.values()),StockMovement.objects.count(),JournalEntry.objects.count())
        self.client.get(self.url())
        self.client.get(self.url("replenishment_list"))
        after=(list(ReplenishmentPlan.objects.values()),ReplenishmentEvent.objects.count(),
            list(Product.objects.values()),StockMovement.objects.count(),JournalEntry.objects.count())
        self.assertEqual(before,after)

    def test_tasker_role_alone_does_not_grant_inventory_review_access(self):
        self.client.force_login(self.tasker_only)
        self.assertEqual(self.client.get(self.url()).status_code,403)
        self.assertEqual(self.client.get(self.url("replenishment_list")).status_code,403)

    def test_source_capability_without_company_scope_cannot_read_or_configure(self):
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url()).status_code,403)
        result=self.client.get(self.url("replenishment_list"))
        self.assertNotContains(result,self.product.name)
        self.assertEqual(self.client.get(self.url("replenishment_configure")).status_code,403)

    def test_staff_product_access_does_not_substitute_for_replenishment_permission(self):
        user=employee("replenishment-staff-only",source=False)
        user.groups.add(Group.objects.get_or_create(name="Staff")[0])
        TeamMembership.objects.create(team=self.team,user=user)
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_revoked_membership_removes_source_even_if_still_assigned(self):
        TeamMembership.objects.filter(team=self.team,user=self.worker).update(is_active=False)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_read_only_source_user_cannot_submit_a_review(self):
        self.client.force_login(self.readonly)
        result=self.client.get(self.url())
        self.assertContains(result,"PRIVATE_STANDING_PROCUREMENT_NOTES")
        self.assertNotContains(result,"Save procurement review")
        self.assertEqual(self.client.post(self.url("replenishment_action"),self.progress()).status_code,403)

    def test_worker_records_next_step_without_stock_or_financial_writes(self):
        before=(StockMovement.objects.count(),JournalEntry.objects.count(),self.product.quantity,self.product.reserved)
        result=self.client.post(self.url("replenishment_action"),self.progress())
        self.assertRedirects(result,self.url())
        self.plan.refresh_from_db();self.product.refresh_from_db()
        self.assertEqual((self.plan.review_state,self.plan.next_review_on),("REVIEWING",date(2030,1,7)))
        self.assertEqual(before,(StockMovement.objects.count(),JournalEntry.objects.count(),self.product.quantity,self.product.reserved))

    def test_repeated_review_submission_keeps_one_decision_receipt(self):
        data=self.progress()
        self.assertEqual(self.client.post(self.url("replenishment_action"),data).status_code,302)
        events=ReplenishmentEvent.objects.count()
        self.assertEqual(self.client.post(self.url("replenishment_action"),data).status_code,302)
        self.assertEqual(ReplenishmentEvent.objects.count(),events)

    def test_stale_stock_proof_retains_draft_and_rejects_progress(self):
        data=self.progress(notes="KEEP_THIS_SOURCE_DRAFT")
        Product.objects.filter(pk=self.product.pk).update(reserved=7)
        result=self.client.post(self.url("replenishment_action"),data)
        self.assertContains(result,"KEEP_THIS_SOURCE_DRAFT",status_code=409)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.review_state,"NEEDS_REVIEW")

    def test_cleared_need_retains_unsaved_draft_without_review_submission_control(self):
        data=self.progress(notes="KEEP_CLEARED_SOURCE_DRAFT")
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        result=self.client.post(self.url("replenishment_action"),data)
        self.assertContains(result,"KEEP_CLEARED_SOURCE_DRAFT",status_code=409)
        self.assertContains(result,"Unsaved review draft",status_code=409)
        self.assertContains(result,'aria-label="Unsaved procurement review draft"',status_code=409)
        self.assertNotContains(result,"Save procurement review",status_code=409)
        self.assertNotContains(result,f'action="{self.url("replenishment_action")}"',status_code=409)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.notes,"PRIVATE_STANDING_PROCUREMENT_NOTES")
        self.assertEqual(self.plan.review_state,"NEEDS_REVIEW")

    def test_paused_need_retains_unsaved_draft_without_review_submission_control(self):
        data=self.progress(notes="KEEP_PAUSED_SOURCE_DRAFT")
        self.plan.is_enabled=False;self.plan.save(update_fields=("is_enabled",))
        result=self.client.post(self.url("replenishment_action"),data)
        self.assertContains(result,"KEEP_PAUSED_SOURCE_DRAFT",status_code=409)
        self.assertContains(result,"Unsaved review draft",status_code=409)
        self.assertNotContains(result,"Save procurement review",status_code=409)
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_enabled)
        self.assertEqual(self.plan.notes,"PRIVATE_STANDING_PROCUREMENT_NOTES")

    def test_forged_financial_or_completion_fields_are_rejected(self):
        for field in ("quantity","actual_paid_amount","complete","assigned_to"):
            result=self.client.post(self.url("replenishment_action"),self.progress(**{field:"1"}))
            self.assertEqual(result.status_code,400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.review_state,"NEEDS_REVIEW")

    def test_source_post_requires_csrf(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.worker)
        self.assertEqual(client.post(self.url("replenishment_action"),self.progress()).status_code,403)

    def test_bulk_reservation_release_clears_live_need_before_worker_runs(self):
        previous=self.product.updated_at
        Product.objects.filter(pk=self.product.pk).update(reserved=2)
        result=self.client.get(self.url())
        self.assertContains(result,"Replenishment need cleared")
        self.assertNotContains(result,"Save procurement review")
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at,previous)
        self.assertEqual(self.client.get(self.url("replenishment_list")).context["items"],[])
        cleared=self.client.get(self.url("replenishment_list"),{"state":"clear"})
        self.assertEqual([item.pk for item in cleared.context["items"]],[self.plan.pk])

    def test_invalid_stock_is_visible_for_review_without_progress_controls(self):
        Product.objects.filter(pk=self.product.pk).update(reserved=11)
        result=self.client.get(self.url())
        self.assertContains(result,"Stock or reservation records are inconsistent")
        self.assertNotContains(result,"Save procurement review")
        self.assertEqual(self.client.get(self.url("replenishment_list"),{"state":"all"}).context["items"][0].current["status"],"UNAVAILABLE")

    def test_paused_review_has_no_progress_form(self):
        self.plan.is_enabled=False;self.plan.save()
        result=self.client.get(self.url())
        self.assertContains(result,"standing review is paused")
        self.assertNotContains(result,"Save procurement review")

    def test_manager_company_chooser_populates_current_eligible_owners(self):
        self.client.force_login(self.manager)
        result=self.client.get(self.url("replenishment_configure_existing"),{"company":self.company.pk})
        self.assertEqual(result.status_code,200)
        self.assertIn(self.peer,result.context["form"].fields["assigned_to"].queryset)
        self.assertNotIn(self.outsider,result.context["form"].fields["assigned_to"].queryset)
        self.assertNotIn(self.readonly,result.context["form"].fields["assigned_to"].queryset)

    def test_manager_reassigns_existing_responsibility_without_changing_product(self):
        self.client.force_login(self.manager)
        result=self.client.post(self.url("replenishment_configure_existing"),self.mapping())
        self.assertRedirects(result,self.url())
        self.plan.refresh_from_db()
        self.assertEqual((self.plan.assigned_to_id,self.plan.product_id),(self.peer.pk,self.product.pk))

    def test_worker_cannot_edit_company_mapping_or_delegate(self):
        self.assertEqual(self.client.get(self.url("replenishment_configure_existing")).status_code,403)
        self.assertEqual(self.client.post(self.url("replenishment_configure_existing"),self.mapping()).status_code,403)

    def test_foreign_company_configuration_is_rejected_and_draft_retained(self):
        self.client.force_login(self.manager)
        result=self.client.post(self.url("replenishment_configure_existing"),self.mapping(
            company=str(self.foreign.pk),notes="KEEP_MAPPING_DRAFT"))
        self.assertContains(result,"KEEP_MAPPING_DRAFT",status_code=400)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.company_id,self.company.pk)

    def test_new_product_configuration_requires_explicit_company_and_owner_choice(self):
        self.client.force_login(self.manager)
        product=Product.objects.create(name="New configured toner",sku="REPL-NEW-CONFIG",quantity=0,reorder_level=0)
        result=self.client.get(self.url("replenishment_configure"),{"company":self.company.pk})
        self.assertTrue(result.context["show_form"])
        result=self.client.post(self.url("replenishment_configure"),self.mapping(
            product=str(product.pk),expected_revision="new",assigned_to=""))
        self.assertEqual(result.status_code,302)
        plan=ReplenishmentPlan.objects.get(product=product)
        self.assertIsNone(plan.assigned_to_id)
        self.assertEqual(plan.company_id,self.company.pk)

    def test_canonical_admin_is_scoped_and_cannot_bypass_source_mutations(self):
        foreign_product=Product.objects.create(name="FOREIGN_PROCUREMENT_PRODUCT",sku="FOREIGN-PROCUREMENT")
        foreign=ReplenishmentPlan.objects.create(product=foreign_product,company=self.foreign,is_enabled=True)
        model_admin=ReplenishmentPlanAdmin(ReplenishmentPlan,AdminSite())
        request=RequestFactory().get("/django-admin/");request.user=self.worker
        self.assertEqual(list(model_admin.get_queryset(request)),[self.plan])
        self.assertFalse(model_admin.has_view_permission(request,foreign))
        self.assertFalse(model_admin.has_change_permission(request,self.plan))
        self.assertFalse(model_admin.has_add_permission(request))
        self.assertFalse(model_admin.has_delete_permission(request,self.plan))

    def test_replaying_new_configuration_reaches_original_receipt_after_product_is_mapped(self):
        self.client.force_login(self.manager)
        product=Product.objects.create(name="Retry source product",sku="REPL-NEW-RETRY",quantity=0,reorder_level=0)
        data=self.mapping(product=str(product.pk),expected_revision="new")
        url=self.url("replenishment_configure")
        self.assertEqual(self.client.post(url,data).status_code,302)
        count=ReplenishmentEvent.objects.count()
        self.assertEqual(self.client.post(url,data).status_code,302)
        self.assertEqual(ReplenishmentPlan.objects.filter(product=product).count(),1)
        self.assertEqual(ReplenishmentEvent.objects.count(),count)

    def test_invalid_list_filters_are_explained_instead_of_crashing(self):
        result=self.client.get(self.url("replenishment_list"),{"state":"every-company","scope":"public"})
        self.assertContains(result,"Choose a supported replenishment view",status_code=400)

    def test_existing_low_stock_catalogue_handles_valid_near_int32_counts(self):
        low=Product.objects.create(name="High-count low stock",sku="REPL-HIGH-LOW",
            quantity=2147483647,reserved=2147483647,reorder_level=2147483647)
        clear=Product.objects.create(name="High-count sufficient stock",sku="REPL-HIGH-CLEAR",
            quantity=2147483647,reserved=0,reorder_level=2147483646)
        result=self.client.get(reverse("ims:inventory:product_list"),{"low":"1"})
        self.assertEqual(result.status_code,200)
        products={product.pk for product in result.context["products"]}
        self.assertIn(low.pk,products)
        self.assertNotIn(clear.pk,products)

    def test_company_setup_preserves_valid_preselected_unmapped_product(self):
        self.client.force_login(self.manager)
        product=Product.objects.create(name="Preselected toner",sku="REPL-PRESELECTED",quantity=0)
        url=self.url("replenishment_configure")
        result=self.client.get(url,{"product":product.pk})
        self.assertEqual(result.context["preselected_product"],product.pk)
        self.assertContains(result,f'<input type="hidden" name="product" value="{product.pk}">',html=True)
        result=self.client.get(url,{"product":product.pk,"company":self.company.pk})
        self.assertEqual(result.context["form"].initial["product"],product.pk)
        self.assertEqual(result.context["selected_company"],self.company)

    def test_company_setup_does_not_echo_unvalidated_product_parameter(self):
        self.client.force_login(self.manager)
        result=self.client.get(self.url("replenishment_configure"),{"product":"<script>UNSAFE_PRODUCT</script>"})
        self.assertIsNone(result.context["preselected_product"])
        self.assertNotContains(result,"UNSAFE_PRODUCT")
