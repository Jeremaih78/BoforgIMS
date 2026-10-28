"""Scoped baseline collection and export from declared, accepted evidence.

Task counts, timer durations, arrival and browser events are deliberately not
inputs to these calculations. A ratio is shown only for complete accepted rows.
"""
from __future__ import annotations

import csv
import io
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from tasker.models import BaselineObservation, BaselineStudy, OrganizationAccess, Team, TeamMembership
from tasker.organization import can_manage_company, can_manage_team, company_read_ids


def _active(user):
    return bool(user and user.is_authenticated and user.is_active)


def _current_actor(user):
    """Refresh identity and permission caches after waiting for scope locks."""
    if not _active(user):
        raise PermissionDenied
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if current is None:
        raise PermissionDenied
    return current


def _member_teams(user):
    return TeamMembership.objects.filter(user=user, is_active=True, user__is_active=True,
                                         team__is_deleted=False, team__company__is_active=True).values("team_id")


def _member_companies(user):
    teams = TeamMembership.objects.filter(user=user, is_active=True, user__is_active=True,
                                         team__is_deleted=False, team__company__is_active=True).values("team__company_id")
    grants = OrganizationAccess.objects.filter(user=user, is_active=True, user__is_active=True,
                                              company__is_active=True).values("company_id")
    return Q(company_id__in=teams) | Q(company_id__in=grants)


def visible_studies_for(user):
    studies = BaselineStudy.objects.select_related("company", "team").filter(company__is_active=True).filter(Q(team__isnull=True) | Q(team__is_deleted=False))
    if not _active(user) or not user.has_perm("tasker.view_baselinestudy"):
        return studies.none()
    if user.is_superuser:
        return studies
    return studies.filter(Q(company_id__in=company_read_ids(user)) | Q(team_id__in=_member_teams(user)) | (Q(team__isnull=True) & _member_companies(user))).distinct()


def _can_manage(user, study):
    return can_manage_team(user, study.team_id) if study.team_id else can_manage_company(user, study.company_id)


def visible_observations_for(user):
    observations = BaselineObservation.objects.select_related("study", "study__company", "study__team", "employee", "accepted_by").filter(study__in=visible_studies_for(user))
    if not _active(user) or not user.has_perm("tasker.view_baselineobservation"):
        return observations.none()
    if user.is_superuser:
        return observations
    from tasker.organization import managed_teams_for
    return observations.filter(Q(employee=user) | Q(study__company_id__in=company_read_ids(user)) | Q(study__team_id__in=managed_teams_for(user))).distinct()


def _require_manage(user, study, permission):
    if not _active(user) or not user.has_perm(permission) or not _can_manage(user, study):
        raise PermissionDenied


def _lock_scope(study):
    # These parent locks are shared with membership/grant changes and delegation.
    from accounting.models import Company
    Company.objects.select_for_update().get(pk=study.company_id)
    if study.team_id:
        Team.objects.select_for_update().get(pk=study.team_id)


def _employee_in_scope(employee_id, study):
    members = TeamMembership.objects.filter(user_id=employee_id, user__is_active=True,
                                            is_active=True, team__is_deleted=False,
                                            team__company__is_active=True)
    if study.team_id:
        return members.filter(team_id=study.team_id).exists()
    return members.filter(team__company_id=study.company_id).exists() or OrganizationAccess.objects.filter(
        company_id=study.company_id, user_id=employee_id, user__is_active=True,
        is_active=True, company__is_active=True).exists()


@transaction.atomic
def save_study(form, user):
    study = form.save(commit=False)
    _lock_scope(study)
    user = _current_actor(user)
    previous = BaselineStudy.all_objects.select_for_update().filter(pk=study.pk).first() if study.pk else None
    _require_manage(user, study, "tasker.change_baselinestudy" if previous else "tasker.add_baselinestudy")
    if previous:
        if previous.is_deleted or not visible_studies_for(user).filter(pk=previous.pk).exists() or not _can_manage(user, previous):
            raise PermissionDenied
        if previous.company_id != study.company_id or previous.team_id != study.team_id:
            raise ValidationError("Create a separate study to change company or team scope.")
    study.collection_started_at = previous.collection_started_at if previous else None
    if study.status == BaselineStudy.Status.COLLECTING and study.collection_started_at is None:
        if study.pilot_start_date and study.pilot_start_date <= timezone.localdate():
            raise ValidationError({"pilot_start_date": "Baseline collection must begin before employee rollout. This pilot has already started."})
        study.collection_started_at = timezone.now()
    if not previous:
        study.created_by = user
    study.updated_by = user
    study.full_clean()
    study.save()
    form.save_m2m()
    return study


