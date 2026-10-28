"""Tasker shell navigation, permission boundaries and IMS isolation."""
from html.parser import HTMLParser

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
from django.contrib.staticfiles import finders
from django.urls import reverse

from accounting.models import Company
from tasker.models import (
    BaselineStudy, DailyPlan, DailyReview, Goal, OrganizationAccess, Task,
    Team, TeamMembership,
)


class ShellLinks(HTMLParser):
    """Inspect semantic navigation without depending on template whitespace."""
    def __init__(self, html):
        super().__init__()
        self.links = []
        self.current = None
        self.feed(html)

    def handle_starttag(self, tag, attributes):
        if tag == "a":
            self.current = {**dict(attributes), "text": ""}

    def handle_data(self, data):
        if self.current is not None:
            self.current["text"] += data

    def handle_endtag(self, tag):
        if tag == "a" and self.current is not None:
            self.current["text"] = " ".join(self.current["text"].split())
            self.links.append(self.current)
            self.current = None

    @property
    def primary(self):
        return [link for link in self.links if "tasker-nav-link" in link.get("class", "").split()]


class TaskerShellTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.employee = users.objects.create_user("shell-worker")
        cls.manager = users.objects.create_user("shell-manager")
        cls.capability_only = users.objects.create_user("shell-capability-only")
        cls.executive = users.objects.create_user("shell-executive")
        all_permissions = list(Permission.objects.filter(content_type__app_label="tasker"))
        for user in (cls.manager, cls.capability_only, cls.executive):
            user.user_permissions.add(*all_permissions)
        cls.employee.user_permissions.add(*Permission.objects.filter(
            content_type__app_label="tasker",
            codename__in=("view_task", "add_task", "change_task", "view_dailyplan", "view_goal", "view_team", "view_baselinestudy"),
        ))
        cls.company = Company.objects.create(name="Shell company")
        cls.foreign_company = Company.objects.create(name="Shell foreign company")
        cls.team = Team.objects.create(company=cls.company, name="Shell visible team")
        cls.other_team = Team.objects.create(company=cls.company, name="Shell restricted team")
        cls.foreign_team = Team.objects.create(company=cls.foreign_company, name="Shell foreign team")
        cls.membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.team, user=cls.employee)
        OrganizationAccess.objects.create(company=cls.company, user=cls.executive, can_manage=True, can_view_all=True)
        cls.goal = Goal.objects.create(company=cls.company, team=cls.team, title="Shell visible goal")
        cls.foreign_goal = Goal.objects.create(company=cls.foreign_company, team=cls.foreign_team, title="Shell foreign goal")
        BaselineStudy.objects.create(company=cls.company, team=cls.team, name="Shell visible baseline")
        BaselineStudy.objects.create(company=cls.company, team=cls.other_team, name="Shell restricted baseline")
        Task.objects.create(owner=cls.employee, title="Shell private work", notes="Shell private task notes")
        plan = DailyPlan.objects.create(owner=cls.employee, quick_notes="Shell private capture")
        DailyReview.objects.create(plan=plan, lessons_learned="Shell private reflection")

    def setUp(self):
        self.client.force_login(self.employee)

    def url(self, name):
        return reverse(f"ims:tasker:{name}")

    def primary(self, response):
        return ShellLinks(response.content.decode()).primary

    def test_employee_primary_navigation_only_offers_available_destinations(self):
        response = self.client.get(self.url("index"))
        links = self.primary(response)
        destinations = {link["text"]: link.get("href") for link in links}
        self.assertEqual(destinations.get("Now"), self.url("index"))
        self.assertEqual(destinations.get("Plan"), self.url("daily_plan"))
        self.assertEqual(destinations.get("Work"), self.url("task_list"))
        self.assertEqual(destinations.get("Goals"), self.url("goal_list"))
        self.assertNotIn("Strategy", destinations)
        self.assertEqual(destinations.get("Inbox"), self.url("capture_list"))
        self.assertFalse(any("arrival" in link.get("href", "") for link in links))

    def test_primary_navigation_marks_current_destination(self):
        response = self.client.get(self.url("task_list"))
        active = [link for link in self.primary(response) if link.get("aria-current") == "page"]
        self.assertEqual([(link["text"], link.get("href")) for link in active], [("Work", self.url("task_list"))])

    def test_new_task_form_defaults_its_owner_to_current_user(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url("task_create"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(str(response.context["form"]["owner"].value()), str(self.manager.pk))

    def test_plan_is_not_offered_without_its_capability(self):
        self.employee.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="view_dailyplan"))
        response = self.client.get(self.url("index"))
        self.assertNotIn("Plan", {link["text"] for link in self.primary(response)})

    def test_goals_navigation_requires_its_own_view_capability(self):
        self.employee.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="view_goal"))
        response = self.client.get(self.url("index"))
        self.assertNotIn("Goals", {link["text"] for link in self.primary(response)})

    @override_settings(ROOT_URLCONF="config.host_urls.ai")
    def test_tasker_navigation_stays_on_current_ai_host_in_local_preview(self):
        response = self.client.get(self.url("index"), HTTP_HOST="127.0.0.1:8765")
        self.assertEqual(response.status_code, 200)
        for link in self.primary(response):
            self.assertTrue(link.get("href", "").startswith("/"), link)
            self.assertFalse(link["href"].startswith("//"), link)
        self.assertEqual(self.url("index"), "/")

    def test_new_tasker_assets_and_preferences_do_not_leak_into_ims(self):
        self.employee.user_permissions.add(Permission.objects.get(content_type__app_label="sales", codename="view_invoice"))
        tasker = self.client.get(self.url("index"))
        ims = self.client.get(reverse("ims:dashboard"))
        self.assertEqual(ims.status_code, 200)
        for asset in ("tasker/css/shell.css", "tasker/js/ui_preferences.js", "tasker/js/ui.js"):
            self.assertContains(tasker, asset)
            self.assertNotContains(ims, asset)
        self.assertNotContains(ims, 'data-tasker-user=')
        self.assertNotContains(ims, 'class="tasker-mobile-nav"')
        html = tasker.content.decode()
        self.assertLess(html.index("tasker/js/ui_preferences.js"), html.index("tasker/css/ui.css"))
        self.assertLess(html.index("tasker/css/dashboard.css"), html.index("tasker/css/shell.css"))

    def test_tasker_controls_do_not_depend_on_external_bootstrap_script(self):
        response = self.client.get(self.url("index"))
        self.assertNotContains(response, "https://cdn.jsdelivr.net")
        for asset in ("tasker/vendor/bootstrap/css/bootstrap.min.css", "tasker/vendor/bootstrap/js/bootstrap.bundle.min.js", "tasker/vendor/bootstrap-icons/font/bootstrap-icons.css", "tasker/js/ui_preferences.js", "tasker/css/shell.css"):
            self.assertContains(response, asset)
            self.assertIsNotNone(finders.find(asset), asset)

    def test_preferences_page_has_explicit_theme_and_shortcut_controls(self):
        response = self.client.get(self.url("ui_preferences"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, f'data-tasker-user="{self.employee.pk}"')
        self.assertContains(response, 'id="taskerPreferencesForm"', count=1)
        for name in ("theme", "shortcuts_enabled", "shortcut_capture", "shortcut_create", "shortcut_now", "shortcut_plan", "shortcut_work", "shortcut_goals", "shortcut_search", "shortcut_help"):
            self.assertContains(response, f'name="{name}"')
        for theme in ("system", "light", "dark"):
            self.assertContains(response, f'value="{theme}"')
        self.assertContains(response, 'id="taskerPreferenceStatus"')
        self.assertEqual(self.client.post(self.url("ui_preferences"), {"theme": "light"}).status_code, 405)

    def test_preferences_require_login_and_tasker_access(self):
        self.client.logout()
        response = self.client.get(self.url("ui_preferences"))
        self.assertEqual(response.status_code, 302)
        unauthorized = get_user_model().objects.create_user("shell-no-capabilities")
        self.client.force_login(unauthorized)
        self.assertEqual(self.client.get(self.url("ui_preferences")).status_code, 403)

    def test_strategy_requires_real_scope_not_global_role_capabilities(self):
        self.client.force_login(self.capability_only)
        response = self.client.get(self.url("index"))
        self.assertNotIn("Strategy", {link["text"] for link in self.primary(response)})
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 403)
        self.client.force_login(self.employee)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 403)

    def test_strategy_visible_to_live_manager_without_disclosing_other_teams(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url("index"))
        self.assertIn("Strategy", {link["text"] for link in self.primary(response)})
        strategy = self.client.get(self.url("strategy_home"))
        self.assertEqual(strategy.status_code, 200)
        self.assertEqual(self.client.post(self.url("strategy_home")).status_code, 405)
        self.assertContains(strategy, self.team.name)
        for secret in (self.other_team.name, self.foreign_team.name, self.foreign_company.name, "Shell restricted baseline", "Shell private work", "Shell private task notes", "Shell private capture", "Shell private reflection"):
            self.assertNotContains(strategy, secret)

    def test_revoked_membership_removes_strategy_navigation_and_direct_access(self):
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 200)
        TeamMembership.objects.filter(pk=self.membership.pk).update(is_active=False)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 403)
        self.assertNotIn("Strategy", {link["text"] for link in self.primary(self.client.get(self.url("index")))})

    def test_manager_membership_also_requires_assignment_capability(self):
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="assign_task"))
        self.client.force_login(self.manager)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 403)

    def test_explicit_company_management_grant_allows_only_its_company(self):
        self.client.force_login(self.executive)
        response = self.client.get(self.url("strategy_home"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.team.name)
        self.assertContains(response, self.other_team.name)
        self.assertNotContains(response, self.foreign_team.name)
        OrganizationAccess.objects.filter(user=self.executive).update(is_active=False)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 403)

    @override_settings(TASKER_DIRECTION_ENABLED=False)
    def test_direction_flag_hides_unavailable_goals_and_strategy(self):
        self.client.force_login(self.manager)
        response = self.client.get(self.url("index"))
        names = {link["text"] for link in self.primary(response)}
        self.assertNotIn("Goals", names)
        self.assertNotIn("Strategy", names)
        self.assertEqual(self.client.get(self.url("strategy_home")).status_code, 404)
        self.assertEqual(self.client.get(self.url("ui_preferences")).status_code, 200)
