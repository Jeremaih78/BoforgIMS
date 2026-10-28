from datetime import timedelta
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, close_old_connections, transaction
from django.test import TestCase, TransactionTestCase
from django.utils import timezone

from accounting.models import Company
from tasker.models import (
    Goal, Initiative, KeyResult, OrganizationAccess, OutcomeMeasurement,
    Task, TaskContribution, Team, TeamMembership,
)
from tasker.services.goals import (
    accept_outcome, key_result_progress, link_task_contribution,
    record_outcome, reconcile_task_outcomes, retract_outcome,
    visible_goals_for, visible_outcomes_for,
)


class GoalEvidenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.manager = users.objects.create_user(username="goals-manager")
        cls.employee = users.objects.create_user(username="goals-employee")
        cls.other = users.objects.create_user(username="goals-other")
        cls.executive = users.objects.create_user(username="goals-executive")
        permissions = list(Permission.objects.filter(content_type__app_label="tasker"))
        for user in (cls.manager, cls.employee, cls.other, cls.executive):
            user.user_permissions.add(*permissions)
        cls.company = Company.objects.create(name="Goals test company")
        cls.other_company = Company.objects.create(name="Other goals company")
        cls.team = Team.objects.create(company=cls.company, name="Delivery")
        cls.other_team = Team.objects.create(company=cls.other_company, name="Other team")
        TeamMembership.objects.create(team=cls.team, user=cls.manager, role=TeamMembership.Role.MANAGER)
        TeamMembership.objects.create(team=cls.team, user=cls.employee)
        TeamMembership.objects.create(team=cls.other_team, user=cls.other, role=TeamMembership.Role.MANAGER)
        OrganizationAccess.objects.create(company=cls.company, user=cls.executive, can_manage=True, can_view_all=True)
        cls.goal = Goal.objects.create(company=cls.company, team=cls.team, title="Accepted deliveries", status=Goal.Status.ACTIVE, evidence_policy="Approved delivery evidence")
        cls.result = KeyResult.objects.create(goal=cls.goal, title="Delivered orders", unit="orders", baseline=0, target=100)
        cls.initiative = Initiative.objects.create(goal=cls.goal, key_result=cls.result, title="Improve delivery")

    def task(self, **kwargs):
        return Task.objects.create(title="Deliver order", owner=self.employee, company=self.company,
                                   team=self.team, status=Task.Status.COMPLETED, **kwargs)

    def outcome(self, *, task=None, source_key="order-1", value=10, **kwargs):
        return record_outcome(user=self.employee, key_result=self.result, employee=self.employee,
                              task=task, value=Decimal(value), evidence="Approved source document", source_key=source_key, **kwargs)

    def test_goal_visibility_requires_live_membership_even_with_global_capabilities(self):
        self.assertTrue(visible_goals_for(self.employee).filter(pk=self.goal.pk).exists())
        self.assertFalse(visible_goals_for(self.other).filter(pk=self.goal.pk).exists())
        TeamMembership.objects.filter(team=self.team, user=self.employee).update(is_active=False)
        self.assertFalse(visible_goals_for(self.employee).filter(pk=self.goal.pk).exists())

    def test_task_completion_without_evidence_does_not_increase_goal(self):
        self.task(progress=100)
        metric = key_result_progress(self.result)
        self.assertEqual(metric["current"], 0)
        self.assertEqual(metric["progress"], 0)
        self.assertEqual(metric["accepted_count"], 0)

    def test_only_explicit_accepted_evidence_counts_and_replay_counts_once(self):
        outcome = self.outcome()
        self.assertEqual(key_result_progress(self.result)["current"], 0)
        accept_outcome(outcome=outcome, user=self.manager)
        replay = self.outcome()
        self.assertEqual(replay.pk, outcome.pk)
        accept_outcome(outcome=replay, user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 10)
        self.assertEqual(key_result_progress(self.result)["accepted_count"], 1)

    def test_employee_cannot_accept_or_retract_even_with_capability(self):
        outcome = self.outcome()
        with self.assertRaises(PermissionDenied):
            accept_outcome(outcome=outcome, user=self.employee)
        with self.assertRaises(PermissionDenied):
            retract_outcome(outcome=outcome, user=self.employee, reason="No scope")

    def test_revoked_manager_cannot_accept(self):
        outcome = self.outcome()
        TeamMembership.objects.filter(user=self.manager).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            accept_outcome(outcome=outcome, user=self.manager)

    def test_wrong_team_manager_cannot_record_accept_or_read_outcomes(self):
        outcome = self.outcome()
        with self.assertRaises(PermissionDenied):
            accept_outcome(outcome=outcome, user=self.other)
        with self.assertRaises(PermissionDenied):
            record_outcome(user=self.other, key_result=self.result, employee=self.employee, value=1, evidence="wrong scope", source_key="forged")
        self.assertFalse(visible_outcomes_for(self.other).filter(pk=outcome.pk).exists())

    def test_capability_required_in_addition_to_manager_membership(self):
        outcome = self.outcome()
        self.manager.user_permissions.remove(Permission.objects.get(content_type__app_label="tasker", codename="change_outcomemeasurement"))
        manager = get_user_model().objects.get(pk=self.manager.pk)
        with self.assertRaises(PermissionDenied):
            accept_outcome(outcome=outcome, user=manager)

    def test_unknown_baseline_remains_unknown_with_accepted_evidence(self):
        self.result.baseline = None
        self.result.save()
        accept_outcome(outcome=self.outcome(), user=self.manager)
        metric = key_result_progress(self.result)
        self.assertIsNone(metric["current"])
        self.assertIsNone(metric["progress"])
        self.assertFalse(metric["known"])

    def test_lower_is_better_latest_absolute_measure(self):
        self.result.baseline = Decimal(10)
        self.result.target = Decimal(2)
        self.result.direction = KeyResult.Direction.LOWER
        self.result.aggregation = KeyResult.Aggregation.LATEST
        self.result.save()
        accept_outcome(outcome=self.outcome(value=6), user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 6)
        self.assertEqual(key_result_progress(self.result)["progress"], 50)

    def test_wrong_target_direction_and_nonfinite_values_rejected(self):
        self.result.target = -1
        with self.assertRaises(ValidationError):
            self.result.save()
        self.result.target = Decimal("NaN")
        with self.assertRaises(ValidationError):
            self.result.save()

    def test_retraction_removes_progress_but_preserves_source_id(self):
        outcome = accept_outcome(outcome=self.outcome(), user=self.manager)
        retract_outcome(outcome=outcome, user=self.manager, reason="Source cancelled")
        self.assertEqual(key_result_progress(self.result)["current"], 0)
        self.assertEqual(self.outcome().pk, outcome.pk)
        with self.assertRaises(ValidationError):
            self.outcome(value=11)

    def test_company_source_identity_prevents_attribution_to_second_result(self):
        self.outcome()
        other_result = KeyResult.objects.create(goal=self.goal, title="Another measure", unit="orders", baseline=0, target=20)
        with self.assertRaises(ValidationError):
            record_outcome(user=self.employee, key_result=other_result, employee=self.employee, value=10,
                           evidence="Approved source document", source_key="order-1")

    def test_database_source_identity_survives_soft_deletion(self):
        outcome = self.outcome()
        OutcomeMeasurement.objects.filter(pk=outcome.pk).delete()
        with self.assertRaises(ValidationError):
            self.outcome()
        with self.assertRaises(IntegrityError), transaction.atomic():
            OutcomeMeasurement.all_objects.bulk_create([OutcomeMeasurement(company=self.company, team=self.team,
                key_result=self.result, employee=self.employee, occurred_on=timezone.localdate(), value=10,
                evidence="another attempt", source_type="manual", source_key="order-1")])

    def test_task_outcome_requires_primary_matching_result(self):
        task = self.task()
        with self.assertRaises(ValidationError):
            self.outcome(task=task)
        link_task_contribution(task=task, initiative=self.initiative, user=self.manager)
        outcome = self.outcome(task=task)
        accept_outcome(outcome=outcome, user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 10)

    def test_unfinished_task_cannot_be_accepted(self):
        task = self.task()
        task.status = Task.Status.IN_PROGRESS
        task.save()
        link_task_contribution(task=task, initiative=self.initiative, user=self.manager)
        outcome = self.outcome(task=task)
        with self.assertRaises(ValidationError):
            accept_outcome(outcome=outcome, user=self.manager)

    def test_reopening_retracts_and_recompletion_requires_explicit_acceptance(self):
        task = self.task()
        link_task_contribution(task=task, initiative=self.initiative, user=self.manager)
        outcome = accept_outcome(outcome=self.outcome(task=task), user=self.manager)
        task.status = Task.Status.IN_PROGRESS
        task.save()
        reconcile_task_outcomes(task=task, user=self.employee)
        outcome.refresh_from_db()
        self.assertEqual(outcome.status, OutcomeMeasurement.Status.RETRACTED)
        self.assertEqual(key_result_progress(self.result)["current"], 0)
        task.status = Task.Status.COMPLETED
        task.save()
        self.assertEqual(key_result_progress(self.result)["current"], 0)
        accept_outcome(outcome=outcome, user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 10)

    def test_accepted_evidence_is_immutable(self):
        outcome = accept_outcome(outcome=self.outcome(), user=self.manager)
        outcome.value = 90
        with self.assertRaises(ValidationError):
            outcome.save()

    def test_accepted_metric_definition_cannot_be_reinterpreted(self):
        accept_outcome(outcome=self.outcome(), user=self.manager)
        self.result.aggregation = KeyResult.Aggregation.LATEST
        with self.assertRaises(ValidationError):
            self.result.save()

    def test_draft_goal_and_missing_policy_cannot_accept_business_output(self):
        outcome = self.outcome()
        self.goal.status = Goal.Status.DRAFT
        self.goal.save()
        with self.assertRaises(ValidationError):
            accept_outcome(outcome=outcome, user=self.manager)
        self.goal.status = Goal.Status.ACTIVE
        self.goal.evidence_policy = ""
        self.goal.save()
        with self.assertRaises(ValidationError):
            accept_outcome(outcome=outcome, user=self.manager)

    def test_direct_status_change_does_not_bypass_acceptance_service(self):
        outcome = self.outcome()
        outcome.status = OutcomeMeasurement.Status.ACCEPTED
        outcome.accepted_by = self.manager
        outcome.accepted_at = timezone.now()
        with self.assertRaises(ValidationError):
            outcome.save()

    def test_structural_cross_company_and_wrong_result_links_rejected(self):
        with self.assertRaises(ValidationError):
            Goal.objects.create(company=self.other_company, team=self.team, title="Invalid scope")
        other_goal = Goal.objects.create(company=self.company, team=self.team, title="Another goal")
        with self.assertRaises(ValidationError):
            Initiative.objects.create(goal=other_goal, key_result=self.result, title="Wrong result")
        task = Task.objects.create(title="Personal", owner=self.employee)
        with self.assertRaises(ValidationError):
            TaskContribution.objects.create(task=task, initiative=self.initiative)

    def test_task_percent_and_multiple_checklist_subtasks_cannot_duplicate_output(self):
        for index in range(3):
            self.task(progress=100)
        outcome = accept_outcome(outcome=self.outcome(value=1), user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 1)
        self.assertEqual(visible_outcomes_for(self.manager).get(pk=outcome.pk).value, 1)

    def test_horizon_filters_old_source_evidence_without_altering_audit(self):
        self.result.start_date = timezone.localdate()
        self.result.save()
        old = self.outcome(occurred_on=timezone.localdate() - timedelta(days=1))
        accept_outcome(outcome=old, user=self.manager)
        self.assertEqual(key_result_progress(self.result)["current"], 0)
        self.assertEqual(OutcomeMeasurement.objects.get(pk=old.pk).status, OutcomeMeasurement.Status.ACCEPTED)

    def test_company_goal_can_receive_scoped_team_evidence_without_company_admin(self):
        goal = Goal.objects.create(company=self.company, title="Shared company direction", status=Goal.Status.ACTIVE, evidence_policy="Approved orders")
        result = KeyResult.objects.create(goal=goal, title="Orders", baseline=0, target=50, unit="orders")
        outcome = record_outcome(user=self.manager, key_result=result, employee=self.employee,
            team=self.team, value=5, evidence="Order reference", source_key="shared-order")
        accept_outcome(outcome=outcome, user=self.manager)
        self.assertEqual(key_result_progress(result)["current"], 5)
        self.assertFalse(visible_outcomes_for(self.other).filter(pk=outcome.pk).exists())

    def test_inactive_company_hides_goals_and_prevents_acceptance(self):
        outcome = self.outcome()
        Company.objects.filter(pk=self.company.pk).update(is_active=False)
        self.assertFalse(visible_goals_for(self.manager).exists())
        with self.assertRaises(PermissionDenied):
            accept_outcome(outcome=outcome, user=self.manager)

    def test_rescoped_task_does_not_disclose_former_restricted_goal(self):
        task = self.task()
        link_task_contribution(task=task, initiative=self.initiative, user=self.manager)
        task.owner = self.other
        task.company = self.other_company
        task.team = self.other_team
        task.save()
        self.client.force_login(self.other)
        response = self.client.get(task.get_absolute_url())
        self.assertContains(response, task.title)
        self.assertNotContains(response, self.goal.title)
        self.assertNotContains(response, self.initiative.title)

    def test_link_rechecks_manager_revoked_while_waiting_for_scope_lock(self):
        from tasker.services import goals as services
        lock = services._lock_scope
        task = self.task()

        def revoke(*args, **kwargs):
            lock(*args, **kwargs)
            TeamMembership.objects.filter(team=self.team, user=self.manager).update(is_active=False)

        with patch.object(services, "_lock_scope", side_effect=revoke), self.assertRaises(PermissionDenied):
            link_task_contribution(task=task, initiative=self.initiative, user=self.manager)
        self.assertFalse(TaskContribution.objects.filter(task=task).exists())

    def test_record_rechecks_employee_revoked_while_waiting_for_scope_lock(self):
        from tasker.services import goals as services
        lock = services._lock_scope

        def revoke(*args, **kwargs):
            lock(*args, **kwargs)
            TeamMembership.objects.filter(team=self.team, user=self.employee).update(is_active=False)

        with patch.object(services, "_lock_scope", side_effect=revoke), self.assertRaises(PermissionDenied):
            self.outcome()
        self.assertFalse(OutcomeMeasurement.objects.exists())


    def test_future_business_outcome_cannot_be_recorded_as_actual(self):
        with self.assertRaises(ValidationError):
            self.outcome(occurred_on=timezone.localdate() + timedelta(days=1))

    def test_legacy_future_draft_cannot_be_accepted(self):
        outcome = self.outcome()
        OutcomeMeasurement.objects.filter(pk=outcome.pk).update(occurred_on=timezone.localdate() + timedelta(days=1))
        with self.assertRaises(ValidationError):
            accept_outcome(outcome=outcome, user=self.manager)

    def test_future_legacy_accepted_evidence_does_not_inflate_actual_progress(self):
        outcome = accept_outcome(outcome=self.outcome(), user=self.manager)
        OutcomeMeasurement.objects.filter(pk=outcome.pk).update(occurred_on=timezone.localdate() + timedelta(days=1))
        self.assertEqual(key_result_progress(self.result)["current"], 0)


