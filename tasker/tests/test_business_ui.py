"""Source-authorized operational UI keeps ownership and financial evidence canonical."""
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django_hosts.resolvers import reverse as host_reverse

from accounting.models import Company
from credit_control.models import CollectionTask, PromiseToPay, SourceActionEvent
from customers.models import Customer
from tasker.business_adapters.collections import digest, snapshot
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink
from tasker.job_models import OutboxJob
from tasker.models import Task, Team, TeamMembership
from tasker.services.business_links import business_actions_for


DAY = date(2030, 1, 7)


def employee(name, *, tasker=True, credit=True):
    user = get_user_model().objects.create_user(name, first_name=name.title())
    if tasker:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    if credit:
        user.user_permissions.add(*Permission.objects.filter(content_type__app_label="credit_control",
            codename__in=("view_collectiontask", "view_promisetopay")))
    return user


def link_for(source, kind="collection_task"):
    facts = snapshot(kind, source.pk)
    return BusinessRecordLink.objects.create(**facts, source_digest=digest(facts))


@override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=True, TASKER_DIRECTION_ENABLED=True,
    TASKER_AI_WORKFLOW_ENABLED=False, TASKER_STRATEGY_ENABLED=False,
    TASKER_REPORTS_ENABLED=False, TASKER_COORDINATION_ENABLED=False)
