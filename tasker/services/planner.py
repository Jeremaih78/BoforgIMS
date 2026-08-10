from django.db import transaction
from tasker.models import Task


@transaction.atomic
def save_daily_plan(*, form, user, plan_date):
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
    review = form.save(commit=False)
    is_new = review.pk is None
    previous_ids = set(review.carry_over_tasks.values_list("pk", flat=True)) if not is_new else set()
    review.plan = plan
    if is_new:
        review.created_by = user
    review.updated_by = user
    review.save()
    form.save_m2m()

    selected_ids = set(form.cleaned_data["carry_over_tasks"].values_list("pk", flat=True))
    for task in Task.objects.filter(owner=user, pk__in=selected_ids):
        if not task.carry_forward:
            task.carry_forward = True
            task.updated_by = user
            task.save(update_fields=["carry_forward", "updated_by", "updated_at"])
    for task in Task.objects.filter(owner=user, pk__in=previous_ids - selected_ids):
        if task.carry_forward:
            task.carry_forward = False
            task.updated_by = user
            task.save(update_fields=["carry_forward", "updated_by", "updated_at"])
    return review
