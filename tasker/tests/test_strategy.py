"""Scope, factual reconciliation, explicit declarations and bounded board reads."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, time, timedelta, timezone as datetime_timezone
import json
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext

from accounting.models import Company
from tasker.arrival_models import ArrivalDeclaration
from tasker.goal_models import Goal, Initiative, TaskContribution
from tasker.coordination_models import CoordinationRequest
from tasker.dependency_models import TaskDependency
from tasker.models import DailyPlan, DailyReview, OrganizationAccess, Task, Team, TeamMembership
from tasker.planning_models import Availability, ScheduleBlock, WorkPolicy
from tasker.report_models import EndDaySubmission, ManagerReportSnapshot
from tasker.services.focus import block_task
from tasker.services.strategy import (DeclarationConflict, build_strategy_board, declare_work, strategy_teams_for)
from tasker.strategy_models import WorkDeclaration, WorkDeclarationEvent


DAY = date(2030, 1, 7)
NOW = datetime(2030, 1, 7, 8, tzinfo=datetime_timezone.utc)


def employee(name):
    user = get_user_model().objects.create_user(name)
    user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
    return user


@override_settings(TASKER_STRATEGY_ENABLED=True)
class StrategyBoardTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.manager, cls.worker, cls.other, cls.executive = [employee(name) for name in
            ("strategy-manager", "strategy-worker", "strategy-other", "strategy-executive")]
        cls.company = Company.objects.create(name="Strategy company")
        cls.foreign_company = Company.objects.create(name="Foreign strategy company")
        cls.team = Team.objects.create(company=cls.company, name="Equipment")
        cls.other_team = Team.objects.create(company=cls.company, name="Other team")
        cls.foreign_team = Team.objects.create(company=cls.foreign_company, name="Foreign team")
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.team, user=cls.worker)
        TeamMembership.objects.create(team=cls.team, user=cls.other)
        TeamMembership.objects.create(team=cls.other_team, user=cls.other)

    def setUp(self):
        self.clock = patch("django.utils.timezone.now", return_value=NOW)
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.task = Task.objects.create(owner=self.worker, team=self.team, company=self.company,
            title="Shared equipment follow-up", estimated_duration=timedelta(minutes=30))
        self.next_task = Task.objects.create(owner=self.worker, team=self.team, company=self.company,
            title="Prepare shared requirements", priority="HIGH")
        self.coworker_task = Task.objects.create(owner=self.other, team=self.team, company=self.company,
            title="Coworker shared work", priority="CRITICAL", due_date=DAY-timedelta(days=1))

    def board(self, user=None, **changes):
        return build_strategy_board(**{"user": user or self.manager, "day": DAY, **changes})

    def lane(self, board, user=None):
        return next(row for row in board["lanes"] if row["user_id"] == (user or self.worker).pk and row["team_id"] == self.team.pk)

    def policy(self, user=None, **changes):
        return WorkPolicy.objects.create(**{"owner": user or self.worker, "configured": True,
            "timezone_name": "Africa/Harare", "weekdays": [0,1,2,3,4], "start_time": time(9),
            "end_time": time(17), "buffer_minutes": 0, **changes})

    def test_employee_sees_own_lane_and_work_not_coworker_facts(self):
        board = self.board(self.worker)
        self.assertEqual({row["user_id"] for row in board["lanes"]}, {self.worker.pk})
        encoded = json.dumps(board)
        self.assertIn(self.task.title, encoded)
        self.assertNotIn(self.coworker_task.title, encoded)
        self.assertNotIn(self.other.username, encoded)

    def test_manager_sees_only_current_managed_team(self):
        hidden = Task.objects.create(owner=self.other, company=self.company, team=self.other_team, title="UNREADABLE_TEAM_TITLE")
        private = Task.objects.create(owner=self.worker, title="PRIVATE_PERSONAL_TITLE", notes="PRIVATE_NOTES")
        board = self.board()
        self.assertEqual({row["team_id"] for row in board["lanes"]}, {self.team.pk})
        encoded = json.dumps(board)
        self.assertNotIn(hidden.title, encoded)
        self.assertNotIn(private.title, encoded)
        self.assertNotIn("PRIVATE_NOTES", encoded)

    def test_global_capability_without_grant_does_not_create_company_read(self):
        board = self.board(self.executive)
        self.assertEqual(board["counts"]["task_total"], 0)
        self.assertEqual(board["lanes"], [])
        with self.assertRaises(PermissionDenied):
            self.board(self.executive, company_id=self.company.pk)

    def test_executive_with_current_company_grant_sees_company_lanes(self):
        grant = OrganizationAccess.objects.create(user=self.executive, company=self.company, can_view_all=True)
        board = self.board(self.executive)
        self.assertEqual({row["team_id"] for row in board["lanes"]}, {self.team.pk,self.other_team.pk})
        grant.is_active=False
        grant.save()
        self.assertEqual(self.board(self.executive)["lanes"], [])

    def test_forged_team_and_company_filters_are_denied(self):
        for changes in ({"team_id": self.other_team.pk}, {"team_id": self.foreign_team.pk}, {"company_id": self.foreign_company.pk}):
            with self.subTest(changes=changes), self.assertRaises(PermissionDenied): self.board(**changes)

    def test_revoked_membership_removes_manager_lanes_immediately(self):
        self.manager_membership.is_active=False
        self.manager_membership.save()
        self.assertEqual(self.board()["lanes"], [])
        self.assertFalse(strategy_teams_for(get_user_model().objects.get(pk=self.manager.pk)).exists())

    def test_declared_current_and_next_are_never_inferred_from_task_status(self):
        self.task.status="IN_PROGRESS"
        self.task.save()
        lane = self.lane(self.board())
        self.assertIsNone(lane["declaration"]["current"])
        self.assertIsNone(lane["declaration"]["next"])
        self.assertIsNone(lane["declaration"]["last_confirmed"])
        self.assertTrue(lane["declaration"]["stale"])

    def test_self_confirmation_has_audit_and_preserves_work(self):
        before = Task.objects.values().get(pk=self.task.pk)
        declaration = declare_work(user=self.worker, team_id=self.team.pk,
            current_task_id=self.task.pk, next_task_id=self.next_task.pk)
        self.assertEqual(before, Task.objects.values().get(pk=self.task.pk))
        self.assertEqual(WorkDeclarationEvent.objects.filter(declaration=declaration).count(), 1)
        lane = self.lane(self.board())
        self.assertEqual(lane["declaration"]["current"]["id"], self.task.pk)
        self.assertEqual(lane["declaration"]["next"]["id"], self.next_task.pk)
        self.assertFalse(lane["declaration"]["stale"])

    def test_confirmation_cannot_choose_another_employee_or_team_work(self):
        with self.assertRaises(PermissionDenied):
            declare_work(user=self.worker, team_id=self.team.pk, current_task_id=self.coworker_task.pk)
        with self.assertRaises(PermissionDenied):
            declare_work(user=self.worker, team_id=self.other_team.pk, current_task_id=self.task.pk)
        with self.assertRaises(ValidationError):
            declare_work(user=self.worker, team_id=self.team.pk, current_task_id=self.task.pk,next_task_id=self.task.pk)
        self.assertFalse(WorkDeclaration.objects.exists())

    def test_changed_source_is_needs_update_and_old_declaration_never_transfers(self):
        declare_work(user=self.worker, team_id=self.team.pk,current_task_id=self.task.pk)
        self.task.owner=self.other
        self.task.save()
        lane = self.lane(self.board())
        self.assertIsNone(lane["declaration"]["current"])
        self.assertTrue(lane["declaration"]["needs_update"])
        self.assertTrue(lane["declaration"]["stale"])

    def test_changed_task_version_invalidates_confirmation_even_same_clock(self):
        declare_work(user=self.worker, team_id=self.team.pk,current_task_id=self.task.pk)
        self.task.title="Clarified scope at the same timestamp"
        self.task.save()
        self.assertTrue(self.lane(self.board())["declaration"]["needs_update"])

    def test_confirm_revision_replay_cannot_add_duplicate_audit(self):
        item = declare_work(user=self.worker,team_id=self.team.pk,current_task_id=self.task.pk)
        with self.assertRaises(DeclarationConflict):
            declare_work(user=self.worker,team_id=self.team.pk,current_task_id=self.next_task.pk,expected_revision="none")
        self.assertEqual(WorkDeclarationEvent.objects.count(), 1)
        updated = declare_work(user=self.worker,team_id=self.team.pk,current_task_id=self.next_task.pk,expected_revision=item.revision)
        self.assertNotEqual(item.revision,updated.revision)

    def test_no_configuration_means_unknown_capacity_not_zero(self):
        lane = self.lane(self.board())
        self.assertFalse(lane["capacity"]["known"])
        self.assertIsNone(lane["capacity"]["available_minutes"])
        self.assertIn("capacity_unknown", {row["code"] for row in self.board()["exceptions"]})

    def test_capacity_excludes_private_notes_and_unreadable_schedule_titles(self):
        self.policy()
        Availability.objects.create(owner=self.worker, kind="COMMITMENT", starts_at=NOW,
            ends_at=NOW+timedelta(minutes=30),private_note="PRIVATE_AVAILABILITY_SENTINEL")
        hidden = Task.objects.create(owner=self.worker,title="PRIVATE_ACCEPTED_TASK_SENTINEL",estimated_duration=timedelta(minutes=30))
        ScheduleBlock.objects.create(owner=self.worker,task=hidden,state="ACCEPTED",starts_at=NOW+timedelta(hours=1),ends_at=NOW+timedelta(hours=1,minutes=30))
        board = self.board()
        lane = self.lane(board)
        self.assertTrue(lane["capacity"]["known"])
        self.assertEqual(lane["capacity"]["committed_minutes"],30)
        self.assertEqual(lane["capacity"]["usable_minutes"],450)
        encoded=json.dumps(board)
        self.assertNotIn("PRIVATE_AVAILABILITY_SENTINEL",encoded)
        self.assertNotIn(hidden.title,encoded)

    def test_execution_dependency_and_capacity_views_reconcile_same_task_facts(self):
        boards=[self.board(view=view) for view in ("execution","dependencies","capacity")]
        for board in boards[1:]:
            self.assertEqual(boards[0]["counts"],board["counts"])
            self.assertEqual(boards[0]["lanes"],board["lanes"])

    def test_active_blocker_age_uses_status_transition_not_a_later_edit(self):
        with patch("django.utils.timezone.now",return_value=NOW-timedelta(hours=2)):
            block_task(task=self.task,user=self.worker,reason="Awaiting a shared response")
        self.task.refresh_from_db()
        self.task.title="A later clarification"
        self.task.save()
        task=next(row for row in self.lane(self.board())["tasks"] if row["id"]==self.task.pk)
        self.assertEqual(task["blocker"]["age_minutes"],120)
        self.assertEqual(task["blocker"]["age_basis"],"blocked_status_event")

    def test_legacy_blocker_age_is_unknown_without_status_evidence(self):
        Task.objects.filter(pk=self.task.pk).update(status="BLOCKED",blocked_reason="Legacy blocker")
        task=next(row for row in self.lane(self.board())["tasks"] if row["id"]==self.task.pk)
        self.assertIsNone(task["blocker"]["age_minutes"])
        self.assertEqual(task["blocker"]["age_basis"],"unknown")

    def test_unavailable_dependency_does_not_reveal_private_identity_or_title(self):
        hidden=Task.objects.create(owner=self.other,title="PRIVATE_PREREQUISITE_SENTINEL")
        # Simulate a legacy or subsequently rescoped link; normal edge creation
        # prevents this scope mismatch, but reads must remain safe regardless.
        TaskDependency.objects.bulk_create([TaskDependency(task=self.task,prerequisite=hidden)])
        board=self.board()
        edge=next(row for row in board["dependencies"] if row["task_id"]==self.task.pk)
        self.assertIsNone(edge["prerequisite_id"])
        self.assertEqual(edge["title"],"Unavailable prerequisite")
        self.assertFalse(edge["accessible"])
        self.assertEqual(edge["url"],"")
        self.assertNotIn(hidden.title,json.dumps(board))

    def test_overload_and_insufficient_capacity_are_distinct_explicit_exceptions(self):
        self.policy(end_time=time(10))
        ScheduleBlock.objects.create(owner=self.worker,task=self.task,state="ACCEPTED",
            starts_at=NOW-timedelta(hours=1),ends_at=NOW+timedelta(hours=1))
        board=self.board()
        self.assertEqual(self.lane(board)["capacity"]["overload_minutes"],60)
        codes={row["code"] for row in board["exceptions"] if row.get("user_id")==self.worker.pk}
        self.assertTrue({"overload","insufficient_capacity"}.issubset(codes))

    def test_overdue_handoff_uses_recorded_request_age_and_resolver(self):
        request=CoordinationRequest.objects.create(task=self.task,kind="HANDOFF",raised_by=self.worker,
            resolver=self.manager,reason="A shared response is needed",requested_action="Confirm equipment options",
            expected_response_at=NOW-timedelta(minutes=15),raised_at=NOW-timedelta(minutes=90),
            source_owner=self.worker,source_company=self.company,source_team=self.team)
        board=self.board()
        task=next(row for row in self.lane(board)["tasks"] if row["id"]==self.task.pk)
        self.assertEqual(task["blocker"]["age_minutes"],90)
        self.assertEqual(task["blocker"]["resolver"],self.manager.username)
        self.assertEqual(task["blocker"]["age_basis"],"request_created")
        self.assertIn(str(request.pk),task["blocker"]["request_url"])
        self.assertIn("overdue_handoff",{row["code"] for row in board["exceptions"]})

    def test_goal_without_visible_links_uses_qualified_orphan_label(self):
        goal=Goal.objects.create(company=self.company,title="Shared company direction")
        initiative=Initiative.objects.create(goal=goal,title="Outside the manager's team")
        hidden=Task.objects.create(owner=self.other,company=self.company,team=self.other_team,title="PRIVATE_OTHER_TEAM_GOAL_TASK")
        TaskContribution.objects.create(task=hidden,initiative=initiative)
        board=self.board()
        self.assertEqual(board["goals"][0]["linked_task_count"],0)
        exception=next(row for row in board["exceptions"] if row["code"]=="orphan_goal")
        self.assertEqual(exception["label"],"No linked work in this view")
        self.assertNotIn(hidden.title,json.dumps(board))

    def test_inactive_owner_is_review_exception_not_invented_unowned_person(self):
        get_user_model().objects.filter(pk=self.other.pk).update(is_active=False)
        board=self.board()
        self.assertIn(self.coworker_task.pk,{row["id"] for row in board["unallocated_tasks"]})
        self.assertIn("owner_review",{row["code"] for row in board["exceptions"]})
        self.assertNotIn(self.other.pk,{row["user_id"] for row in board["lanes"]})

    def test_arrival_and_end_day_show_explicit_declarations_only(self):
        before=self.lane(self.board())
        self.assertFalse(before["arrival"]["declared"])
        self.assertTrue(before["end_day"]["known"])
        self.assertFalse(before["end_day"]["submitted"])
        arrival=ArrivalDeclaration.objects.create(user=self.worker,local_date=DAY,timezone_name="Africa/Harare",registered_at=NOW)
        arrival.teams.add(self.team)
        plan=DailyPlan.objects.create(owner=self.worker,plan_date=DAY,quick_notes="PRIVATE_PLAN_SENTINEL")
        review=DailyReview.objects.create(plan=plan,accomplishments="PRIVATE_REFLECTION_SENTINEL")
        EndDaySubmission.objects.create(owner=self.worker,team=self.team,report_date=DAY,version=1,source_digest="x"*64,review=review,
            facts={"private_untrusted_fixture":"PRIVATE_FACTS_SENTINEL"})
        report=ManagerReportSnapshot.objects.create(team=self.team,report_date=DAY,version=1,source_digest="y"*64,
            facts={"private_untrusted_fixture":"PRIVATE_REPORT_SENTINEL"})
        board=self.board()
        lane=self.lane(board)
        self.assertTrue(lane["arrival"]["declared"])
        self.assertEqual(lane["arrival"]["effective_at"],NOW.isoformat())
        self.assertTrue(lane["end_day"]["submitted"])
        self.assertIn(str(report.pk),lane["end_day"]["url"])
        for sentinel in ("PRIVATE_PLAN_SENTINEL","PRIVATE_REFLECTION_SENTINEL","PRIVATE_FACTS_SENTINEL","PRIVATE_REPORT_SENTINEL"):
            self.assertNotIn(sentinel,json.dumps(board))

    def test_company_work_reader_without_report_grant_gets_unknown_not_missing(self):
        OrganizationAccess.objects.create(user=self.executive,company=self.company,can_view_all=True)
        lane=self.lane(self.board(self.executive))
        self.assertFalse(lane["end_day"]["known"])
        self.assertIsNone(lane["end_day"]["submitted"])

    def test_more_than_task_limit_has_true_counts_without_per_task_queries(self):
        Task.objects.bulk_create([Task(owner=self.worker,team=self.team,company=self.company,title=f"Bounded task {i}",slug=f"bounded-strategy-{i}") for i in range(140)])
        with CaptureQueriesContext(connection) as captured:
            board=self.board()
        self.assertEqual(board["counts"]["task_total"],143)
        self.assertEqual(board["counts"]["included_tasks"],100)
        self.assertEqual(board["counts"]["omitted_tasks"],43)
        self.assertLess(len(captured),180)

    def test_lane_pagination_is_bounded_and_total_is_honest(self):
        users=[get_user_model().objects.create_user(f"board-bulk-{i:02}") for i in range(30)]
        TeamMembership.objects.bulk_create([TeamMembership(team=self.team,user=user) for user in users])
        board=self.board()
        self.assertEqual(board["counts"]["lane_total"],33)
        self.assertEqual(board["counts"]["included_lanes"],24)
        self.assertTrue(board["pagination"]["has_next"])
        last=self.board(page=2)
        self.assertEqual(last["counts"]["included_lanes"],9)
        self.assertTrue(last["pagination"]["has_previous"])

    def test_read_does_not_mutate_declarations_tasks_or_policies(self):
        before=list(Task.objects.order_by("pk").values())
        self.board()
        self.assertEqual(before,list(Task.objects.order_by("pk").values()))
        self.assertFalse(WorkDeclaration.objects.exists())
        self.assertFalse(WorkPolicy.objects.exists())

    def test_work_declaration_audit_is_immutable(self):
        declare_work(user=self.worker,team_id=self.team.pk,current_task_id=self.task.pk)
        event=WorkDeclarationEvent.objects.get()
        with self.assertRaises(ValidationError): event.save()
        with self.assertRaises(ValidationError): event.delete()

    @override_settings(TASKER_STRATEGY_ENABLED=False)
    def test_declaration_rollout_flag_is_enforced_at_domain_boundary(self):
        with self.assertRaises(ValidationError):
            declare_work(user=self.worker,team_id=self.team.pk,current_task_id=self.task.pk)
        self.assertFalse(WorkDeclaration.objects.exists())

    def test_invalid_input_is_rejected_without_identity_coercion(self):
        for changes in ({"team_id":True},{"company_id":"x"},{"view":"private-notes"},{"page":0},{"day":"tomorrow"}):
            with self.subTest(changes=changes),self.assertRaises(ValidationError): self.board(**changes)


@override_settings(TASKER_STRATEGY_ENABLED=True)
class WorkDeclarationRaceTests(TransactionTestCase):
    def test_concurrent_first_confirmation_has_one_row_and_one_event(self):
        if connection.vendor!="postgresql": self.skipTest("Requires PostgreSQL row locks")
        user=employee("concurrent-declaration-owner")
        company=Company.objects.create(name="Concurrent declaration company")
        team=Team.objects.create(company=company,name="Shared team")
        TeamMembership.objects.create(team=team,user=user)
        task=Task.objects.create(owner=user,company=company,team=team,title="Confirmed once")
        barrier=Barrier(2)
        def confirm():
            close_old_connections()
            try:
                actor=get_user_model().objects.get(pk=user.pk)
                barrier.wait(timeout=15)
                try:
                    declare_work(user=actor,team_id=team.pk,current_task_id=task.pk)
                    return "saved"
                except DeclarationConflict:
                    return "conflict"
            finally: close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(confirm) for _ in range(2)]
            outcomes=sorted(future.result(timeout=30) for future in futures)
        self.assertEqual(outcomes,["conflict","saved"])
        self.assertEqual(WorkDeclaration.objects.count(),1)
        self.assertEqual(WorkDeclarationEvent.objects.count(),1)
