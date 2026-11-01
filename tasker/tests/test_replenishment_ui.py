"""Tasker consumes live, source-authorized inventory reviews without owning stock."""
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import Company
from inventory.models import Product, StockMovement
from inventory.replenishment_models import ReplenishmentEvent, ReplenishmentPlan
from tasker.business_adapters.replenishment import digest, snapshot
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink
from tasker.job_models import OutboxJob
from tasker.models import Task, Team, TeamMembership


def employee(name,*,tasker=True,source=True,product=True):
    user=get_user_model().objects.create_user(name,first_name=name.title())
    if tasker:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    if source:
        names=["view_replenishmentplan"]+(["view_product"] if product else [])
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="inventory",codename__in=names))
    return user


@override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=True,TASKER_COLLECTIONS_ADAPTER_ENABLED=False,
    TASKER_STRATEGY_ENABLED=False,TASKER_AI_WORKFLOW_ENABLED=False,TASKER_REPORTS_ENABLED=False,
    TASKER_COORDINATION_ENABLED=False)
class ReplenishmentBusinessUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company=Company.objects.create(name="Tasker procurement UI company")
        cls.foreign=Company.objects.create(name="FOREIGN_STOCK_COMPANY_SENTINEL")
        cls.team=Team.objects.create(company=cls.company,name="Tasker procurement UI team")
        cls.worker=employee("tasker-stock-worker")
        cls.peer=employee("tasker-stock-peer")
        cls.tasker_only=employee("tasker-stock-tasker-only",source=False)
        cls.source_only=employee("tasker-stock-source-only",tasker=False)
        cls.no_product=employee("tasker-stock-no-product",product=False)
        cls.outsider=employee("tasker-stock-outsider")
        for user in (cls.worker,cls.peer,cls.tasker_only,cls.source_only,cls.no_product):
            TeamMembership.objects.create(team=cls.team,user=user)

    def setUp(self):
        self.product=Product.objects.create(name="Sample DTF ink",sku="TASKER-REPL-INK",quantity=5,reserved=5,reorder_level=2,
            price=Decimal("765432.10"),avg_cost=Decimal("123456.78"))
        self.plan=ReplenishmentPlan.objects.create(product=self.product,company=self.company,assigned_to=self.worker,
            is_enabled=True,observed_need_state="LOW",next_review_on=date(2030,1,7),notes="PRIVATE_PROCUREMENT_PLAN_SENTINEL")
        facts=snapshot("replenishment_plan",self.plan.pk)
        self.link=BusinessRecordLink.objects.create(**facts,source_digest=digest(facts))
        self.client.force_login(self.worker)

    def url(self,name="business_list",link=None):
        kwargs={"pk":(link or self.link).pk} if name in ("business_detail","business_open_source") else {}
        return reverse("ims:tasker:"+name,kwargs=kwargs)

    def ids(self,response):
        return {item.pk for item in response.context["items"]}

    def test_replenishment_only_flag_enables_list_detail_navigation_and_source_option(self):
        result=self.client.get(self.url())
        self.assertIn(self.link.pk,self.ids(result))
        self.assertContains(result,"Stock replenishment")
        self.assertContains(result,"Sample DTF ink")
        self.assertContains(result,"Critical")
        self.assertContains(result,"Open in Inventory")
        self.assertTrue(result.context["tasker_business_available"])
        self.assertIn("replenishment_plan",dict(result.context["filter_form"].fields["source"].choices))
        self.assertNotIn("promise_to_pay",dict(result.context["filter_form"].fields["source"].choices))
        self.assertContains(self.client.get(self.url("business_detail")),"procurement responsibility")

    def test_no_source_notes_costs_supplier_contacts_or_manual_stock_controls_in_tasker(self):
        for response in (self.client.get(self.url()),self.client.get(self.url("business_detail"))):
            for private in ("PRIVATE_PROCUREMENT_PLAN_SENTINEL","765432.10","123456.78"):
                self.assertNotContains(response,private)
            for control in ('name="quantity"','name="complete"','name="review_state"'):
                self.assertNotContains(response,control)

    def test_tasker_only_and_missing_product_capability_cannot_open_source(self):
        for user in (self.tasker_only,self.no_product):
            self.client.force_login(user)
            self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
            self.assertEqual(self.client.get(self.url("business_detail")).status_code,403)
            self.assertEqual(self.client.get(self.url("business_open_source")).status_code,403)

    def test_inventory_source_capability_does_not_replace_tasker_access(self):
        self.client.force_login(self.source_only)
        self.assertEqual(self.client.get(self.url()).status_code,403)

    def test_current_company_scope_is_required_even_for_assigned_worker(self):
        TeamMembership.objects.filter(user=self.worker,team=self.team).update(is_active=False)
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.assertEqual(self.client.get(self.url("business_detail")).status_code,403)

    def test_bulk_reserved_change_immediately_clears_active_list_without_source_timestamp(self):
        updated=self.product.updated_at
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        result=self.client.get(self.url(),{"state":"closed"})
        self.assertIn(self.link.pk,self.ids(result))
        self.assertContains(result,"Replenishment need cleared")
        detail=self.client.get(self.url("business_detail"))
        self.assertContains(detail,"does not establish that a purchase was received")
        self.product.refresh_from_db()
        self.assertEqual(self.product.updated_at,updated)

    def test_renewed_shortage_reopens_existing_action_without_duplicate_task(self):
        Product.objects.filter(pk=self.product.pk).update(reserved=0)
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        Product.objects.filter(pk=self.product.pk).update(reserved=5)
        self.assertIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.assertEqual(BusinessRecordLink.objects.count(),1)
        self.assertFalse(Task.objects.exists())

    def test_invalid_or_inactive_stock_is_not_an_actionable_stockout(self):
        for values in ({"reserved":6},{"quantity":-1},{"track_inventory":False},{"is_active":False}):
            Product.objects.filter(pk=self.product.pk).update(quantity=5,reserved=5,track_inventory=True,is_active=True)
            Product.objects.filter(pk=self.product.pk).update(**values)
            self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
            self.assertEqual(self.client.get(self.url("business_detail")).status_code,200)

    def test_reassignment_uses_current_source_owner_and_hides_from_old_mine_queue(self):
        ReplenishmentPlan.objects.filter(pk=self.plan.pk).update(assigned_to=self.peer)
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.client.force_login(self.peer)
        self.assertIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.assertContains(self.client.get(self.url("business_detail")),self.peer.get_full_name())

    def test_missing_owner_stays_unassigned_and_does_not_infer_creator(self):
        ReplenishmentPlan.objects.filter(pk=self.plan.pk).update(assigned_to=None,created_by=self.worker)
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.assertContains(self.client.get(self.url(),{"scope":"visible"}),"Needs an owner")
        self.assertContains(self.client.get(self.url("business_detail")),"needs an owner in Inventory")

    def test_fixed_source_redirect_targets_ims_inventory_and_ignores_supplied_url(self):
        result=self.client.get(self.url("business_open_source"),{"next":"https://attacker.invalid/"})
        self.assertEqual(result.status_code,302)
        self.assertEqual(result["Location"],host_reverse("ims:inventory:replenishment_detail",
            kwargs={"pk":self.plan.pk},host="ims"))
        self.assertIn("no-store",result["Cache-Control"])

    def test_source_title_is_live_and_old_cached_private_title_cannot_be_exposed(self):
        BusinessRecordLink.objects.filter(pk=self.link.pk).update(title="STALE_PRIVATE_PRODUCT_TITLE")
        Product.objects.filter(pk=self.product.pk).update(name="Renamed source ink")
        result=self.client.get(self.url("business_detail"))
        self.assertContains(result,"Renamed source ink")
        self.assertNotContains(result,"STALE_PRIVATE_PRODUCT_TITLE")

    def test_foreign_source_cannot_be_revealed_by_forged_link_company(self):
        product=Product.objects.create(name="FOREIGN_PRODUCT_SENTINEL",sku="FOREIGN-TASKER-REPL")
        plan=ReplenishmentPlan.objects.create(product=product,company=self.foreign,assigned_to=self.worker,is_enabled=True)
        facts=snapshot("replenishment_plan",plan.pk)
        link=BusinessRecordLink.objects.create(**facts,source_digest=digest(facts))
        BusinessRecordLink.objects.filter(pk=link.pk).update(company=self.company)
        result=self.client.get(self.url(),{"scope":"visible","state":"all"})
        self.assertNotContains(result,"FOREIGN_PRODUCT_SENTINEL")
        self.assertNotContains(result,"FOREIGN_STOCK_COMPANY_SENTINEL")
        self.assertEqual(self.client.get(self.url("business_detail",link)).status_code,403)

    def test_get_has_no_reconciliation_or_source_stock_financial_effect(self):
        before=(list(ReplenishmentPlan.objects.values()),list(Product.objects.values()),
            ReplenishmentEvent.objects.count(),BusinessLinkEvent.objects.count(),OutboxJob.objects.count(),
            Task.objects.count(),StockMovement.objects.count())
        self.client.get(self.url());self.client.get(self.url("business_detail"));self.client.get(self.url("business_open_source"))
        after=(list(ReplenishmentPlan.objects.values()),list(Product.objects.values()),
            ReplenishmentEvent.objects.count(),BusinessLinkEvent.objects.count(),OutboxJob.objects.count(),
            Task.objects.count(),StockMovement.objects.count())
        self.assertEqual(before,after)

    def test_tasker_source_endpoints_do_not_accept_mutation_posts(self):
        for name in ("business_list","business_detail","business_open_source"):
            self.assertEqual(self.client.post(self.url(name),{"quantity":99}).status_code,405)

    def test_now_and_work_include_operational_source_without_credit_control_flag(self):
        for name in ("index","operator_home","task_list"):
            result=self.client.get(self.url(name))
            self.assertContains(result,"Operational work to move forward")
            self.assertIn(self.link.pk,{item.pk for item in result.context["business_panel"]["items"]})

    @override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=False,TASKER_COLLECTIONS_ADAPTER_ENABLED=True)
    def test_collections_flag_does_not_enable_inventory_adapter(self):
        self.assertNotIn(self.link.pk,self.ids(self.client.get(self.url())))
        self.assertEqual(self.client.get(self.url("business_detail")).status_code,403)

    @override_settings(TASKER_REPLENISHMENT_ADAPTER_ENABLED=False,TASKER_COLLECTIONS_ADAPTER_ENABLED=False)
    def test_both_disabled_hide_business_routes_and_avoid_source_queries(self):
        self.assertEqual(self.client.get(self.url()).status_code,404)
        from tasker.context_processors import tasker_ui
        request=RequestFactory().get("/tasks/")
        request.user=get_user_model().objects.get(pk=self.worker.pk)
        request.resolver_match=SimpleNamespace(app_names=["tasker"],url_name="task_list")
        with patch("tasker.services.business_links.business_actions_for") as build:
            with CaptureQueriesContext(connection) as queries:
                context=tasker_ui(request)
        build.assert_not_called()
        self.assertFalse(context["tasker_business_available"])
        tables=("inventory_replenishmentplan","inventory_product","tasker_businessrecordlink")
        self.assertFalse(any(table in row["sql"] for row in queries for table in tables))
