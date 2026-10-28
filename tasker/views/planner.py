from datetime import timedelta
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import never_cache
from django.urls import reverse
from django.conf import settings

from tasker.forms import DailyPlanForm, DailyReviewForm
from tasker.models import DailyPlan, DailyReview
from tasker.services.planner import save_daily_plan, save_daily_review
from tasker.services.capacity import capacity_for_day
from tasker.views.planning import planning_date


def _date_redirect(request,route,day):
    return redirect(reverse(route)+("?date="+day.isoformat() if request.GET.get("date") else ""))


@login_required
@permission_required("tasker.view_dailyplan", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def daily_plan(request):
    plan_date = planning_date(request)
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
        try:
            save_daily_plan(form=form, user=request.user, plan_date=plan_date)
        except ValidationError as error:
            form.add_error(None,error)
        else:
            messages.success(request, "Daily intentions saved. Accept time blocks separately after checking capacity.")
            return _date_redirect(request,"ims:tasker:daily_plan",plan_date)

    review = DailyReview.objects.filter(plan=plan).first() if plan else None
    capacity,capacity_error=None,""
    if request.user.has_perm("tasker.view_task"):
        try:
            capacity=capacity_for_day(user=request.user,day=plan_date)
        except ValidationError as error:
            capacity_error=" ".join(error.messages)
    return render(
        request,
        "tasker/daily_plan.html",
        {
            "form": form,
            "plan": plan,
            "plan_date": plan_date,
            "review": review,
            "can_edit": can_edit,
            "previous_date":plan_date-timedelta(days=1),"next_date":plan_date+timedelta(days=1),
            "capacity":capacity,"capacity_error":capacity_error,
        },
    )


@login_required
@permission_required("tasker.view_dailyreview", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def daily_review(request):
    plan_date = planning_date(request)
    plan = DailyPlan.objects.filter(owner=request.user, plan_date=plan_date).first()
    if plan is None:
        messages.info(request, "Save the selected day's intentions before completing its review.")
        return _date_redirect(request,"ims:tasker:daily_plan",plan_date)

    review = DailyReview.objects.filter(plan=plan).first()
    can_edit = _can_edit(
        request.user,
        review,
        add_permission="tasker.add_dailyreview",
        change_permission="tasker.change_dailyreview",
    ) and plan_date == planning_date_for_today(request)
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
        if getattr(settings, "TASKER_REPORTS_ENABLED", False):
            from tasker.models import EndDaySubmission
            if EndDaySubmission.objects.filter(owner=request.user, review=form.instance).exists():
                messages.info(request, "Shared End day facts are recorded for your team report. Email preparation and delivery are tracked separately.")
            else:
                messages.info(request, "No active reporting team is configured for you. Your private review is saved; no team report submission was created.")
        return _date_redirect(request,"ims:tasker:daily_review",plan_date)

    submissions = []
    if review and getattr(settings, "TASKER_REPORTS_ENABLED", False):
        from tasker.services.reporting import visible_submissions_for
        seen = set()
        for submission in visible_submissions_for(request.user).filter(owner=request.user, review=review).select_related("team").order_by("team_id", "-version")[:100]:
            if submission.team_id not in seen:
                submissions.append(submission)
                seen.add(submission.team_id)
    return render(
        request,
        "tasker/daily_review.html",
        {"form": form, "plan": plan, "review": review, "can_edit": can_edit,"plan_date":plan_date,"review_readonly_date":plan_date != planning_date_for_today(request), "report_submissions": submissions},
    )


def _can_edit(user, instance, *, add_permission, change_permission):
    return user.has_perm(change_permission) if instance else user.has_perm(add_permission)


def planning_date_for_today(request):
    from tasker.views.planning import policy_zone
    return timezone.now().astimezone(policy_zone(request.user)).date()