class BusinessActionUITests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.worker = employee("business-ui-worker")
        cls.peer = employee("business-ui-peer")
        cls.tasker_only = employee("business-ui-tasker-only", credit=False)
        cls.credit_only = employee("business-ui-credit-only", tasker=False)
        cls.outsider = employee("business-ui-outsider")
        cls.company = Company.objects.create(name="Business source company")
        cls.foreign_company = Company.objects.create(name="UNREADABLE_COMPANY_SENTINEL")
        cls.team = Team.objects.create(company=cls.company, name="Collections team")
        for user in (cls.worker, cls.peer, cls.tasker_only, cls.credit_only):
            TeamMembership.objects.create(team=cls.team, user=user)
        cls.customer = Customer.objects.create(name="PRIVATE_CUSTOMER_SENTINEL",
            email="private-contact@example.test", phone="PRIVATE_PHONE_SENTINEL")

    def setUp(self):
        self.source = CollectionTask.objects.create(company=self.company, customer=self.customer,
            task_type="FOLLOW_UP_DEBTOR", assigned_to=self.worker, created_by=self.peer,
            due_date=DAY, priority="URGENT", notes="PRIVATE_COLLECTION_NOTES_SENTINEL")
        self.link = link_for(self.source)
        self.promise = PromiseToPay.objects.create(company=self.company, customer=self.customer,
            promised_amount=Decimal("765432.10"), actual_paid_amount=Decimal("123456.78"),
            promised_date=DAY, assigned_to=self.worker, notes="PRIVATE_PROMISE_NOTES_SENTINEL")
        self.promise_link = link_for(self.promise, "promise_to_pay")
        self.client.force_login(self.worker)

    def url(self, name="business_list", **kwargs):
        return reverse("ims:tasker:"+name, kwargs=kwargs)

    def item_url(self, name="business_detail", link=None):
        return self.url(name, pk=(link or self.link).pk)

    def item_ids(self, response):
        return {item.pk for item in response.context["items"]}

    def test_readable_list_and_detail_show_live_responsibility_and_safe_metadata(self):
        listing = self.client.get(self.url())
        self.assertEqual(self.item_ids(listing), {self.link.pk, self.promise_link.pk})
        self.assertEqual(listing.context["items"][0].pk, self.link.pk)
        for response in (listing, self.client.get(self.item_url())):
            self.assertContains(response, "Debtor follow-up")
            self.assertContains(response, self.worker.get_full_name())
            self.assertContains(response, "Critical")
            self.assertContains(response, "07 Jan 2030")
            self.assertContains(response, "Open at source")
            self.assertContains(response, "snapshot")
            self.assertIn("no-store", response["Cache-Control"])
        self.assertContains(self.client.get(self.item_url()), "does not establish that a payment was received")

    def test_tasker_pages_copy_no_customer_contacts_notes_or_financial_amounts(self):
        sentinels = ("PRIVATE_CUSTOMER_SENTINEL", "private-contact@example.test", "PRIVATE_PHONE_SENTINEL",
            "PRIVATE_COLLECTION_NOTES_SENTINEL", "PRIVATE_PROMISE_NOTES_SENTINEL", "765432.10", "123456.78")
        for response in (self.client.get(self.url()), self.client.get(self.item_url()),
                self.client.get(self.item_url(link=self.promise_link))):
            for value in sentinels:
                self.assertNotContains(response, value)

    def test_tasker_permission_does_not_grant_credit_control_content(self):
        self.client.force_login(self.tasker_only)
        response = self.client.get(self.url(), {"scope": "visible", "state": "all"})
        self.assertEqual(self.item_ids(response), set())
        for view in ("business_detail", "business_open_source"):
            self.assertEqual(self.client.get(self.item_url(view)).status_code, 403)

    def test_credit_control_read_permission_does_not_replace_tasker_permission(self):
        self.client.force_login(self.credit_only)
        for url in (self.url(), self.item_url(), self.item_url("business_open_source")):
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_anonymous_requests_require_login(self):
        self.client.logout()
        for url in (self.url(), self.item_url(), self.item_url("business_open_source")):
            result = self.client.get(url)
            self.assertEqual(result.status_code, 302)
            self.assertIn("login", result["Location"])

    def test_source_company_boundary_is_rechecked_even_for_a_forged_link(self):
        foreign = CollectionTask.objects.create(company=self.foreign_company, assigned_to=self.worker,
            task_type="FOLLOW_UP_DEBTOR", due_date=DAY)
        foreign_link = link_for(foreign)
        BusinessRecordLink.objects.filter(pk=foreign_link.pk).update(company=self.company, owner=self.worker,
            title="FORGED_PRIVATE_LINK_TITLE")
        response = self.client.get(self.url(), {"scope": "visible", "state": "all"})
        self.assertNotIn(foreign_link.pk, self.item_ids(response))
        for value in ("UNREADABLE_COMPANY_SENTINEL", "FORGED_PRIVATE_LINK_TITLE"):
            self.assertNotContains(response, value)
        self.assertEqual(self.client.get(self.item_url(link=foreign_link)).status_code, 403)
        self.assertEqual(self.client.get(self.item_url("business_open_source", foreign_link)).status_code, 403)

    def test_revoked_membership_removes_assigned_actions_before_worker_refresh(self):
        TeamMembership.objects.filter(team=self.team, user=self.worker).update(is_active=False)
        response = self.client.get(self.url())
        self.assertEqual(self.item_ids(response), set())
        self.assertEqual(self.client.get(self.item_url()).status_code, 403)
        self.assertEqual(self.client.get(self.item_url("business_open_source")).status_code, 403)

    def test_revoked_source_permission_is_checked_at_each_get(self):
        result = self.client.get(self.item_url())
        self.assertEqual(result.status_code, 200)
        self.worker.user_permissions.remove(*Permission.objects.filter(content_type__app_label="credit_control"))
        self.assertEqual(self.client.get(self.item_url()).status_code, 403)
        self.assertEqual(self.client.get(self.item_url("business_open_source")).status_code, 403)

    def test_stored_projection_title_owner_and_priority_are_not_presentation_authority(self):
        BusinessRecordLink.objects.filter(pk=self.link.pk).update(title="PRIVATE_STALE_TITLE_SENTINEL",
            owner=self.peer, priority="LOW", status="COMPLETED")
        response = self.client.get(self.url())
        self.assertIn(self.link.pk, self.item_ids(response))
        item = next(item for item in response.context["items"] if item.pk == self.link.pk)
        self.assertEqual((item.title, item.owner_id, item.priority, item.status),
            ("Debtor follow-up", self.worker.pk, "CRITICAL", "OPEN"))
        self.assertNotContains(response, "PRIVATE_STALE_TITLE_SENTINEL")
        self.assertNotContains(self.client.get(self.item_url()), "PRIVATE_STALE_TITLE_SENTINEL")

    def test_mine_filter_follows_current_source_assignment_not_saved_owner(self):
        self.source.assigned_to = self.peer
        self.source.save()
        self.assertNotIn(self.link.pk, self.item_ids(self.client.get(self.url())))
        visible = self.client.get(self.url(), {"scope": "visible"})
        item = next(item for item in visible.context["items"] if item.pk == self.link.pk)
        self.assertEqual(item.owner_id, self.peer.pk)
        self.client.force_login(self.peer)
        self.assertIn(self.link.pk, self.item_ids(self.client.get(self.url())))

    def test_personal_panel_drops_reassignment_between_selection_and_live_presentation(self):
        from tasker.services import business_links
        original = business_links.present_business_link

        def move_before_presentation(*, user, link):
            if link.pk == self.link.pk:
                # Legacy bulk updates may skip revision/save hooks. The live
                # copy still must belong to this actor before it is queued.
                CollectionTask.objects.filter(pk=self.source.pk).update(assigned_to=self.peer)
            return original(user=user, link=link)

        with patch("tasker.services.business_links.present_business_link", side_effect=move_before_presentation):
            panel = business_links.business_actions_for(self.worker, limit=5)
        self.assertNotIn(self.link.pk, {row.pk for row in panel["items"]})

    def test_personal_list_drops_reassignment_after_sql_page_selection(self):
        from tasker.views import business
        original = business._present
        def move(*, user, link):
            if link.pk == self.link.pk:
                CollectionTask.objects.filter(pk=self.source.pk).update(assigned_to=self.peer)
            return original(user, link)
        with patch("tasker.views.business._present", side_effect=lambda user, link: move(user=user, link=link)):
            response = self.client.get(self.url())
        self.assertNotIn(self.link.pk, self.item_ids(response))

    def test_active_list_drops_completion_after_sql_page_selection(self):
        from tasker.views import business
        original = business._present
        def close(user, link):
            if link.pk == self.link.pk:
                CollectionTask.objects.filter(pk=self.source.pk).update(status="DONE")
            return original(user, link)
        with patch("tasker.views.business._present", side_effect=close):
            response = self.client.get(self.url())
        self.assertNotIn(self.link.pk, self.item_ids(response))

    def test_unassigned_source_has_no_creator_fallback_and_appears_in_readable_view(self):
        self.source.assigned_to = None
        self.source.save()
        self.assertNotIn(self.link.pk, self.item_ids(self.client.get(self.url())))
        response = self.client.get(self.url(), {"scope": "visible"})
        item = next(item for item in response.context["items"] if item.pk == self.link.pk)
        self.assertIsNone(item.owner)
        self.assertEqual(item.status, "UNASSIGNED")
        self.assertContains(response, "Needs an owner")
        self.assertContains(self.client.get(self.item_url()), "needs an owner in Credit Control")

    def test_closed_and_reopened_source_filters_change_without_background_reconcile(self):
        self.source.status = "DONE"
        self.source.save()
        self.assertNotIn(self.link.pk, self.item_ids(self.client.get(self.url())))
        result = self.client.get(self.url(), {"state": "closed"})
        self.assertIn(self.link.pk, self.item_ids(result))
        self.assertContains(result, "Completed at source")
        self.source.status = "PENDING"
        self.source.save()
        self.assertIn(self.link.pk, self.item_ids(self.client.get(self.url())))
        self.assertNotIn(self.link.pk, self.item_ids(self.client.get(self.url(), {"state": "closed"})))

    def test_source_filter_does_not_mix_two_workflows(self):
        result = self.client.get(self.url(), {"source": "promise_to_pay"})
        self.assertEqual(self.item_ids(result), {self.promise_link.pk})

    def test_known_promise_check_is_one_action_even_after_check_completion(self):
        self.source.task_type = "CHECK_PROMISE"
        self.source.promise_to_pay = self.promise
        self.source.save()
        self.assertEqual(self.item_ids(self.client.get(self.url())), {self.link.pk})
        self.source.status = "DONE"
        self.source.save()
        self.assertEqual(self.item_ids(self.client.get(self.url())), set())
        history = self.client.get(self.url(), {"state": "closed"})
        self.assertEqual(self.item_ids(history), {self.link.pk, self.promise_link.pk})
        self.assertContains(history, "Closed or covered by source work")

    def test_invalid_filters_are_explained_and_cannot_supply_a_source_url(self):
        result = self.client.get(self.url(), {"state": "all-money", "scope": "everyone",
            "source": "https://attacker.invalid"})
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "Choose valid filters", status_code=400)
        self.assertTrue(result.context["filter_form"].errors)
        self.assertEqual(self.item_ids(result), {self.link.pk, self.promise_link.pk})

    def test_source_open_redirects_to_canonical_ims_host_and_ignores_next_url(self):
        for link, source_route, source in ((self.link, "task_detail", self.source),
                (self.promise_link, "promise_detail", self.promise)):
            result = self.client.get(self.item_url("business_open_source", link),
                {"next": "https://attacker.invalid", "url": "https://attacker.invalid"})
            expected = host_reverse("ims:credit_control:"+source_route, kwargs={"pk": source.pk}, host="ims")
            self.assertEqual(result.status_code, 302)
            self.assertEqual(result["Location"], expected)
            self.assertEqual(urlparse(result["Location"]).hostname, "ims.boforg.co.zw")
            self.assertIn("no-store", result["Cache-Control"])

    def test_business_pages_resolve_on_ai_host_without_credit_routes_in_its_urlconf(self):
        url = reverse("ims:tasker:business_detail", kwargs={"pk": self.link.pk}, urlconf="config.host_urls.ai")
        result = self.client.get(url, HTTP_HOST="ai.boforg.co.zw")
        self.assertEqual(result.status_code, 200)
        self.assertContains(result, "Debtor follow-up")

    def test_all_ui_endpoints_are_read_only_gets_and_have_no_completion_form(self):
        for url in (self.url(), self.item_url(), self.item_url("business_open_source")):
            self.assertEqual(self.client.post(url, {"status": "DONE"}).status_code, 405)
        detail = self.client.get(self.item_url()).content.decode()
        self.assertNotIn('name="status"', detail)
        self.assertNotIn('name="complete"', detail)

    def test_get_does_not_reconcile_enqueue_or_mutate_source_or_native_tasks(self):
        sources_before = (list(CollectionTask.objects.values()), list(PromiseToPay.objects.values()))
        links_before = list(BusinessRecordLink.objects.values())
        counts_before = (Task.objects.count(), OutboxJob.objects.count(), BusinessLinkEvent.objects.count(),
            SourceActionEvent.objects.count())
        for url in (self.url(), self.item_url(), self.item_url("business_open_source")):
            self.assertIn(self.client.get(url).status_code, (200, 302))
        self.assertEqual(sources_before, (list(CollectionTask.objects.values()), list(PromiseToPay.objects.values())))
        self.assertEqual(links_before, list(BusinessRecordLink.objects.values()))
        self.assertEqual(counts_before, (Task.objects.count(), OutboxJob.objects.count(),
            BusinessLinkEvent.objects.count(), SourceActionEvent.objects.count()))

    def test_pagination_is_bounded_and_uses_current_canonical_priority(self):
        for index in range(25):
            source = CollectionTask.objects.create(company=self.company, assigned_to=self.worker,
                task_type="FOLLOW_UP_DEBTOR", priority="LOW", due_date=DAY-timedelta(days=10))
            link_for(source)
        # A delayed link snapshot must not outrank a critical source action.
        BusinessRecordLink.objects.filter(pk=self.link.pk).update(priority="LOW", due_date=DAY+timedelta(days=90))
        result = self.client.get(self.url())
        self.assertEqual(len(result.context["items"]), 24)
        self.assertEqual(result.context["page_obj"].paginator.count, 27)
        self.assertEqual(result.context["items"][0].pk, self.link.pk)
        self.assertContains(result, "Next")
        self.assertEqual(len(self.client.get(self.url(), {"page": 2}).context["items"]), 3)

    def test_business_panel_is_explicit_and_source_owned_without_native_completion_controls(self):
        panel = business_actions_for(self.worker, limit=1)
        html = render_to_string("tasker/business/panel.html", {
            "tasker_collections_adapter_enabled": True, "business_panel": panel})
        self.assertIn("Operational work to move forward", html)
        self.assertIn("These actions belong to Credit Control", html)
        self.assertIn("Showing 1 of 2", html)
        self.assertIn(self.item_url(), html)
        self.assertNotIn('name="complete"', html)
        self.assertNotIn("PRIVATE_CUSTOMER_SENTINEL", html)
        self.assertEqual(render_to_string("tasker/business/panel.html", {
            "tasker_collections_adapter_enabled": False, "business_panel": panel}).strip(), "")

    def test_now_and_work_embed_five_item_current_source_panel(self):
        for name in ("index", "operator_home", "task_list"):
            result = self.client.get(self.url(name))
            self.assertContains(result, "Operational work to move forward")
            self.assertEqual({row.pk for row in result.context["business_panel"]["items"]},
                {self.link.pk, self.promise_link.pk})
            self.assertTrue(result.context["tasker_business_available"])

    def test_empty_native_operator_points_to_existing_operational_work(self):
        html=render_to_string("tasker/operator/panel.html", {
            "operator":{"capacity":{"timezone_name":"Africa/Harare"},"now":None},
            "business_panel":{"items":[self.link]}})
        self.assertIn("Review your business actions",html)
        self.assertIn("Your assigned operational work",html)
        self.assertNotIn("No work passed",html)
        self.assertNotIn("operator/start/",html)

    def test_navigation_requires_source_read_capability_and_marks_business_active(self):
        result = self.client.get(self.url())
        entry = next(row for row in result.context["tasker_primary_navigation"] if row["key"] == "business")
        self.assertTrue(entry["active"])
        self.assertEqual(entry["url"], self.url())
        self.client.force_login(self.tasker_only)
        result = self.client.get(self.url())
        self.assertNotIn("business", {row["key"] for row in result.context["tasker_primary_navigation"]})

    @override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False)
    def test_disabled_rollout_does_not_read_source_tables_or_build_business_panel(self):
        from tasker.context_processors import tasker_ui
        request = RequestFactory().get("/tasks/")
        request.user = get_user_model().objects.get(pk=self.worker.pk)
        request.resolver_match = SimpleNamespace(app_names=["tasker"], url_name="task_list")
        with patch("tasker.services.business_links.business_actions_for") as build_panel:
            with CaptureQueriesContext(connection) as queries:
                context = tasker_ui(request)
        build_panel.assert_not_called()
        self.assertNotIn("business_panel", context)
        self.assertFalse(context["tasker_business_available"])
        source_tables = ("credit_control_collectiontask", "credit_control_promisetopay", "tasker_businessrecordlink")
        self.assertFalse(any(table in row["sql"] for row in queries for table in source_tables))

    @override_settings(TASKER_COLLECTIONS_ADAPTER_ENABLED=False)
    def test_disabled_rollout_hides_all_business_routes(self):
        for url in (self.url(), self.item_url(), self.item_url("business_open_source")):
            self.assertEqual(self.client.get(url).status_code, 404)