@transaction.atomic
def save_observation(form, user):
    observation = form.save(commit=False)
    study = BaselineStudy.objects.get(pk=observation.study_id)
    _lock_scope(study)
    user = _current_actor(user)
    study = BaselineStudy.objects.select_for_update().get(pk=study.pk)
    previous = BaselineObservation.all_objects.select_for_update().filter(pk=observation.pk).first() if observation.pk else None
    observation.study = study
    if not visible_studies_for(user).filter(pk=study.pk).exists():
        raise PermissionDenied
    if not user.has_perm("tasker.change_baselineobservation" if previous else "tasker.add_baselineobservation"):
        raise PermissionDenied
    if previous:
        if previous.is_deleted or previous.status != BaselineObservation.Status.DRAFT:
            raise ValidationError("Accepted or retracted observations are immutable. Record a new corrected observation with a distinct source reference.")
        if not visible_observations_for(user).filter(pk=previous.pk).exists():
            raise PermissionDenied
        if previous.study_id != study.pk:
            raise ValidationError("An observation cannot be moved to another study.")
    if not _can_manage(user, study) and observation.employee_id != user.pk:
        raise PermissionDenied
    if not _employee_in_scope(observation.employee_id, study):
        raise ValidationError({"employee": "Choose an active employee in this study's team or company."})
    if study.status != BaselineStudy.Status.COLLECTING:
        raise ValidationError({"study": "Start baseline collection before entering observations."})
    observation.role = study.role
    observation.workload_type = study.workload_type
    # Acceptance is a separate authorized action, never a submitted form field.
    observation.status = BaselineObservation.Status.DRAFT
    observation.accepted_at = observation.accepted_by = None
    observation.retracted_at = observation.retracted_by = None
    observation.retraction_reason = ""
    observation.acceptance_history = previous.acceptance_history if previous else []
    if not previous:
        observation.created_by = user
    observation.updated_by = user
    observation.full_clean()
    observation.save()
    form.save_m2m()
    return observation


@transaction.atomic
def accept_observation(observation, user):
    snapshot = BaselineObservation.objects.select_related("study").get(pk=observation.pk)
    study = snapshot.study
    _lock_scope(study)
    user = _current_actor(user)
    study = BaselineStudy.objects.select_for_update().get(pk=study.pk)
    observation = BaselineObservation.objects.select_for_update().get(pk=snapshot.pk, study=study)
    observation.study = study
    _require_manage(user, study, "tasker.change_baselineobservation")
    if not visible_observations_for(user).filter(pk=observation.pk).exists():
        raise PermissionDenied
    if observation.status == BaselineObservation.Status.ACCEPTED:
        return observation
    if observation.status != BaselineObservation.Status.DRAFT:
        raise ValidationError("Retracted evidence cannot be accepted again. Create a corrected source record.")
    if study.status == BaselineStudy.Status.DRAFT:
        raise ValidationError("Start this baseline study before accepting observations.")
    if not _employee_in_scope(observation.employee_id, study):
        raise ValidationError("This employee is no longer an active member of the study scope.")
    if not observation.evidence.strip():
        raise ValidationError({"evidence": "Add source evidence before accepting this observation."})
    observation.status = BaselineObservation.Status.ACCEPTED
    observation.accepted_at = timezone.now()
    observation.accepted_by = user
    observation.updated_by = user
    observation.acceptance_history = [*observation.acceptance_history, {
        "action": "accepted", "actor_id": user.pk, "at": observation.accepted_at.isoformat(),
        "staffed_hours": str(observation.staffed_hours) if observation.staffed_hours is not None else None,
        "accepted_output": str(observation.accepted_output) if observation.accepted_output is not None else None,
        "quality_passed": observation.quality_passed, "rework_count": observation.rework_count,
        "evidence": observation.evidence, "source_key": observation.source_key,
    }]
    observation.full_clean()
    observation.save()
    return observation


