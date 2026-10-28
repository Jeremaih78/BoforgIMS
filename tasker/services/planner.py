import hashlib
import json

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from tasker.models import DailyPlan, Task


def plan_version(plan):
    if plan is None or not plan.pk:
        state = "new"
    else:
        state = [plan.pk,plan.updated_at.isoformat(),plan.focus_area,plan.motivational_quote,plan.quick_notes,
                 sorted(plan.top_priorities.values_list("pk",flat=True)),sorted(plan.planned_tasks.values_list("pk",flat=True))]
    return hashlib.sha256(json.dumps(state,ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def save_daily_plan(*, form, user, plan_date):
    actor=get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk,is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_dailyplan"):
        raise PermissionDenied
    current=DailyPlan.objects.select_for_update().filter(owner=user,plan_date=plan_date).first()
    if form.instance.pk and (current is None or current.pk != form.instance.pk):
        raise PermissionDenied
    expected=form.cleaned_data.get("plan_version")
    if current and (not actor.has_perm("tasker.change_dailyplan") or not expected or expected != plan_version(current)):
        raise ValidationError("This day's intentions changed. Keep your draft, reload the saved plan and compare before saving.")
    if not current and not actor.has_perm("tasker.add_dailyplan"):
        raise PermissionDenied
    selected=set(form.cleaned_data["top_priorities"].values_list("pk",flat=True)) | set(form.cleaned_data["planned_tasks"].values_list("pk",flat=True))
    tasks=list(Task.all_objects.select_for_update().filter(pk__in=selected).order_by("pk"))
    if len(tasks)!=len(selected) or any(task.owner_id!=user.pk or task.is_deleted or task.archived or task.status==Task.Status.CANCELLED for task in tasks):
        raise ValidationError("Selected work changed ownership or availability. Review the current selection before saving.")
    plan = form.save(commit=False)
    is_new = plan.pk is None
    plan.owner = user
    plan.plan_date = plan_date
    if is_new:
        plan.created_by = user
    plan.updated_by = user
    plan.save()
    form.save_m2m()
    priorities = tuple(form.cleaned_data["top_priorities"])
    if priorities:
        plan.planned_tasks.add(*priorities)
    return plan


@transaction.atomic
def save_daily_review(*, form, user, plan):
    from zoneinfo import ZoneInfo
    from django.utils import timezone
    from tasker.models import WorkPolicy
    actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_dailyreview"):
        raise PermissionDenied
    user = actor
    policy=WorkPolicy.objects.filter(owner=user).first()
    today=timezone.now().astimezone(ZoneInfo(policy.timezone_name if policy else "Africa/Harare")).date()
    # Daily intentions already lock Plan -> Tasks. Preserve that order before
    # a new Review's plan FK is inserted, and recheck the canonical plan.
    plan = DailyPlan.objects.select_for_update().filter(pk=plan.pk, owner=user, plan_date=today).first()
    if plan is None:
        raise PermissionDenied
    review = form.save(commit=False)
    is_new = review.pk is None
    capability = "tasker.add_dailyreview" if is_new else "tasker.change_dailyreview"
    if not user.has_perm(capability) or (not is_new and review.plan_id != plan.pk):
        raise PermissionDenied
    previous_ids = set(review.carry_over_tasks.values_list("pk", flat=True)) if not is_new else set()
    selected_ids = {task.pk for task in form.cleaned_data["carry_over_tasks"]}
    # Review and M2M saves invalidate operator advice. Lock every Task that can
    # change before those writes, matching Task -> scope -> operator state in
    # Focus/Work. This also rejects a stale selected task before saving review
    # content or silently changing a newly assigned employee's carry-over.
    from accounting.models import Company
    from tasker.models import Team
    from tasker.organization import require_task_access
    tasks = list(Task.all_objects.select_for_update().filter(pk__in=previous_ids | selected_ids).order_by("pk"))
    records = {task.pk: task for task in tasks}
    if not selected_ids.issubset(records) or any(
        records[pk].owner_id != user.pk or records[pk].is_deleted or records[pk].archived
        or records[pk].status in (Task.Status.COMPLETED, Task.Status.CANCELLED)
        for pk in selected_ids
    ):
        raise PermissionDenied
    changed_tasks = [task for task in tasks if task.owner_id == user.pk and not task.is_deleted
                     and task.carry_forward != (task.pk in selected_ids)]
    authorized_tasks = [task for task in tasks if task.pk in selected_ids or task in changed_tasks]
    list(Company.objects.select_for_update().filter(pk__in={task.company_id for task in authorized_tasks if task.company_id}).order_by("pk"))
    list(Team.all_objects.select_for_update().filter(pk__in={task.team_id for task in authorized_tasks if task.team_id}).order_by("pk"))
    for task in authorized_tasks:
        # Carry-over is an owned planning intention authorized by the review
        # capability. It does not grant general editing of the Task itself.
        require_task_access(user, task, change=False, capability="tasker.view_task")
    review.plan = plan
    if is_new:
        review.created_by = user
    review.updated_by = user
    review.save()
    form.save_m2m()

    for task in changed_tasks:
        task.carry_forward = task.pk in selected_ids
        task.updated_by = user
        task.save(update_fields=["carry_forward", "updated_by", "updated_at"])
    from django.conf import settings
    if getattr(settings, "TASKER_REPORTS_ENABLED", False):
        from tasker.services.reporting import close_day
        close_day(user=user, plan=plan, review=review)
    return review
