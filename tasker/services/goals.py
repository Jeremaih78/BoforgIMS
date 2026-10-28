"""Scoped company direction, primary contribution and accepted evidence."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.utils import timezone

from accounting.models import Company
from tasker.goal_models import Goal, Initiative, KeyResult, OutcomeMeasurement, TaskContribution
from tasker.models import OrganizationEvent, Task, Team, TeamMembership
from tasker.organization import (
    can_manage_company, can_manage_team, company_read_ids, managed_teams_for,
    visible_companies_for, visible_teams_for,
)


def _active(user):
    return bool(user and user.is_authenticated and user.is_active)


def _current_actor(user):
    if not _active(user):
        raise PermissionDenied
    current = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if current is None:
        raise PermissionDenied
    return current


def visible_goals_for(user):
    queryset = Goal.objects.select_related("company", "team", "owner").filter(company__is_active=True)
    if not _active(user) or not user.has_perm("tasker.view_goal"):
        return queryset.none()
    return queryset.filter(company__in=visible_companies_for(user)).filter(
        Q(team__isnull=True) | Q(team__in=visible_teams_for(user))
    )


def visible_key_results_for(user):
    return KeyResult.objects.select_related("goal", "goal__company", "goal__team").filter(goal__in=visible_goals_for(user))


def visible_initiatives_for(user):
    return Initiative.objects.select_related("goal", "key_result", "goal__company", "goal__team").filter(goal__in=visible_goals_for(user))


def visible_outcomes_for(user):
    queryset = OutcomeMeasurement.objects.select_related("key_result", "key_result__goal", "employee", "task", "team")
    if not _active(user) or not user.has_perm("tasker.view_outcomemeasurement"):
        return queryset.none()
    return queryset.filter(key_result__in=visible_key_results_for(user)).filter(
        Q(employee=user) | Q(team__in=managed_teams_for(user)) | Q(company_id__in=company_read_ids(user))
    )


def _lock_scope(company_id, team_id=None):
    Company.objects.select_for_update().get(pk=company_id)
    if team_id:
        Team.all_objects.select_for_update().get(pk=team_id)


def _audit(obj, user, action, *, goal=None):
    if isinstance(obj, Goal):
        goal = obj
    elif goal is None:
        goal = obj.goal if isinstance(obj, (KeyResult, Initiative)) else obj.key_result.goal
    OrganizationEvent.objects.create(
        company_id=goal.company_id, team_id=getattr(obj, "team_id", goal.team_id),
        actor=user, action=action, object_type=obj._meta.model_name, object_id=str(obj.pk),
        details={"goal_id": goal.pk},
    )


def _can_manage(user, goal, permission):
    if not _active(user) or not user.has_perm(f"tasker.{permission}") or not goal.company.is_active or goal.is_deleted:
        return False
    return can_manage_company(user, goal.company_id) or (goal.team_id and can_manage_team(user, goal.team_id))


def _require_manage(user, goal, permission):
    if not _can_manage(user, goal, permission):
        raise PermissionDenied


def _can_manage_outcome(user, goal, team_id, permission):
    return (_active(user) and user.has_perm(f"tasker.{permission}") and goal.company.is_active
            and not goal.is_deleted and (can_manage_company(user, goal.company_id)
            or (team_id and can_manage_team(user, team_id) and (not goal.team_id or goal.team_id == team_id))))


def can_manage_outcome(user, outcome):
    """UI/admin affordance using the same scope/capability as acceptance."""
    return _can_manage_outcome(user, outcome.key_result.goal, outcome.team_id, "change_outcomemeasurement")


def _validate_owner(owner_id, goal):
    if not owner_id:
        return
    if not get_user_model().objects.filter(pk=owner_id, is_active=True).exists():
        raise ValidationError({"owner": "Choose an active employee."})
    if goal.team_id:
        eligible = TeamMembership.objects.filter(team_id=goal.team_id, user_id=owner_id, is_active=True).exists()
    else:
        from tasker.models import OrganizationAccess
        eligible = TeamMembership.objects.filter(team__company_id=goal.company_id, team__is_deleted=False, user_id=owner_id, is_active=True).exists() or OrganizationAccess.objects.filter(company_id=goal.company_id, user_id=owner_id, is_active=True).exists()
    if not eligible:
        raise ValidationError({"owner": "The accountable owner must belong to this company/team."})


@transaction.atomic
def save_goal(*, form, user):
    goal = form.save(commit=False)
    _lock_scope(goal.company_id, goal.team_id)
    user = _current_actor(user)
    previous = Goal.all_objects.select_for_update().get(pk=goal.pk) if goal.pk else None
    if previous:
        _require_manage(user, previous, "change_goal")
    _require_manage(user, goal, "change_goal" if previous else "add_goal")
    _validate_owner(goal.owner_id, goal)
    if previous is None:
        goal.created_by = user
    goal.updated_by = user
    goal.save()
    form.save_m2m()
    _audit(goal, user, "goal.updated" if previous else "goal.created")
    return goal


@transaction.atomic
def save_key_result(*, form, user):
    result = form.save(commit=False)
    goal = Goal.objects.select_related("company", "team").get(pk=result.goal_id)
    _lock_scope(goal.company_id, goal.team_id)
    user = _current_actor(user)
    if result.pk:
        previous = KeyResult.objects.select_for_update().select_related("goal__company").get(pk=result.pk)
        _require_manage(user, previous.goal, "change_keyresult")
    _require_manage(user, goal, "change_keyresult" if result.pk else "add_keyresult")
    _validate_owner(result.owner_id, goal)
    if not result.pk:
        result.created_by = user
    result.updated_by = user
    result.save()
    form.save_m2m()
    _audit(result, user, "key_result.saved")
    return result


@transaction.atomic
def save_initiative(*, form, user):
    initiative = form.save(commit=False)
    goal = Goal.objects.select_related("company", "team").get(pk=initiative.goal_id)
    _lock_scope(goal.company_id, goal.team_id)
    user = _current_actor(user)
    if initiative.pk:
        previous = Initiative.objects.select_for_update().select_related("goal__company").get(pk=initiative.pk)
        _require_manage(user, previous.goal, "change_initiative")
    _require_manage(user, goal, "change_initiative" if initiative.pk else "add_initiative")
    _validate_owner(initiative.owner_id, goal)
    if not initiative.pk:
        initiative.created_by = user
    initiative.updated_by = user
    initiative.save()
    form.save_m2m()
    _audit(initiative, user, "initiative.saved")
    return initiative


@transaction.atomic
def link_task_contribution(*, task, initiative, user):
    from tasker.policies import can_change_task
    task = Task.objects.select_for_update().get(pk=task.pk)
    initiative = visible_initiatives_for(user).filter(pk=initiative.pk).first()
    if not initiative or not can_change_task(user, task) or not user.has_perm("tasker.change_taskcontribution"):
        raise PermissionDenied
    _lock_scope(initiative.goal.company_id, initiative.goal.team_id)
    user = _current_actor(user)
    initiative = visible_initiatives_for(user).filter(pk=initiative.pk).first()
    if not initiative or not can_change_task(user, task) or not user.has_perm("tasker.change_taskcontribution"):
        raise PermissionDenied
    contribution = TaskContribution.all_objects.select_for_update().filter(task=task).first()
    if contribution:
        contribution.initiative = initiative
        contribution.is_deleted = False
        contribution.deleted_at = None
        contribution.deleted_by = None
        contribution.updated_by = user
    else:
        contribution = TaskContribution(task=task, initiative=initiative, created_by=user, updated_by=user)
    contribution.save()
    _audit(contribution, user, "task_contribution.linked", goal=initiative.goal)
    return contribution


def _employee_in_scope(employee_id, company_id, team_id):
    if not get_user_model().objects.filter(pk=employee_id, is_active=True).exists():
        return False
    if team_id:
        return TeamMembership.objects.filter(team_id=team_id, user_id=employee_id, is_active=True, team__is_deleted=False).exists()
    from tasker.models import OrganizationAccess
    return TeamMembership.objects.filter(team__company_id=company_id, team__is_deleted=False, user_id=employee_id, is_active=True).exists() or OrganizationAccess.objects.filter(company_id=company_id, user_id=employee_id, is_active=True).exists()


@transaction.atomic
def record_outcome(*, user, key_result, employee, value, evidence, source_type="manual", source_key, occurred_on=None, task=None, company=None, team=None):
    """Record a draft; a replay returns the same occurrence only if identical."""
    key_result = visible_key_results_for(user).filter(pk=key_result.pk).first()
    if not key_result or not user.has_perm("tasker.add_outcomemeasurement"):
        raise PermissionDenied
    goal = key_result.goal
    task = Task.objects.select_for_update().get(pk=task.pk) if task else None
    team_id = task.team_id if task else (goal.team_id or getattr(team, "pk", team))
    _lock_scope(goal.company_id, team_id)
    user = _current_actor(user)
    current_result = visible_key_results_for(user).filter(pk=key_result.pk).first()
    if current_result is None or not user.has_perm("tasker.add_outcomemeasurement"):
        raise PermissionDenied
    if current_result.goal_id != key_result.goal_id or current_result.goal.company_id != goal.company_id or current_result.goal.team_id != goal.team_id:
        raise ValidationError("The result's scope changed. Reload before recording evidence.")
    key_result, goal = current_result, current_result.goal
    if company is not None and getattr(company, "pk", company) != goal.company_id:
        raise ValidationError({"company": "The outcome must use the goal's company."})
    if team is not None and getattr(team, "pk", team) != team_id:
        raise ValidationError({"team": "The outcome must use the goal/task's team."})
    if team_id and not visible_teams_for(user).filter(pk=team_id, company_id=goal.company_id).exists():
        raise PermissionDenied
    if employee.pk != user.pk and not _can_manage_outcome(user, goal, team_id, "add_outcomemeasurement"):
        raise PermissionDenied
    if not _employee_in_scope(employee.pk, goal.company_id, team_id):
        raise ValidationError({"employee": "The employee must be an active member of this company/team."})
    if task:
        from tasker.policies import visible_tasks_for
        if not visible_tasks_for(user).filter(pk=task.pk).exists():
            raise PermissionDenied
    try:
        numeric_value = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValidationError({"value": "Enter a finite number."}) from exc
    data = dict(company_id=goal.company_id, team_id=team_id, key_result_id=key_result.pk, employee_id=employee.pk,
                task_id=task.pk if task else None, value=numeric_value, evidence=evidence.strip(),
                source_type=source_type.strip(), source_key=source_key.strip(), occurred_on=occurred_on or timezone.localdate())
    existing = OutcomeMeasurement.all_objects.select_for_update().filter(company_id=goal.company_id, source_type=data["source_type"], source_key=data["source_key"]).first()
    if existing:
        if existing.is_deleted or any(getattr(existing, field) != item for field, item in data.items()):
            raise ValidationError({"source_key": "This source occurrence already exists with different evidence. Reconcile it rather than counting it again."})
        return existing
    outcome = OutcomeMeasurement(**data, created_by=user, updated_by=user)
    outcome.save()
    _audit(outcome, user, "outcome.recorded")
    return outcome


def _task_evidence_valid(outcome):
    if not outcome.task_id:
        return True
    task = outcome.task
    return (not task.is_deleted and not task.archived and task.status == Task.Status.COMPLETED
            and task.owner_id == outcome.employee_id and task.company_id == outcome.company_id and task.team_id == outcome.team_id
            and TaskContribution.objects.filter(task=task, initiative__is_deleted=False,
                initiative__goal__is_deleted=False, initiative__key_result_id=outcome.key_result_id).exists())


def _history(outcome, *, status, actor, reason=""):
    outcome.acceptance_history = [*outcome.acceptance_history, {
        "status": status, "at": timezone.now().isoformat(), "actor_id": actor.pk if actor else None, "reason": reason,
    }]


@transaction.atomic
def accept_outcome(*, outcome, user):
    identity = OutcomeMeasurement.objects.values("task_id", "company_id", "team_id").get(pk=outcome.pk)
    if identity["task_id"]:
        Task.all_objects.select_for_update().get(pk=identity["task_id"])
    _lock_scope(identity["company_id"], identity["team_id"])
    user = _current_actor(user)
    outcome = OutcomeMeasurement.objects.select_for_update(of=("self",)).select_related("key_result__goal__company", "task").get(pk=outcome.pk)
    goal = outcome.key_result.goal
    if not _can_manage_outcome(user, goal, outcome.team_id, "change_outcomemeasurement"):
        raise PermissionDenied
    if goal.status != Goal.Status.ACTIVE or not outcome.key_result.unit or not (goal.evidence_policy.strip() or outcome.key_result.evidence_policy.strip()):
        raise ValidationError("Activate the goal and define the result's unit and acceptance evidence policy before accepting outputs.")
    if outcome.key_result.is_deleted or not _employee_in_scope(outcome.employee_id, outcome.company_id, outcome.team_id):
        raise ValidationError("The result and employee membership must still be active.")
    if not _task_evidence_valid(outcome):
        raise ValidationError("Task evidence requires a completed, live task with unchanged owner, scope and primary contribution.")
    if outcome.status == OutcomeMeasurement.Status.ACCEPTED:
        return outcome
    outcome.status = OutcomeMeasurement.Status.ACCEPTED
    outcome.accepted_at = timezone.now()
    outcome.accepted_by = user
    outcome.retracted_at = None
    outcome.retracted_by = None
    outcome.retraction_reason = ""
    outcome.updated_by = user
    _history(outcome, status=outcome.status, actor=user)
    outcome._acceptance_service = True
    outcome.save()
    _audit(outcome, user, "outcome.accepted")
    return outcome


@transaction.atomic
def retract_outcome(*, outcome, user, reason):
    identity = OutcomeMeasurement.objects.values("task_id", "company_id", "team_id").get(pk=outcome.pk)
    if identity["task_id"]:
        Task.all_objects.select_for_update().get(pk=identity["task_id"])
    _lock_scope(identity["company_id"], identity["team_id"])
    user = _current_actor(user)
    outcome = OutcomeMeasurement.objects.select_for_update(of=("self",)).select_related("key_result__goal__company").get(pk=outcome.pk)
    if not _can_manage_outcome(user, outcome.key_result.goal, outcome.team_id, "change_outcomemeasurement"):
        raise PermissionDenied
    if not reason.strip():
        raise ValidationError("Give a reason for retracting this evidence.")
    if len(reason.strip()) > 500:
        raise ValidationError("Keep the retraction reason within 500 characters.")
    if outcome.status == OutcomeMeasurement.Status.RETRACTED:
        return outcome
    outcome.status = OutcomeMeasurement.Status.RETRACTED
    outcome.retracted_at = timezone.now()
    outcome.retracted_by = user
    outcome.retraction_reason = reason.strip()
    outcome.updated_by = user
    _history(outcome, status=outcome.status, actor=user, reason=outcome.retraction_reason)
    outcome._acceptance_service = True
    outcome.save()
    _audit(outcome, user, "outcome.retracted")
    return outcome


@transaction.atomic
def reconcile_task_outcomes(*, task, user=None):
    """Reopening/reassigning never silently recycles previously accepted output."""
    task = Task.all_objects.select_for_update().get(pk=task.pk)
    if task.company_id:
        _lock_scope(task.company_id, task.team_id)
    for outcome in OutcomeMeasurement.objects.select_for_update(of=("self",)).filter(task_id=task.pk, status=OutcomeMeasurement.Status.ACCEPTED).select_related("task", "key_result__goal"):
        if _task_evidence_valid(outcome):
            continue
        outcome.status = OutcomeMeasurement.Status.RETRACTED
        outcome.retracted_at = timezone.now()
        outcome.retracted_by = user
        outcome.retraction_reason = "Task reopened, archived, deleted, reassigned or its contribution/scope changed; explicit acceptance is required again."
        outcome.updated_by = user
        _history(outcome, status=outcome.status, actor=user, reason=outcome.retraction_reason)
        # The old immutable evidence can deliberately refer to the former owner
        # or scope. Preserve it rather than rewriting history to fit the task.
        OutcomeMeasurement.all_objects.filter(pk=outcome.pk).update(
            status=outcome.status, retracted_at=outcome.retracted_at, retracted_by=user,
            retraction_reason=outcome.retraction_reason, updated_by=user, updated_at=timezone.now(),
            acceptance_history=outcome.acceptance_history,
        )
        _audit(outcome, user, "outcome.task_invalidated")


def key_result_progress(result):
    """Deterministic evidence progress; task percentages and timers are absent."""
    valid_task_contribution = Q(task__primary_contribution__is_deleted=False,
        task__primary_contribution__initiative__is_deleted=False,
        task__primary_contribution__initiative__key_result_id=result.pk)
    queryset = OutcomeMeasurement.objects.filter(key_result=result, status=OutcomeMeasurement.Status.ACCEPTED,
        key_result__is_deleted=False, key_result__goal__is_deleted=False, company__is_active=True).filter(
        Q(task__isnull=True) | Q(task__is_deleted=False, task__archived=False, task__status=Task.Status.COMPLETED,
            task__owner_id=F("employee_id"), task__company_id=F("company_id"), task__team_id=F("team_id")) & valid_task_contribution
    )
    # SQL NULL equality does not match company-only tasks. Include their
    # explicitly matching null-team records through a separate branch.
    company_only = OutcomeMeasurement.objects.filter(key_result=result, status=OutcomeMeasurement.Status.ACCEPTED,
        key_result__is_deleted=False, key_result__goal__is_deleted=False, company__is_active=True,
        team__isnull=True, task__team__isnull=True, task__isnull=False,
        task__is_deleted=False, task__archived=False, task__status=Task.Status.COMPLETED,
        task__owner_id=F("employee_id"), task__company_id=F("company_id")).filter(valid_task_contribution)
    queryset = (queryset | company_only).filter(occurred_on__lte=timezone.localdate()).order_by("-occurred_on", "-pk")
    start_date = result.start_date or result.goal.start_date
    end_date = result.end_date or result.goal.end_date
    if start_date:
        queryset = queryset.filter(occurred_on__gte=start_date)
    if end_date:
        queryset = queryset.filter(occurred_on__lte=end_date)
    count = queryset.count()
    if result.aggregation == KeyResult.Aggregation.LATEST:
        latest = queryset.values_list("value", flat=True).first()
        current = latest if latest is not None else result.baseline
    else:
        accepted_delta = queryset.aggregate(total=Sum("value"))["total"] or Decimal(0)
        current = result.baseline + accepted_delta if result.baseline is not None else None
    known = result.baseline is not None and result.target is not None and current is not None and bool(result.unit)
    progress = None
    if known:
        distance = result.target - result.baseline
        if not distance or (result.direction == KeyResult.Direction.HIGHER and distance < 0) or (result.direction == KeyResult.Direction.LOWER and distance > 0):
            known = False
        else:
            progress = max(Decimal(0), min(Decimal(100), ((current - result.baseline) / distance) * 100))
    return {"current": current, "baseline": result.baseline, "target": result.target,
            "progress": progress, "known": known, "accepted_count": count}
