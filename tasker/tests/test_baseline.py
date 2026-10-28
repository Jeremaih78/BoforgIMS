"""Evidence and privacy boundaries for the pre-rollout productivity baseline."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.forms import modelform_factory
from django.test import TestCase
from django.utils import timezone

from accounting.models import Company
from tasker.models import BaselineObservation, BaselineStudy, OrganizationAccess, Team, TeamMembership
from tasker.services.baseline import (
    accept_observation, baseline_csv, baseline_summary, retract_observation,
    save_observation, save_study, visible_observations_for, visible_studies_for,
)


StudyForm = modelform_factory(BaselineStudy, fields=(
    "company", "team", "name", "role", "workload_type", "output_unit",
    "output_definition", "acceptance_criteria", "effort_definition", "quality_definition",
    "source_definition", "comparison_plan", "start_date", "end_date", "pilot_start_date", "status",
))
ObservationForm = modelform_factory(BaselineObservation, fields=(
    "study", "employee", "observed_on", "staffed_hours", "accepted_output",
    "quality_passed", "rework_count", "evidence", "source_key",
))


class BaselineEvidenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.company = Company.objects.create(name="Baseline company")
        cls.other_company = Company.objects.create(name="Separate baseline company")
        cls.team = Team.objects.create(company=cls.company, name="Repairs")
        cls.other_team = Team.objects.create(company=cls.company, name="Sales")
        cls.manager = get_user_model().objects.create_user(username="baseline-manager")
        cls.employee = get_user_model().objects.create_user(username="baseline-employee")
        cls.colleague = get_user_model().objects.create_user(username="baseline-colleague")
        cls.outsider = get_user_model().objects.create_user(username="baseline-outsider")
        permissions = Permission.objects.filter(content_type__app_label="tasker")
        cls.manager.user_permissions.set(permissions)
        for user in (cls.employee, cls.colleague, cls.outsider):
            user.user_permissions.set(permissions.filter(codename__in=(
                "view_baselinestudy", "view_baselineobservation", "add_baselineobservation", "change_baselineobservation",
            )))
        cls.manager_membership = TeamMembership.objects.create(team=cls.team, user=cls.manager, role="MANAGER")
        TeamMembership.objects.create(team=cls.team, user=cls.employee)
        TeamMembership.objects.create(team=cls.team, user=cls.colleague)
        TeamMembership.objects.create(team=cls.other_team, user=cls.outsider)

    def setUp(self):
        self.today = timezone.localdate()
        self.study = BaselineStudy.objects.create(
            company=self.company, team=self.team, name="Repair baseline", role="Technician",
            workload_type="Standard repairs", output_unit="accepted repairs",
            output_definition="Repairs accepted by the customer", acceptance_criteria="Evidence reviewed by manager",
            effort_definition="Rostered staffed hours excluding leave, declared from staffing records",
            quality_definition="Passed repair checks and documented rework", source_definition="Repair register and roster",
            comparison_plan="Compare equivalent repair types before and after pilot; include uncertainty",
            start_date=self.today-timedelta(days=10), end_date=self.today-timedelta(days=1),
            pilot_start_date=self.today+timedelta(days=1), status="COLLECTING",
        )

    def observation(self, employee=None, **updates):
        data = dict(study=self.study, employee=employee or self.employee,
                    observed_on=self.today-timedelta(days=2), role=self.study.role,
                    workload_type=self.study.workload_type, staffed_hours=Decimal("8"),
                    accepted_output=Decimal("16"), quality_passed=16, rework_count=0,
                    evidence="Repair register R001-R016 and staffing roster", source_key="roster-employee-day1")
        data.update(updates)
        return BaselineObservation.objects.create(**data)

    def observation_form(self, employee=None, **updates):
        data = dict(study=self.study.pk, employee=(employee or self.employee).pk,
                    observed_on=(self.today-timedelta(days=2)).isoformat(), staffed_hours="8",
                    accepted_output="16", quality_passed="16", rework_count="0",
                    evidence="Repair and roster evidence", source_key="form-day1")
        data.update(updates)
        return ObservationForm(data)

    def study_form(self, *, instance=None, **updates):
        fields = StudyForm._meta.fields
        data = {field: getattr(self.study, field) for field in fields}
        data.update(company=self.company.pk, team=self.team.pk, name="New declared baseline")
        data.update(updates)
        return StudyForm(data, instance=instance)

    def test_scope_requires_membership_and_does_not_imply_companywide_access(self):
        self.assertIn(self.study, visible_studies_for(self.employee))
        self.assertNotIn(self.study, visible_studies_for(self.outsider))
        # Even all capability permissions do not grant an unrelated company.
        foreign = BaselineStudy.objects.create(company=self.other_company, name="Foreign")
        self.assertNotIn(foreign, visible_studies_for(self.manager))

    def test_employee_cannot_see_colleague_observations_or_export_them(self):
        own = self.observation()
        other = self.observation(employee=self.colleague, source_key="colleague-private-evidence", evidence="Not available to other employees")
        self.assertIn(own, visible_observations_for(self.employee))
        self.assertNotIn(other, visible_observations_for(self.employee))
        exported = baseline_csv(self.employee, self.study)
        self.assertNotIn("colleague-private-evidence", exported)
        self.assertIn("roster-employee-day1", exported)

    def test_summary_is_employee_scoped(self):
        accept_observation(self.observation(), self.manager)
        accept_observation(self.observation(employee=self.colleague, source_key="colleague-day1", accepted_output=Decimal("40")), self.manager)
        self.assertEqual(baseline_summary(self.study, user=self.employee)["accepted_output"], Decimal("16"))
        self.assertEqual(baseline_summary(self.study, user=self.manager)["accepted_output"], Decimal("56"))

    def test_revoked_manager_loses_acceptance_and_export_access(self):
        observation = self.observation()
        TeamMembership.objects.filter(pk=self.manager_membership.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            accept_observation(observation, self.manager)
        with self.assertRaises(BaselineStudy.DoesNotExist):
            baseline_csv(self.manager, self.study)

    def test_revoked_employee_loses_even_own_baseline_scope(self):
        self.observation()
        TeamMembership.objects.filter(team=self.team, user=self.employee).update(is_active=False)
        self.assertFalse(visible_studies_for(self.employee).exists())
        self.assertFalse(visible_observations_for(self.employee).exists())

    def test_employee_can_submit_own_observation_and_snapshots_are_filled(self):
        form = self.observation_form()
        self.assertTrue(form.is_valid(), form.errors)
        observation = save_observation(form, self.employee)
        self.assertEqual(observation.role, self.study.role)
        self.assertEqual(observation.workload_type, self.study.workload_type)
        self.assertEqual(observation.status, "DRAFT")

    def test_employee_cannot_submit_for_colleague_or_accept_own(self):
        form = self.observation_form(employee=self.colleague)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(PermissionDenied):
            save_observation(form, self.employee)
        with self.assertRaises(PermissionDenied):
            accept_observation(self.observation(), self.employee)

    def test_manager_cannot_forge_cross_team_employee(self):
        form = self.observation_form(employee=self.outsider)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            save_observation(form, self.manager)

    def test_inactive_superuser_has_no_access(self):
        inactive = get_user_model().objects.create_user(username="inactive-baseline-admin", is_superuser=True, is_active=False)
        self.assertFalse(visible_studies_for(inactive).exists())
        with self.assertRaises(PermissionDenied):
            accept_observation(self.observation(), inactive)

    def test_membership_without_read_capability_does_not_expose_baseline(self):
        unprivileged = get_user_model().objects.create_user(username="baseline-no-permission")
        TeamMembership.objects.create(team=self.team, user=unprivileged)
        self.assertFalse(visible_studies_for(unprivileged).exists())
        self.assertFalse(visible_observations_for(unprivileged).exists())

    def test_collection_requires_definitions_and_dates(self):
        study = BaselineStudy(company=self.company, team=self.team, name="Undefined", status="COLLECTING")
        with self.assertRaises(ValidationError) as caught:
            study.full_clean()
        self.assertIn("effort_definition", caught.exception.message_dict)
        self.assertIn("start_date", caught.exception.message_dict)

    def test_pilot_must_follow_entire_baseline_window(self):
        self.study.pilot_start_date = self.study.end_date
        with self.assertRaises(ValidationError):
            self.study.full_clean()

    def test_server_records_actual_first_collection_start(self):
        form = self.study_form()
        self.assertTrue(form.is_valid(), form.errors)
        before = timezone.now()
        study = save_study(form, self.manager)
        self.assertGreaterEqual(study.collection_started_at, before)
        self.assertTrue(baseline_summary(study, user=self.manager)["pre_rollout_verified"])

    def test_collection_cannot_start_after_known_pilot_already_started(self):
        form = self.study_form(pilot_start_date=self.today)
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError) as caught:
            save_study(form, self.manager)
        self.assertIn("pilot_start_date", caught.exception.message_dict)

    def test_actual_collection_start_is_immutable(self):
        form = self.study_form()
        self.assertTrue(form.is_valid(), form.errors)
        study = save_study(form, self.manager)
        study.collection_started_at -= timedelta(days=5)
        with self.assertRaises(ValidationError) as caught:
            study.full_clean()
        self.assertIn("collection_started_at", caught.exception.message_dict)

    def test_later_pilot_date_cannot_precede_actual_collection_start(self):
        form = self.study_form()
        self.assertTrue(form.is_valid(), form.errors)
        study = save_study(form, self.manager)
        study.pilot_start_date = self.today
        with self.assertRaises(ValidationError) as caught:
            study.full_clean()
        self.assertIn("pilot_start_date", caught.exception.message_dict)

    def test_unknown_pilot_date_does_not_claim_pre_rollout_verification(self):
        form = self.study_form(pilot_start_date="")
        self.assertTrue(form.is_valid(), form.errors)
        study = save_study(form, self.manager)
        self.assertIsNotNone(study.collection_started_at)
        self.assertFalse(baseline_summary(study, user=self.manager)["pre_rollout_verified"])

    def test_outside_window_or_future_observation_rejected(self):
        for value in (self.study.start_date-timedelta(days=1), self.today, self.today+timedelta(days=1)):
            form = self.observation_form(observed_on=value.isoformat())
            self.assertFalse(form.is_valid())
            self.assertIn("observed_on", form.errors)

    def test_draft_study_cannot_collect(self):
        self.study.status = "DRAFT"
        self.study.save(update_fields=["status"])
        form = self.observation_form()
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            save_observation(form, self.employee)

    def test_replayed_source_is_not_counted_twice_even_after_retraction(self):
        accepted = accept_observation(self.observation(), self.manager)
        accept_observation(accepted, self.manager)
        self.assertEqual(baseline_summary(self.study, user=self.manager)["accepted_count"], 1)
        retract_observation(accepted, self.manager, "Incorrect source value")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.observation(observed_on=self.today-timedelta(days=3))

    def test_duplicate_same_employee_day_workload_is_rejected(self):
        self.observation()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.observation(source_key="different-key-same-work")

    def test_retraction_preserves_acceptance_history_and_allows_corrected_source(self):
        observation = accept_observation(self.observation(), self.manager)
        retracted = retract_observation(observation, self.manager, "Rework was omitted")
        self.assertEqual(retracted.status, "RETRACTED")
        self.assertEqual(retracted.acceptance_history[0]["accepted_output"], "16.000")
        self.assertEqual(retracted.acceptance_history[1]["reason"], "Rework was omitted")
        self.assertEqual(baseline_summary(self.study, user=self.manager)["accepted_count"], 0)
        corrected = self.observation(source_key="corrected-roster-day1", accepted_output=Decimal("14"), rework_count=2)
        accept_observation(corrected, self.manager)
        self.assertEqual(baseline_summary(self.study, user=self.manager)["accepted_output"], Decimal("14"))

    def test_accepted_observations_cannot_be_edited_through_service(self):
        observation = accept_observation(self.observation(), self.manager)
        form = self.observation_form(source_key=observation.source_key)
        form.instance = observation
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(ValidationError):
            save_observation(form, self.manager)
        with self.assertRaises(ValidationError):
            observation.delete(user=self.manager)

    def test_missing_measurements_stay_unknown_and_ratio_uses_complete_rows_only(self):
        observation = self.observation(staffed_hours=None, quality_passed=None)
        accept_observation(observation, self.manager)
        summary = baseline_summary(self.study, user=self.manager)
        self.assertIsNone(summary["output_per_staffed_hour"])
        self.assertIsNone(summary["staffed_hours"])
        self.assertEqual(summary["missing_staffed_hours"], 1)
        self.assertEqual(summary["missing_quality"], 1)
        self.assertEqual(summary["completeness_percent"], Decimal("0"))

    def test_zero_hours_never_produces_infinite_productivity(self):
        accept_observation(self.observation(staffed_hours=Decimal("0")), self.manager)
        self.assertIsNone(baseline_summary(self.study, user=self.manager)["output_per_staffed_hour"])

    def test_accepted_evidence_needed_and_task_activity_does_not_change_baseline(self):
        from tasker.models import Task
        empty = self.observation(evidence="")
        with self.assertRaises(ValidationError):
            accept_observation(empty, self.manager)
        Task.objects.create(title="Checklists are not output", owner=self.employee, progress=100, status="COMPLETED")
        summary = baseline_summary(self.study, user=self.manager)
        self.assertEqual(summary["accepted_count"], 0)
        self.assertIsNone(summary["output_per_staffed_hour"])

    def test_mismatched_company_team_and_changed_definition_rejected(self):
        self.study.company = self.other_company
        with self.assertRaises(ValidationError):
            self.study.full_clean()
        self.study.company = self.company
        self.observation()
        self.study.output_definition = "Count clicks instead"
        with self.assertRaises(ValidationError):
            self.study.full_clean()

    def test_csv_formula_escaping_and_unknown_values(self):
        self.study.name = "=DANGEROUS()"
        self.study.save(update_fields=["name"])
        self.observation(evidence="  +formula", staffed_hours=None, quality_passed=None)
        exported = baseline_csv(self.manager, self.study)
        self.assertIn("'=DANGEROUS()", exported)
        self.assertIn("'  +formula", exported)
        self.assertIn("Unknown", exported)

    def test_empty_study_has_unknown_completeness_no_uplift_claim(self):
        summary = baseline_summary(self.study, user=self.manager)
        self.assertIsNone(summary["completeness_percent"])
        self.assertIsNone(summary["output_per_staffed_hour"])
        self.assertFalse(summary["comparison_available"])

    def test_company_grant_requires_matching_capability(self):
        OrganizationAccess.objects.create(company=self.company, user=self.employee, can_manage=True, can_view_all=True)
        self.assertNotIn(self.observation(employee=self.colleague), visible_observations_for(self.employee))
        form = StudyForm({"company": self.company.pk, "team": self.team.pk, "name": "Unauthorized", "status": "DRAFT"})
        self.assertTrue(form.is_valid(), form.errors)
        with self.assertRaises(PermissionDenied):
            save_study(form, self.employee)

    def revoke_permission_during_scope_lock(self, user, codename):
        from tasker.services.baseline import _lock_scope
        permission = Permission.objects.get(content_type__app_label="tasker", codename=codename)
        self.assertTrue(user.has_perm(f"tasker.{codename}"))  # Simulate cached workspace/form capability.

        def after_lock(study):
            _lock_scope(study)
            user.user_permissions.remove(permission)

        return patch("tasker.services.baseline._lock_scope", side_effect=after_lock)

    def test_study_write_rechecks_permission_revoked_while_waiting_for_scope_lock(self):
        form = self.study_form()
        self.assertTrue(form.is_valid(), form.errors)
        with self.revoke_permission_during_scope_lock(self.manager, "add_baselinestudy"):
            with self.assertRaises(PermissionDenied):
                save_study(form, self.manager)

    def test_observation_write_rechecks_permission_revoked_while_waiting_for_scope_lock(self):
        form = self.observation_form()
        self.assertTrue(form.is_valid(), form.errors)
        with self.revoke_permission_during_scope_lock(self.employee, "add_baselineobservation"):
            with self.assertRaises(PermissionDenied):
                save_observation(form, self.employee)

    def test_acceptance_rechecks_permission_revoked_while_waiting_for_scope_lock(self):
        observation = self.observation()
        with self.revoke_permission_during_scope_lock(self.manager, "change_baselineobservation"):
            with self.assertRaises(PermissionDenied):
                accept_observation(observation, self.manager)
        observation.refresh_from_db()
        self.assertEqual(observation.status, "DRAFT")

    def test_retraction_rechecks_permission_revoked_while_waiting_for_scope_lock(self):
        observation = accept_observation(self.observation(), self.manager)
        with self.revoke_permission_during_scope_lock(self.manager, "change_baselineobservation"):
            with self.assertRaises(PermissionDenied):
                retract_observation(observation, self.manager, "Needs correction")
        observation.refresh_from_db()
        self.assertEqual(observation.status, "ACCEPTED")

    def test_superuser_deactivated_while_waiting_cannot_accept_with_stale_identity(self):
        from tasker.services.baseline import _lock_scope
        user = get_user_model().objects.create_user(username="baseline-stale-admin", is_superuser=True)
        observation = self.observation()

        def after_lock(study):
            _lock_scope(study)
            get_user_model().objects.filter(pk=user.pk).update(is_active=False)

        with patch("tasker.services.baseline._lock_scope", side_effect=after_lock):
            with self.assertRaises(PermissionDenied):
                accept_observation(observation, user)