class OutcomeConcurrencyTests(TransactionTestCase):
    """Different requests cannot count one business occurrence twice."""

    def setUp(self):
        self.user = get_user_model().objects.create_user(username="outcome-thread-user")
        self.user.user_permissions.add(*Permission.objects.filter(content_type__app_label="tasker"))
        self.company = Company.objects.create(name="Outcome concurrency company")
        self.team = Team.objects.create(company=self.company, name="Operations")
        TeamMembership.objects.create(team=self.team, user=self.user, role=TeamMembership.Role.MANAGER)
        self.goal = Goal.objects.create(company=self.company, team=self.team, title="Source reconciliation", status=Goal.Status.ACTIVE, evidence_policy="Approved external evidence")
        self.result = KeyResult.objects.create(goal=self.goal, title="Orders", unit="orders", baseline=0, target=100)
        self.second_result = KeyResult.objects.create(goal=self.goal, title="Second goal allocation", unit="orders", baseline=0, target=100)

    def _race_records(self, result_ids):
        barrier = Barrier(2)
        user_id = self.user.pk

        def record(result_id):
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                result = KeyResult.objects.get(pk=result_id)
                barrier.wait(timeout=15)
                try:
                    outcome = record_outcome(user=user, key_result=result, employee=user,
                        value=1, evidence="Same accepted business event", source_key="external-order-42")
                    return "recorded", outcome.pk
                except ValidationError:
                    return "conflict", None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(record, pk) for pk in result_ids]
            return [future.result(timeout=30) for future in futures]

    def test_concurrent_identical_replays_return_one_occurrence(self):
        results = self._race_records([self.result.pk, self.result.pk])
        self.assertEqual([status for status, _ in results], ["recorded", "recorded"])
        self.assertEqual(len({pk for _, pk in results}), 1)
        self.assertEqual(OutcomeMeasurement.objects.count(), 1)

    def test_concurrent_second_goal_cannot_claim_same_business_occurrence(self):
        results = self._race_records([self.result.pk, self.second_result.pk])
        self.assertEqual(sorted(status for status, _ in results), ["conflict", "recorded"])
        self.assertEqual(OutcomeMeasurement.objects.count(), 1)

    def test_concurrent_acceptance_creates_one_acceptance_event(self):
        outcome = record_outcome(user=self.user, key_result=self.result, employee=self.user,
            value=3, evidence="Approved order", source_key="accept-race")
        barrier = Barrier(2)
        user_id, outcome_id = self.user.pk, outcome.pk

        def accept():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_id)
                candidate = OutcomeMeasurement.objects.get(pk=outcome_id)
                barrier.wait(timeout=15)
                return accept_outcome(outcome=candidate, user=user).pk
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(accept) for _ in range(2)]
            self.assertEqual({future.result(timeout=30) for future in futures}, {outcome_id})
        outcome.refresh_from_db()
        self.assertEqual(len(outcome.acceptance_history), 1)
        self.assertEqual(key_result_progress(self.result)["current"], 3)
