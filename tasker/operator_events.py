"""Synchronous invalidation for the one Tasker recommendation service.

This is an invalidation seam, not a second work queue. Stage 8 may dispatch a
refresh after a committed domain event by calling ``invalidate_operator``;
recommendations themselves are built only by ``services.operator``. Calls in a
transaction participate in that transaction, so a rolled-back change cannot
invalidate an otherwise current recommendation. No task titles, notes, blocker
text, reflection text or availability notes are copied here.

Normal model/service mutations emit the signals below. Maintenance code that
uses QuerySet.update/bulk_update or recoverable QuerySet.delete/restore must call
the seam explicitly; those Django operations do not emit model save signals.
Execution must still recheck current authority and feasibility, regardless of
whether an invalidation was delivered.

Task-child and plan/review saves invalidate conservatively before subsequent
task saves in their domain service. Those services must lock all affected Tasks
before their first invalidation. Public callers must never take a scoped state
lock and then acquire a Task or upgrade to global invalidation in the same
transaction. The broad pilot invalidation trades refresh work for one lock order.
"""
from __future__ import annotations

import uuid

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.db import transaction
from django.db.models.signals import m2m_changed, post_delete, post_save
from django.dispatch import receiver
from django.utils import timezone


INVALIDATION_REASONS = frozenset({
    "task_changed", "availability_changed", "schedule_changed", "effort_changed",
    "dependency_changed", "plan_changed", "focus_changed", "scope_changed",
    "goal_changed", "policy_changed", "review_completed", "manual_refresh",
    "work_changed", "checklist_changed",
})

# These are persisted task fields used by eligibility, displayed instructions,
# context or ranking. Shared note changes deliberately do not interrupt advice.
TASK_FIELDS = frozenset({
    "title", "definition_of_done", "owner_id", "company_id", "team_id",
    "priority", "status", "estimated_duration", "actual_duration", "due_date",
    "due_time", "started_at", "completed_at", "progress", "carry_forward",
    "category_id", "parent_task_id", "archived", "blocked_reason", "is_deleted",
})


def invalidate_operator(*, user_ids=None, reason="work_changed", task_id=None):
    """Expire existing owner states with one opaque revision update.

    ``None`` means conservative invalidation of all existing states. A supplied
    iterable restricts it to those owners; an empty iterable changes nothing.
    ``task_id`` is accepted as event provenance for the future dispatch seam but
    is not stored, dereferenced or exposed. Only known reason codes are stored.
    This function creates neither users nor policies nor state rows.
    """
    state_model = apps.get_model("tasker", "OperatorState")
    queryset = state_model.objects.all()
    if user_ids is not None:
        owner_ids = {value for value in user_ids if type(value) is int and value > 0}
        if not owner_ids:
            return 0
        queryset = queryset.filter(owner_id__in=owner_ids)
    code = reason if isinstance(reason, str) and reason in INVALIDATION_REASONS else "work_changed"
    from django.conf import settings
    dispatch = getattr(settings, "TASKER_OPERATOR_DISPATCH_ENABLED", False)

    def apply_invalidation():
        revision = uuid.uuid4()
        count = queryset.update(revision=revision, reason=code, updated_at=timezone.now())
        if count and dispatch:
            from tasker.services.jobs import enqueue_operator_refresh
            for owner_id in queryset.values_list("owner_id", flat=True).iterator(chunk_size=100):
                enqueue_operator_refresh(owner_id=owner_id, revision=revision)
        return count

    if dispatch:
        # The revision and durable refresh request commit or roll back together,
        # including explicit invalidations outside a larger domain transaction.
        with transaction.atomic():
            return apply_invalidation()
    return apply_invalidation()


def _task_owner(task_id):
    task_model = apps.get_model("tasker", "Task")
    return task_model.all_objects.filter(pk=task_id).values_list("owner_id", flat=True).first()


def _owner_for(instance):
    owner_id = getattr(instance, "owner_id", None)
    if owner_id is not None:
        return owner_id
    task_id = getattr(instance, "task_id", None)
    if task_id is not None:
        return _task_owner(task_id)
    plan_id = getattr(instance, "plan_id", None)
    if plan_id is not None:
        return apps.get_model("tasker", "DailyPlan").all_objects.filter(pk=plan_id).values_list("owner_id", flat=True).first()
    return None


@receiver(post_save, sender="tasker.Task", dispatch_uid="tasker.operator.task.saved")
def task_changed(sender, instance, created, raw=False, update_fields=None, **kwargs):
    if raw:
        return
    changes = set(getattr(instance, "_tasker_changes", {})) & TASK_FIELDS
    if update_fields is not None:
        persisted = set(update_fields)
        persisted.update(f"{name}_id" for name in update_fields)
        changes &= persisted
    if not created and not changes:
        return
    # A prerequisite can be several links away and owned by another employee.
    # Conservative invalidation avoids retaining a transitive readiness result;
    # no task/source data crosses an audience boundary through this revision.
    invalidate_operator(reason="task_changed", task_id=instance.pk)


