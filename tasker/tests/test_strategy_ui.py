"""Scoped, accessible Strategy screens and explicit preview/commit workflows."""
from datetime import datetime, time, timedelta, timezone as dt_timezone
from unittest.mock import patch
import uuid

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from accounting.models import Company
from tasker.models import Task, Team, TeamMembership
from tasker.planning_models import WorkPolicy
from tasker.scenario_models import StrategyScenario
from tasker.strategy_models import WorkDeclaration
from tasker.services.scenarios import create_scenario


NOW = datetime(2030, 1, 7, 8, tzinfo=dt_timezone.utc)


@override_settings(TASKER_STRATEGY_ENABLED=True, TASKER_DIRECTION_ENABLED=True,
    TASKER_AI_WORKFLOW_ENABLED=False, TASKER_COORDINATION_ENABLED=True, TASKER_REPORTS_ENABLED=True)
class StrategyUITests(TestCase):
    def setUp(self):
        self.clock = patch("django.utils.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        users = get_user_model()
        self.manager = users.objects.create_user("strategy-ui-manager", first_name="Sample manager")
        self.employee = users.objects.create_user("strategy-ui-employee", first_name="Sample employee")
        self.recipient = users.objects.create_user("strategy-ui-recipient", first_name="Sample recipient")
        self.outsider = users.objects.create_user("strategy-ui-outsider")
        for user in (self.manager, self.employee, self.recipient, self.outsider):
            user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        self.company = Company.objects.create(name="UI strategy company")
        self.foreign_company = Company.objects.create(name="FOREIGN_COMPANY_SENTINEL")
        self.team = Team.objects.create(company=self.company, name="Sample sales team")
        self.foreign_team = Team.objects.create(company=self.foreign_company, name="FOREIGN_TEAM_SENTINEL")
        TeamMembership.objects.create(team=self.team, user=self.manager, role="MANAGER")
        TeamMembership.objects.create(team=self.team, user=self.employee)
        TeamMembership.objects.create(team=self.team, user=self.recipient)
        TeamMembership.objects.create(team=self.foreign_team, user=self.outsider, role="MANAGER")
        self.task = Task.objects.create(title="Readable customer work", owner=self.employee,
            company=self.company, team=self.team, priority="CRITICAL", estimated_duration=timedelta(minutes=30),
            description="PRIVATE_DESCRIPTION_SENTINEL", notes="PRIVATE_WORK_NOTES_SENTINEL")
        self.other_task = Task.objects.create(title="Next readable task", owner=self.employee,
            company=self.company, team=self.team, estimated_duration=timedelta(minutes=20))
        self.foreign_task = Task.objects.create(title="FOREIGN_TASK_SENTINEL", owner=self.outsider,
            company=self.foreign_company, team=self.foreign_team)
        self.personal = Task.objects.create(title="EMPLOYEE_PERSONAL_TASK_SENTINEL", owner=self.employee)
        for user in (self.employee, self.recipient):
            WorkPolicy.objects.create(owner=user, configured=True, timezone_name="Africa/Harare",
                weekdays=[0, 1, 2, 3, 4], start_time=time(8), end_time=time(17), buffer_minutes=0)
        self.client.force_login(self.manager)

    def url(self, name, **kwargs):
        return reverse("ims:tasker:"+name, kwargs=kwargs)

    def post_data(self, **changes):
        data = {"team": self.team.pk, "title": "Move customer work carefully", "client_key": uuid.uuid4(),
            "changes-TOTAL_FORMS": "1", "changes-INITIAL_FORMS": "0", "changes-MIN_NUM_FORMS": "0",
            "changes-MAX_NUM_FORMS": "10", "changes-0-task": self.task.pk,
            "changes-0-owner": self.recipient.pk, "changes-0-priority": "CRITICAL",
            "changes-0-due_date": "2030-01-08", "changes-0-due_time": "16:00"}
        data.update(changes)
        return data

    def scenario(self):
        return create_scenario(user=self.manager, team_id=self.team.pk, title="Reviewed synthetic scenario",
            client_key=uuid.uuid4(), changes=[{"task_id": self.task.pk, "owner_id": self.recipient.pk,
                "priority": "CRITICAL", "due_date": "2030-01-08", "due_time": "16:00"}])

    def test_manager_board_excludes_private_and_foreign_content(self):
        response = self.client.get(self.url("strategy_board"))
        self.assertContains(response, "Readable customer work")
        for value in ("FOREIGN_TASK_SENTINEL", "FOREIGN_TEAM_SENTINEL", "FOREIGN_COMPANY_SENTINEL",
                "PRIVATE_DESCRIPTION_SENTINEL", "PRIVATE_WORK_NOTES_SENTINEL", "EMPLOYEE_PERSONAL_TASK_SENTINEL"):
            self.assertNotContains(response, value)
        self.assertContains(response, "No work confirmation recorded")
        self.assertContains(response, "no employee ranking")
        self.assertIn("no-store", response["Cache-Control"])

    def test_employee_board_has_own_lane_and_no_coworker_work(self):
        Task.objects.create(title="COWORKER_TASK_SENTINEL", owner=self.recipient, team=self.team, company=self.company)
        self.client.force_login(self.employee)
        response = self.client.get(self.url("strategy_board"))
        self.assertContains(response, "Readable customer work")
        self.assertContains(response, "Confirm my current and next work")
        self.assertNotContains(response, "COWORKER_TASK_SENTINEL")
        self.assertNotContains(response, "Try a what-if scenario")

    def test_all_three_views_share_the_read_boundary(self):
        for view, phrase in (("execution", "People and execution lanes"),
                ("dependencies", "Where work depends"), ("capacity", "Committed time")):
            result = self.client.get(self.url("strategy_board"), {"view": view})
            self.assertContains(result, phrase)
            self.assertNotContains(result, "FOREIGN_TASK_SENTINEL")

    def test_forged_team_filter_is_rejected_without_foreign_data(self):
        result = self.client.get(self.url("strategy_board"), {"team": self.foreign_team.pk})
        self.assertEqual(result.status_code, 400)
        self.assertNotContains(result, "FOREIGN_TASK_SENTINEL", status_code=400)

    def test_invalid_date_retains_the_filter_error(self):
        result = self.client.get(self.url("strategy_board"), {"day": "tomorrow", "view": "capacity"})
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "Enter a valid date", status_code=400)
        self.assertEqual(self.client.get(self.url("strategy_board"), {"day": "2300-01-01"}).status_code, 400)

    def test_self_declaration_is_post_only_and_explicit(self):
        self.client.force_login(self.employee)
        prefix = "declare_"+str(self.team.pk)+"-"
        self.assertEqual(self.client.get(self.url("strategy_declare")).status_code, 405)
        result = self.client.post(self.url("strategy_declare"), {"team_id": self.team.pk,
            prefix+"team": self.team.pk, prefix+"current_task": self.task.pk,
            prefix+"next_task": self.other_task.pk, prefix+"expected_revision": "none"})
        self.assertEqual(result.status_code, 302)
        declaration = WorkDeclaration.objects.get(user=self.employee, team=self.team)
        self.assertEqual(declaration.current_task_id, self.task.pk)
        board = self.client.get(self.url("strategy_board"))
        self.assertContains(board, "Last work confirmation")

    def test_stale_declaration_retains_choices_and_returns_conflict(self):
        from tasker.services.strategy import declare_work
        declare_work(user=self.employee, team_id=self.team.pk, current_task_id=self.task.pk)
        self.client.force_login(self.employee)
        prefix = "declare_"+str(self.team.pk)+"-"
        result = self.client.post(self.url("strategy_declare"), {"team_id": self.team.pk,
            prefix+"team": self.team.pk, prefix+"current_task": self.other_task.pk,
            prefix+"expected_revision": "none"})
        self.assertEqual(result.status_code, 409)
        self.assertContains(result, "Your declaration was not saved", status_code=409)
        self.assertEqual(WorkDeclaration.objects.get(user=self.employee).current_task_id, self.task.pk)

    def test_declaration_cannot_select_another_employees_task(self):
        self.client.force_login(self.recipient)
        prefix = "declare_"+str(self.team.pk)+"-"
        result = self.client.post(self.url("strategy_declare"), {"team_id": self.team.pk,
            prefix+"team": self.team.pk, prefix+"current_task": self.task.pk, prefix+"expected_revision": "none"})
        self.assertEqual(result.status_code, 400)
        self.assertFalse(WorkDeclaration.objects.filter(user=self.recipient).exists())

    def test_scenario_form_get_is_read_only_and_scoped(self):
        result = self.client.get(self.url("scenario_create"), {"team": self.team.pk})
        self.assertContains(result, "Readable customer work")
        self.assertContains(result, "Create preview")
        self.assertNotContains(result, "FOREIGN_TASK_SENTINEL")
        self.assertFalse(StrategyScenario.objects.exists())

    def test_preview_post_does_not_move_live_task_and_replays_once(self):
        data = self.post_data()
        result = self.client.post(self.url("scenario_create"), data)
        self.assertEqual(result.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.employee.pk)
        self.assertEqual(self.client.post(self.url("scenario_create"), data)["Location"], result["Location"])
        self.assertEqual(StrategyScenario.objects.count(), 1)
        detail = self.client.get(result["Location"])
        self.assertContains(detail, "Commit these reviewed changes")
        self.assertContains(detail, "seven-day window")

    def test_invalid_preview_retains_name_and_typed_values(self):
        result = self.client.post(self.url("scenario_create"), self.post_data(**{"changes-0-due_time": "25:00"}))
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "Move customer work carefully", status_code=400)
        self.assertContains(result, "25:00", status_code=400)
        self.assertFalse(StrategyScenario.objects.exists())

    def test_duplicate_task_rows_and_forged_owners_are_rejected(self):
        result = self.client.post(self.url("scenario_create"), self.post_data(**{"changes-0-owner": self.outsider.pk}))
        self.assertEqual(result.status_code, 400)
        data = self.post_data(**{"changes-TOTAL_FORMS": "2"})
        for name in ("task", "owner", "priority", "due_date", "due_time"):
            data["changes-1-"+name] = data["changes-0-"+name]
        result = self.client.post(self.url("scenario_create"), data)
        self.assertEqual(result.status_code, 400)
        self.assertContains(result, "only once", status_code=400)

    def test_scenario_detail_and_actions_are_owner_private(self):
        item = self.scenario()
        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url("scenario_detail", pk=item.pk)).status_code, 403)
        self.assertEqual(self.client.post(self.url("scenario_commit", pk=item.pk), {"expected_revision": item.revision}).status_code, 403)

    def test_history_hides_draft_title_after_source_work_moves_out_of_scope(self):
        item = self.scenario()
        Task.objects.filter(pk=self.task.pk).update(owner=self.outsider, team=self.foreign_team, company=self.foreign_company)
        result = self.client.get(self.url("scenario_list"))
        self.assertEqual(result.status_code, 200)
        self.assertNotContains(result, item.title)
        self.assertEqual(self.client.get(self.url("scenario_detail", pk=item.pk)).status_code, 403)

    def test_commit_is_post_only_and_rechecks_revision(self):
        item = self.scenario()
        self.assertEqual(self.client.get(self.url("scenario_commit", pk=item.pk)).status_code, 405)
        result = self.client.post(self.url("scenario_commit", pk=item.pk), {"expected_revision": uuid.uuid4()})
        self.assertEqual(result.status_code, 409)
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.employee.pk)

    def test_explicit_commit_and_replay_change_work_once(self):
        item = self.scenario()
        result = self.client.post(self.url("scenario_commit", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 302)
        self.task.refresh_from_db()
        self.assertEqual(self.task.owner_id, self.recipient.pk)
        again = self.client.post(self.url("scenario_commit", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(again.status_code, 302)
        self.assertEqual(item.events.filter(action="COMMITTED").count(), 1)

    def test_discard_does_not_change_work(self):
        item = self.scenario()
        result = self.client.post(self.url("scenario_discard", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 302)
        item.refresh_from_db()
        self.task.refresh_from_db()
        self.assertEqual(item.state, "DISCARDED")
        self.assertEqual(self.task.owner_id, self.employee.pk)

    def test_owner_can_discard_with_rollout_off(self):
        item = self.scenario()
        with override_settings(TASKER_STRATEGY_ENABLED=False):
            result = self.client.post(self.url("scenario_discard", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], self.url("index"))
        item.refresh_from_db()
        self.assertEqual(item.state, "DISCARDED")

    def test_owner_can_discard_after_management_revocation_without_preview_leak(self):
        item = self.scenario()
        TeamMembership.objects.filter(team=self.team, user=self.manager).update(role="EMPLOYEE")
        result = self.client.post(self.url("scenario_discard", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 302)
        self.assertEqual(result["Location"], self.url("index"))
        item.refresh_from_db()
        self.assertEqual(item.state, "DISCARDED")

    def test_foreign_owner_cannot_discard_with_rollout_off(self):
        item = self.scenario()
        self.client.force_login(self.outsider)
        with override_settings(TASKER_STRATEGY_ENABLED=False):
            result = self.client.post(self.url("scenario_discard", pk=item.pk), {"expected_revision": item.revision})
        self.assertEqual(result.status_code, 403)

    def test_csrf_is_required_and_rollout_flag_is_respected(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.manager)
        self.assertEqual(client.post(self.url("scenario_create"), self.post_data()).status_code, 403)
        with override_settings(TASKER_STRATEGY_ENABLED=False):
            self.assertEqual(self.client.get(self.url("strategy_board")).status_code, 404)
            self.assertEqual(self.client.get(self.url("scenario_create")).status_code, 404)
