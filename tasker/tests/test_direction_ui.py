from datetime import timedelta
from decimal import Decimal

from django.contrib import admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management import call_command
from django.test import TestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone

from accounting.models import Company
from tasker.models import (
    BaselineObservation, BaselineStudy, Goal, Initiative, KeyResult,
    OrganizationAccess, OrganizationEvent, OutcomeMeasurement, Task,
    Team, TeamMembership,
)


class DirectionPageTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("setup_tasker_roles", verbosity=0)
        cls.company = Company.objects.create(name="UI sample company")
        cls.foreign = Company.objects.create(name="Foreign UI company")
        users = get_user_model()
        cls.manager = users.objects.create_user("direction-manager", is_staff=True)
        cls.employee = users.objects.create_user("direction-employee")
        cls.other = users.objects.create_user("direction-other")
        cls.admin = users.objects.create_superuser("direction-admin", "", "test-pass")
        cls.manager.groups.add(Group.objects.get(name="Tasker Supervisor"))
        cls.employee.groups.add(Group.objects.get(name="Tasker Employee"))
        cls.other.groups.add(Group.objects.get(name="Tasker Employee"))
        cls.team = Team.objects.create(company=cls.company, name="Delivery UI")
        cls.foreign_team = Team.objects.create(company=cls.foreign, name="Foreign UI")
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.team, user=cls.employee)
        TeamMembership.objects.create(team=cls.foreign_team, user=cls.other)
        cls.goal = Goal.objects.create(company=cls.company, team=cls.team, title="Delivery goal", status="ACTIVE", evidence_policy="Approved delivery document")
        cls.kr = KeyResult.objects.create(goal=cls.goal, title="Accepted deliveries", unit="orders", baseline=None, target=100)
        cls.initiative = Initiative.objects.create(goal=cls.goal, key_result=cls.kr, title="Fulfilment")
        cls.foreign_goal = Goal.objects.create(company=cls.foreign, team=cls.foreign_team, title="Restricted goal")
        today = timezone.localdate()
        cls.study = BaselineStudy.objects.create(company=cls.company, team=cls.team, name="Delivery baseline", role="Fulfilment", workload_type="Standard delivery", output_unit="orders", output_definition="Accepted deliveries", acceptance_criteria="Approved document", effort_definition="Staffed roster hours", quality_definition="Accepted first pass; rework separately", source_definition="Approved ledger", comparison_plan="Staggered cohort", start_date=today-timedelta(days=7), end_date=today+timedelta(days=2), pilot_start_date=today+timedelta(days=3), status="COLLECTING")
        cls.task = Task.objects.create(company=cls.company, team=cls.team, title="UI shared task", owner=cls.employee, assigned_by=cls.manager)

    def url(self, name, **kwargs):
        return reverse(f"ims:tasker:{name}", kwargs=kwargs)

    def test_basic_pages_render_for_scoped_manager(self):
        self.client.force_login(self.manager)
        for name, kwargs in (("team_list", {}), ("team_detail", {"pk": self.team.pk}), ("goal_list", {}), ("goal_detail", {"pk": self.goal.pk}), ("baseline_list", {}), ("baseline_detail", {"pk": self.study.pk}), ("goal_create", {}), ("key_result_create", {}), ("initiative_create", {}), ("outcome_create", {}), ("baseline_create", {}), ("observation_create", {}), ("contribution_edit", {"slug": self.task.slug})):
            with self.subTest(page=name):
                self.assertEqual(self.client.get(self.url(name, **kwargs)).status_code, 200)

    def test_unknown_baseline_is_visible(self):
        self.client.force_login(self.employee)
        response = self.client.get(self.url("goal_detail", pk=self.goal.pk))
        self.assertContains(response, "Baseline Unknown")
        self.assertContains(response, "Progress: Unknown")

    def test_foreign_goals_teams_and_exports_are_inaccessible(self):
        self.client.force_login(self.other)
        for name, kwargs in (("team_detail", {"pk": self.team.pk}), ("goal_detail", {"pk": self.goal.pk}), ("baseline_detail", {"pk": self.study.pk}), ("baseline_export", {"pk": self.study.pk})):
            self.assertEqual(self.client.get(self.url(name, **kwargs)).status_code, 404)

    def test_revoked_manager_cannot_reuse_goal_form(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url("goal_edit", pk=self.goal.pk)).status_code, 200)
        TeamMembership.objects.filter(pk=self.manager_membership.pk).update(is_active=False)
        response = self.client.post(self.url("goal_edit", pk=self.goal.pk), {"title": "Forged"})
        self.assertEqual(response.status_code, 404)
        self.goal.refresh_from_db()
        self.assertEqual(self.goal.title, "Delivery goal")

    def test_goal_post_rejects_forged_company_and_preserves_draft_values(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url("goal_create"), {"company": self.foreign.pk, "team": self.foreign_team.pk, "title": "Preserved draft", "status": "DRAFT"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Changes have not been saved")
        self.assertContains(response, "Preserved draft")
        self.assertFalse(Goal.objects.filter(title="Preserved draft").exists())

    def test_manager_can_create_team_goal_with_unknown_fields(self):
        self.client.force_login(self.manager)
        response = self.client.post(self.url("goal_create"), {"company": self.company.pk, "team": self.team.pk, "title": "New team goal", "status": "DRAFT"})
        self.assertEqual(response.status_code, 302)
        goal = Goal.objects.get(title="New team goal")
        self.assertIsNone(goal.owner_id)
        self.assertIsNone(goal.end_date)

    def test_outcome_roundtrip_employee_records_manager_accepts_retracts(self):
        self.client.force_login(self.employee)
        response = self.client.post(self.url("outcome_create"), {"company": self.company.pk, "team": self.team.pk, "key_result": self.kr.pk, "employee": self.employee.pk, "occurred_on": timezone.localdate(), "value": "2", "source_type": "manual", "source_key": "UI-DELIVERY-1", "evidence": "Approved source"})
        self.assertEqual(response.status_code, 302, getattr(response, "context", None))
        outcome = OutcomeMeasurement.objects.get(source_key="UI-DELIVERY-1")
        self.assertEqual(self.client.post(self.url("outcome_action", pk=outcome.pk, action="accept")).status_code, 403)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url("outcome_action", pk=outcome.pk, action="accept")).status_code, 302)
        outcome.refresh_from_db()
        self.assertEqual(outcome.status, "ACCEPTED")
        self.assertEqual(self.client.post(self.url("outcome_action", pk=outcome.pk, action="retract"), {"reason": "Source corrected"}).status_code, 302)
        outcome.refresh_from_db()
        self.assertEqual(outcome.status, "RETRACTED")

    def test_company_goal_shows_own_team_manager_acceptance_controls(self):
        goal = Goal.objects.create(company=self.company, title="Shared direction", status="ACTIVE", evidence_policy="Approved source")
        kr = KeyResult.objects.create(goal=goal, title="Total output", unit="orders", baseline=0, target=20)
        OutcomeMeasurement.objects.create(company=self.company, team=self.team, key_result=kr, employee=self.employee, occurred_on=timezone.localdate(), value=2, source_key="company-ui-outcome", evidence="Approved source")
        self.client.force_login(self.manager)
        response = self.client.get(self.url("goal_detail", pk=goal.pk))
        self.assertContains(response, "Accept evidence")
        self.assertNotContains(response, ">Edit goal<")

    def test_baseline_roundtrip_and_export_preserves_unknown(self):
        self.client.force_login(self.employee)
        response = self.client.post(self.url("observation_create"), {"study": self.study.pk, "employee": self.employee.pk, "observed_on": timezone.localdate(), "source_key": "UI-ROSTER-1", "evidence": "Signed source"})
        self.assertEqual(response.status_code, 302)
        observation = BaselineObservation.objects.get(source_key="UI-ROSTER-1")
        self.assertEqual(observation.role, self.study.role)
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url("observation_action", pk=observation.pk, action="accept")).status_code, 302)
        response = self.client.get(self.url("baseline_export", pk=self.study.pk))
        self.assertContains(response, "Unknown")
        self.assertContains(response, "UI-ROSTER-1")

    def test_roster_change_requires_company_scope_and_independent_capability(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url("membership_create"), {"team": self.team.pk, "user": self.other.pk, "role": "EMPLOYEE", "is_active": True}).status_code, 403)
        self.assertFalse(TeamMembership.objects.filter(team=self.team, user=self.other).exists())
        OrganizationAccess.objects.create(company=self.company, user=self.manager, can_manage=True)
        self.manager.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="manage_organization"))
        self.assertEqual(self.client.post(self.url("team_create"), {"company": self.company.pk, "name": "Forbidden team"}).status_code, 403)
        self.assertFalse(Team.objects.filter(name="Forbidden team").exists())

    def test_employee_cannot_open_roster_editor_or_enumerate_other_company_users(self):
        self.client.force_login(self.employee)
        response = self.client.get(self.url("membership_create"))
        self.assertEqual(response.status_code, 403)
        self.assertNotContains(response, self.other.username, status_code=403)

    def test_admin_can_change_roster_with_audit_and_report_grants(self):
        self.client.force_login(self.admin)
        response = self.client.post(self.url("membership_create"), {"team": self.team.pk, "user": self.other.pk, "role": "EMPLOYEE", "is_active": True})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrganizationEvent.objects.filter(object_type="teammembership", actor=self.admin).exists())
        response = self.client.post(self.url("organization_access_create"), {"company": self.company.pk, "user": self.manager.pk, "is_active": True, "can_receive_reports": True})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(OrganizationAccess.objects.get(company=self.company, user=self.manager).can_receive_reports)

    def test_agreed_business_drafts_are_explicit_idempotent_and_keep_unknowns(self):
        self.client.force_login(self.admin)
        url = self.url("business_goal_drafts")
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertEqual(self.client.post(url, {"company": "invalid"}).status_code, 404)
        self.assertEqual(self.client.post(url, {"company": self.company.pk}).status_code, 302)
        sales_goal = Goal.objects.get(company=self.company, reference="BIZ-G01")
        self.assertIsNone(sales_goal.owner_id)
        self.assertIsNone(sales_goal.start_date)
        self.assertIsNone(sales_goal.end_date)
        self.assertEqual(sales_goal.status, "DRAFT")
        result = sales_goal.key_results.get()
        self.assertEqual(result.unit, "USD")
        self.assertEqual(result.target, Decimal("2000000"))
        self.assertIsNone(result.baseline)
        sales_goal.title = "Owner edited title"
        sales_goal.save()
        self.client.post(url, {"company": self.company.pk})
        self.assertEqual(Goal.objects.filter(company=self.company, reference__startswith="BIZ-").count(), 3)
        sales_goal.refresh_from_db()
        self.assertEqual(sales_goal.title, "Owner edited title")

    def test_business_drafts_do_not_grant_team_manager_company_authority(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.post(self.url("business_goal_drafts"), {"company": self.company.pk}).status_code, 404)
        self.assertFalse(Goal.objects.filter(reference="BIZ-G01").exists())

    def test_staff_admin_lists_scope_work_and_disable_unvalidated_writes(self):
        own_private = Task.objects.create(title="Private employee work", owner=self.employee)
        foreign = Task.objects.create(title="Foreign shared work", owner=self.other, company=self.foreign, team=self.foreign_team)
        request = RequestFactory().get("/")
        request.user = self.manager
        model_admin = admin.site._registry[Task]
        visible_ids = set(model_admin.get_queryset(request).values_list("pk", flat=True))
        self.assertIn(self.task.pk, visible_ids)
        self.assertNotIn(own_private.pk, visible_ids)
        self.assertNotIn(foreign.pk, visible_ids)
        self.assertFalse(model_admin.has_change_permission(request, self.task))
        self.assertEqual(model_admin.get_actions(request), {})
        self.assertFalse(admin.site._registry[OutcomeMeasurement].has_change_permission(request))

    def test_privileged_admin_reassignment_closes_old_owners_timer(self):
        from tasker.services.focus import start_focus
        entry = start_focus(task=self.task, user=self.employee)
        request = RequestFactory().post("/admin/")
        request.user = self.admin
        self.task.owner = self.manager
        admin.site._registry[Task].save_model(request, self.task, None, change=True)
        entry.refresh_from_db()
        self.assertIsNotNone(entry.ended_at)
        self.assertTrue(OrganizationEvent.objects.filter(action="task.admin_scope_changed", object_id=str(self.task.pk)).exists())

    @override_settings(TASKER_DIRECTION_ENABLED=False)
    def test_direction_feature_flag_hides_routes_and_links(self):
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(self.url("goal_list")).status_code, 404)
        self.assertNotContains(self.client.get(self.url("index")), ">Goals</a>")