@transaction.atomic
def retract_observation(observation, user, reason):
    snapshot = BaselineObservation.objects.select_related("study").get(pk=observation.pk)
    _lock_scope(snapshot.study)
    user = _current_actor(user)
    study = BaselineStudy.objects.select_for_update().get(pk=snapshot.study_id)
    observation = BaselineObservation.objects.select_for_update().get(pk=snapshot.pk, study=study)
    observation.study = study
    _require_manage(user, observation.study, "tasker.change_baselineobservation")
    if not visible_observations_for(user).filter(pk=observation.pk).exists():
        raise PermissionDenied
    reason = str(reason).strip()
    if not reason or len(reason) > 500:
        raise ValidationError("A retraction reason of up to 500 characters is required.")
    if observation.status == BaselineObservation.Status.RETRACTED:
        return observation
    if observation.status != BaselineObservation.Status.ACCEPTED:
        raise ValidationError("Only accepted baseline evidence can be retracted.")
    observation.status = BaselineObservation.Status.RETRACTED
    observation.retracted_by = user
    observation.retracted_at = timezone.now()
    observation.retraction_reason = reason
    observation.updated_by = user
    observation.acceptance_history = [*observation.acceptance_history, {"action": "retracted", "actor_id": user.pk, "at": observation.retracted_at.isoformat(), "reason": reason}]
    observation.full_clean()
    observation.save()
    return observation


def baseline_summary(study, *, user):
    study = visible_studies_for(user).get(pk=study.pk)
    observations = visible_observations_for(user).filter(study=study)
    rows = list(observations.filter(status=BaselineObservation.Status.ACCEPTED))
    complete = [row for row in rows if row.staffed_hours is not None and row.accepted_output is not None and row.quality_passed is not None and row.rework_count is not None]
    hours = sum((row.staffed_hours for row in complete), Decimal("0"))
    output = sum((row.accepted_output for row in complete), Decimal("0"))
    ratio = output / hours if hours > 0 and complete else None
    accepted_count = len(rows)
    return {
        "accepted_count": accepted_count,
        "draft_count": observations.filter(status=BaselineObservation.Status.DRAFT).count(),
        "retracted_count": observations.filter(status=BaselineObservation.Status.RETRACTED).count(),
        "complete_count": len(complete),
        "incomplete_count": accepted_count - len(complete),
        "missing_staffed_hours": sum(row.staffed_hours is None for row in rows),
        "missing_output": sum(row.accepted_output is None for row in rows),
        "missing_quality": sum(row.quality_passed is None or row.rework_count is None for row in rows),
        "staffed_hours": hours if complete else None,
        "accepted_output": output if complete else None,
        "quality_passed": sum(row.quality_passed for row in complete) if complete else None,
        "rework_count": sum(row.rework_count for row in complete) if complete else None,
        "output_per_staffed_hour": ratio,
        "completeness_percent": Decimal(len(complete) * 100) / accepted_count if accepted_count else None,
        "unit": study.output_unit,
        "comparison_available": False,
        "collection_started_at": study.collection_started_at,
        "pilot_start_date": study.pilot_start_date,
        "pre_rollout_verified": bool(study.collection_started_at and study.pilot_start_date and timezone.localdate(study.collection_started_at) < study.pilot_start_date),
        "interpretation": "Baseline evidence only; productivity improvement has not been measured.",
    }


def _csv_cell(value):
    if value is None:
        return "Unknown"
    if isinstance(value, str) and (value.startswith(("\t", "\r")) or value.lstrip().startswith(("=", "+", "-", "@"))):
        return "'" + value
    return value


def baseline_csv(user, study):
    """Export the caller's authorized observations, without private user content."""
    study = visible_studies_for(user).get(pk=study.pk)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream)
    writer.writerow(["study", "employee_id", "date", "role", "workload_type", "output_unit", "status", "staffed_hours", "accepted_output", "quality_passed", "rework_count", "evidence", "source_key", "accepted_at", "accepted_by_id", "retraction_reason", "collection_started_at", "pilot_start_date"])
    for row in visible_observations_for(user).filter(study=study).order_by("observed_on", "employee_id", "pk").iterator(chunk_size=200):
        writer.writerow([_csv_cell(value) for value in [study.name, row.employee_id, row.observed_on.isoformat(), row.role, row.workload_type, study.output_unit, row.status, row.staffed_hours, row.accepted_output, row.quality_passed, row.rework_count, row.evidence, row.source_key, row.accepted_at.isoformat() if row.accepted_at else None, row.accepted_by_id, row.retraction_reason, study.collection_started_at.isoformat() if study.collection_started_at else None, study.pilot_start_date.isoformat() if study.pilot_start_date else None]])
    return stream.getvalue()