def _domain_changed(sender, instance, raw=False, **kwargs):
    if raw:
        return
    name = sender._meta.model_name
    if name == "task":
        invalidate_operator(reason="task_changed", task_id=instance.pk)
    elif name in {"taskdependency", "dependencygraphstate"}:
        invalidate_operator(reason="dependency_changed")
    elif name in {"taskcontribution", "goal", "initiative", "keyresult"}:
        invalidate_operator(reason="goal_changed")
    elif name in {"teammembership", "organizationaccess", "team", "company"}:
        invalidate_operator(reason="scope_changed")
    elif name in {"tasktimeentry", "taskchecklistitem", "dailyplan", "dailyreview"}:
        # log_time/checklist/progress/review services may save Tasks afterwards.
        # Acquire the same conservative state set on their first child write;
        # a scoped-then-global upgrade can deadlock two independent employees.
        code = {"tasktimeentry": "focus_changed", "taskchecklistitem": "checklist_changed",
                "dailyplan": "plan_changed", "dailyreview": "review_completed"}[name]
        invalidate_operator(reason=code)
    else:
        code = {
            "workpolicy": "policy_changed", "operatorpolicy": "policy_changed",
            "availability": "availability_changed", "scheduleblock": "schedule_changed",
            "taskeffortestimate": "effort_changed", "dailyplan": "plan_changed",
            "dailyreview": "review_completed", "tasktimeentry": "focus_changed",
            "taskchecklistitem": "checklist_changed",
        }.get(name, "work_changed")
        owner_id = _owner_for(instance)
        # Time may be recorded by an authorized actor other than the owner;
        # both recommendation streams can be affected by an active timer.
        owner_ids = [owner_id, getattr(instance, "user_id", None)]
        invalidate_operator(user_ids=owner_ids, reason=code)


for _label in (
    "tasker.WorkPolicy", "tasker.OperatorPolicy", "tasker.Availability",
    "tasker.ScheduleBlock", "tasker.TaskEffortEstimate", "tasker.TaskDependency",
    "tasker.DependencyGraphState", "tasker.TaskContribution", "tasker.Goal",
    "tasker.Initiative", "tasker.KeyResult", "tasker.DailyPlan", "tasker.DailyReview",
    "tasker.TaskTimeEntry", "tasker.TaskChecklistItem", "tasker.TeamMembership",
    "tasker.OrganizationAccess", "tasker.Team", "accounting.Company",
):
    post_save.connect(_domain_changed, sender=_label, dispatch_uid=f"tasker.operator.{_label}.saved")
    post_delete.connect(_domain_changed, sender=_label, dispatch_uid=f"tasker.operator.{_label}.deleted")

post_delete.connect(_domain_changed, sender="tasker.Task", dispatch_uid="tasker.operator.task.deleted")


def _plan_members_changed(sender, instance, action, reverse, pk_set, **kwargs):
    plan_model = apps.get_model("tasker", "DailyPlan")
    review_model = apps.get_model("tasker", "DailyReview")
    is_review = sender is review_model.carry_over_tasks.through
    cache_key = f"_operator_clear_{sender._meta.model_name}"
    if action == "pre_clear" and reverse:
        # Reverse clear has no post-clear IDs. Capture only owners before the
        # through rows disappear, never scratchpads or private review content.
        relation = sender.objects.filter(task_id=instance.pk)
        lookup = "dailyreview__plan__owner_id" if is_review else "dailyplan__owner_id"
        setattr(instance, cache_key, tuple(relation.values_list(lookup, flat=True)))
        return
    if action not in {"post_add", "post_remove", "post_clear"}:
        return
    if reverse:
        if action == "post_clear":
            owners = getattr(instance, cache_key, ())
            if hasattr(instance, cache_key):
                delattr(instance, cache_key)
        elif is_review:
            owners = review_model.all_objects.filter(pk__in=pk_set or ()).values_list("plan__owner_id", flat=True)
        else:
            owners = plan_model.all_objects.filter(pk__in=pk_set or ()).values_list("owner_id", flat=True)
    else:
        owners = [_owner_for(instance)]
    invalidate_operator(user_ids=owners, reason="review_completed" if is_review else "plan_changed")


for _model_name, _field in (
    ("DailyPlan", "top_priorities"), ("DailyPlan", "planned_tasks"),
    ("DailyReview", "carry_over_tasks"),
):
    _model = apps.get_model("tasker", _model_name)
    m2m_changed.connect(
        _plan_members_changed, sender=getattr(_model, _field).through,
        dispatch_uid=f"tasker.operator.{_model_name}.{_field}",
    )


def _permission_changed(sender, instance, action, **kwargs):
    if action in {"post_add", "post_remove", "post_clear"}:
        invalidate_operator(reason="scope_changed")


def _user_changed(sender, instance, raw=False, **kwargs):
    if not raw:
        # Even an active-user capability/role edit must invalidate an existing
        # proof. Password/profile writes are conservative harmless refreshes.
        invalidate_operator(user_ids=[instance.pk], reason="scope_changed")


_user_model = get_user_model()
post_save.connect(_user_changed, sender=_user_model, dispatch_uid="tasker.operator.user.saved")
for _through in (_user_model.user_permissions.through, _user_model.groups.through, Group.permissions.through):
    m2m_changed.connect(_permission_changed, sender=_through,
                        dispatch_uid=f"tasker.operator.permissions.{_through._meta.label_lower}")
