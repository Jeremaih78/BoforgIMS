from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from tasker.forms import DailyPlanForm, DailyReviewForm
from tasker.models import DailyPlan, DailyReview
from tasker.services.planner import save_daily_plan, save_daily_review


@login_required
@permission_required("tasker.view_dailyplan", raise_exception=True)
@require_http_methods(["GET", "POST"])
def daily_plan(request):
    plan_date = timezone.localdate()
    plan = (
        DailyPlan.objects.filter(owner=request.user, plan_date=plan_date)
        .prefetch_related("top_priorities", "planned_tasks")
        .first()
    )
    can_edit = _can_edit(
        request.user,
        plan,
        add_permission="tasker.add_dailyplan",
        change_permission="tasker.change_dailyplan",
    )
    if request.method == "POST" and not can_edit:
        raise PermissionDenied

    instance = plan or DailyPlan(owner=request.user, plan_date=plan_date)
    form = DailyPlanForm(
        request.POST or None,
        instance=instance,
        user=request.user,
        plan_date=plan_date,
    )
    if request.method == "POST" and form.is_valid():
        save_daily_plan(form=form, user=request.user, plan_date=plan_date)
        messages.success(request, "Daily plan saved. Your priorities are clear.")
        return redirect("ims:tasker:daily_plan")

    review = DailyReview.objects.filter(plan=plan).first() if plan else None
    return render(
        request,
        "tasker/daily_plan.html",
        {
            "form": form,
            "plan": plan,
            "plan_date": plan_date,
            "review": review,
            "can_edit": can_edit,
        },
    )


@login_required
@permission_required("tasker.view_dailyreview", raise_exception=True)
@require_http_methods(["GET", "POST"])
def daily_review(request):
    plan_date = timezone.localdate()
    plan = DailyPlan.objects.filter(owner=request.user, plan_date=plan_date).first()
    if plan is None:
        messages.info(request, "Save today’s plan before completing the end of day review.")
        return redirect("ims:tasker:daily_plan")

    review = DailyReview.objects.filter(plan=plan).first()
    can_edit = _can_edit(
        request.user,
        review,
        add_permission="tasker.add_dailyreview",
        change_permission="tasker.change_dailyreview",
    )
    if request.method == "POST" and not can_edit:
        raise PermissionDenied

    instance = review or DailyReview(plan=plan)
    form = DailyReviewForm(
        request.POST or None,
        instance=instance,
        user=request.user,
        plan=plan,
    )
    if request.method == "POST" and form.is_valid():
        save_daily_review(form=form, user=request.user, plan=plan)
        messages.success(request, "Day reviewed. Tomorrow now has a clearer starting point.")
        return redirect("ims:tasker:daily_review")

    return render(
        request,
        "tasker/daily_review.html",
        {"form": form, "plan": plan, "review": review, "can_edit": can_edit},
    )


def _can_edit(user, instance, *, add_permission, change_permission):
    return user.has_perm(change_permission) if instance else user.has_perm(add_permission)
