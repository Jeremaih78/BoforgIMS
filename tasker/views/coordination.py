"""Scoped coordination, quiet attention and factual report interfaces."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from copy import deepcopy
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.dateparse import parse_datetime
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounting.models import Company
from tasker.coordination_forms import CoordinationActionForm, CoordinationForm, NotificationPreferenceForm, ReportDateForm, ReportPolicyForm
from tasker.models import CoordinationRequest, ManagerReportSnapshot, NotificationDelivery, OutboxJob, ReportPolicy, Team
from tasker.organization import can_manage_company
from tasker.policies import can_change_task, visible_tasks_for
from tasker.services import coordination as requests, notifications, reporting
from tasker.services.jobs import jobs_health, retry_failed
from tasker.services.work_dates import work_date
from tasker.views.tasks import _task_for_user


def _enabled(name):
    if not getattr(settings, name, False):
        raise Http404


def _error(exc):
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def coordination_home(request):
    _enabled("TASKER_COORDINATION_ENABLED")
    state = "all" if request.GET.get("state") == "all" else "active"
    queryset = requests.visible_requests_for(request.user).select_related("task", "raised_by", "resolver")
    if state == "active":
        queryset = queryset.filter(status__in=("OPEN", "ACKNOWLEDGED"))
    return render(request, "tasker/coordination/home.html", {"page_obj": Paginator(queryset, 24).get_page(request.GET.get("page")), "state": state})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@permission_required("tasker.change_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def coordination_create(request, slug):
    _enabled("TASKER_COORDINATION_ENABLED")
    task = _task_for_user(request.user, slug)
    if not can_change_task(request.user, task):
        raise PermissionDenied
    form = CoordinationForm(request.POST if request.method == "POST" else None, user=request.user, task=task)
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                item = requests.create_request(user=request.user, task=task, **form.cleaned_data)
            except (ValueError, ValidationError) as exc:
                form.add_error(None, _error(exc))
                status = 409
            else:
                messages.success(request, "Request recorded. Its resolver will receive an alert through their delivery preferences.")
                return redirect("ims:tasker:coordination_detail", pk=item.pk)
    return render(request, "tasker/coordination/form.html", {"task": task, "form": form}, status=status)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def coordination_detail(request, pk):
    _enabled("TASKER_COORDINATION_ENABLED")
    item = requests.get_request(user=request.user, pk=pk)
    error, status = "", 200
    retained_reason = request.POST.get("reason", "")[:1000]
    if request.method == "POST":
        status = 400
        form = CoordinationActionForm(request.POST)
        if form.is_valid():
            common = {"user": request.user, "request_id": item.pk, "expected_revision": form.cleaned_data["expected_revision"]}
            try:
                action = request.POST.get("action")
                if action == "acknowledge":
                    requests.acknowledge_request(**common)
                elif action == "resolve":
                    requests.resolve_request(**common, resolution=form.cleaned_data["reason"])
                elif action == "cancel":
                    requests.cancel_request(**common, reason=form.cleaned_data["reason"])
                else:
                    raise ValidationError("Choose a supported response.")
            except (ValueError, ValidationError) as exc:
                error, status = _error(exc), 409
            else:
                messages.success(request, "Your response is recorded. Obsolete alerts will be cancelled.")
                return redirect("ims:tasker:coordination_detail", pk=item.pk)
        else:
            error = "The request version or response is invalid. Your draft is retained."
        # A conflicting audience or assignment must not reveal the newer source.
        item = requests.get_request(user=request.user, pk=pk)
    return render(request, "tasker/coordination/detail.html", {"item": item, "active": item.status in ("OPEN", "ACKNOWLEDGED"),
        "can_respond": request.user.pk == item.resolver_id or can_change_task(request.user, item.task),
        "can_cancel": request.user.pk == item.raised_by_id or can_change_task(request.user, item.task),
        "events": item.events.order_by("-created_at", "-pk")[:20], "error": error, "retained_reason": retained_reason}, status=status)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def notification_home(request):
    _enabled("TASKER_COORDINATION_ENABLED")
    queryset = notifications.visible_notifications_for(request.user, include_held=True).prefetch_related("deliveries")
    page = Paginator(queryset, 24).get_page(request.GET.get("page"))
    items = []
    for item in page:
        context = notifications.notification_context(item, request.user)
        items.append({"item": item, "context": context, "deliveries": list(item.deliveries.all())})
    return render(request, "tasker/coordination/notifications.html", {"page_obj": page, "items": items})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def notification_mark(request, pk):
    _enabled("TASKER_COORDINATION_ENABLED")
    try:
        notifications.mark_notification(user=request.user, notification_id=pk, acknowledge=request.POST.get("action") == "acknowledge")
    except (ValueError, ValidationError) as exc:
        messages.error(request, _error(exc))
    return redirect("ims:tasker:notification_home")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def notification_preferences(request):
    _enabled("TASKER_COORDINATION_ENABLED")
    effective = notifications.effective_notification_preferences(request.user)
    record = effective["record"]
    initial = {name: getattr(record, name) for name in ("timezone_name", "quiet_enabled", "quiet_start", "quiet_end", "respect_work_hours", "suppress_during_focus", "digest_mode")}
    initial["expected_revision"] = effective["revision"]
    form = NotificationPreferenceForm(request.POST if request.method == "POST" else None, initial=initial)
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                notifications.save_notification_preferences(user=request.user, **form.cleaned_data)
            except (ValueError, ValidationError) as exc:
                form.add_error(None, _error(exc))
                status = 409
            else:
                messages.success(request, "Notification preferences saved. Held alerts will be rechecked by the scheduler.")
                return redirect("ims:tasker:notification_home")
    return render(request, "tasker/coordination/preferences.html", {"form": form}, status=status)


def _report_date(request):
    today = work_date(request.user)
    form = ReportDateForm(request.GET if "report_date" in request.GET else None, initial={"report_date": today})
    day = today
    if form.is_bound:
        if not form.is_valid():
            return form, None
        day = form.cleaned_data["report_date"]
        if day > today or day.year < 1900:
            form.add_error("report_date", "Choose today or an earlier date from 1900 onward.")
            return form, None
    return form, day


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def report_home(request):
    _enabled("TASKER_REPORTS_ENABLED")
    form, day = _report_date(request)
    teams = list(reporting.report_teams_for(request.user).select_related("company")[:100])
    if not teams:
        raise PermissionDenied
    reports = reporting.visible_reports_for(request.user).select_related("team__company")
    if day:
        reports = reports.filter(report_date=day)
    else:
        reports = reports.none()
    page = Paginator(reports, 24).get_page(request.GET.get("page"))
    companies = []
    seen = set()
    for team in teams:
        if team.company_id not in seen and can_manage_company(request.user, team.company_id):
            seen.add(team.company_id)
            companies.append(team.company)
    return render(request, "tasker/coordination/reports.html", {"form": form, "report_date": day,
        "teams": teams, "companies": companies, "page_obj": page}, status=200 if day else 400)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def report_build(request, pk):
    _enabled("TASKER_REPORTS_ENABLED")
    team = get_object_or_404(reporting.report_teams_for(request.user), pk=pk)
    form = ReportDateForm(request.POST)
    if not form.is_valid() or form.cleaned_data["report_date"] > work_date(request.user):
        messages.error(request, "Choose today or a valid earlier report date.")
        return redirect("ims:tasker:report_home")
    try:
        report = reporting.build_team_report(user=request.user, team=team, report_date=form.cleaned_data["report_date"])
    except (ValueError, ValidationError) as exc:
        messages.error(request, _error(exc))
        return redirect("ims:tasker:report_home")
    messages.success(request, "Factual team report generated. Email status is shown separately.")
    return redirect("ims:tasker:report_detail", pk=report.pk)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def report_detail(request, pk):
    _enabled("TASKER_REPORTS_ENABLED")
    report = reporting.get_report(user=request.user, pk=pk)
    members = deepcopy(report.facts.get("members", []))
    task_ids = {task["task_id"] for member in members for task in member.get("accepted_tasks", [])}
    # Report audience rights deliberately do not grant underlying Task access.
    links = {task_id: reverse("ims:tasker:task_detail", args=(slug,)) for task_id, slug in
        visible_tasks_for(request.user).filter(pk__in=task_ids).values_list("pk", "slug")}
    for member in members:
        member["role_label"] = {"MANAGER": "Team manager", "EMPLOYEE": "Employee"}.get(member.get("role"), "Team member")
        arrival = member.get("arrival", {})
        arrival["effective_display"] = parse_datetime(arrival["effective_at"]) if arrival.get("effective_at") else None
        for task in member.get("accepted_tasks", []):
            task["url"] = links.get(task["task_id"], "")
        for task in member.get("commitments", []):
            task["kind_label"] = {"DAILY_INTENTION": "Daily intention", "ACCEPTED_TIME_BLOCK": "Accepted time block"}.get(task.get("kind"), "Commitment")
        for task in member.get("deadline_risks", []):
            task["risk_label"] = {"OVERDUE": "Overdue", "DUE_TODAY": "Due today"}.get(task.get("risk"), "Review deadline")
    return render(request, "tasker/coordination/report_detail.html", {"report": report, "facts": report.facts,
        "display_members": members, "has_omissions": any(report.facts.get("omitted", {}).values()),
        "summary": reporting.report_status_summary(report), "deliveries": report.deliveries.select_related("recipient").order_by("queued_at", "pk")[:100]})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def report_policy(request, pk):
    _enabled("TASKER_REPORTS_ENABLED")
    company = get_object_or_404(Company, pk=pk, is_active=True)
    if not can_manage_company(request.user, company):
        raise PermissionDenied
    record = ReportPolicy.objects.filter(company=company).first() or ReportPolicy(company=company)
    names = ("timezone_name", "cutoff_time", "consolidation", "correction_policy", "missing_policy", "weekdays", "email_mode", "retention_days")
    initial = {name: getattr(record, name) for name in names}
    initial["expected_revision"] = str(record.revision) if record.pk else "unconfigured-v1"
    form = ReportPolicyForm(request.POST if request.method == "POST" else None, initial=initial)
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                reporting.save_report_policy(user=request.user, company=company, **form.cleaned_data)
            except (ValueError, ValidationError) as exc:
                form.add_error(None, _error(exc))
                status = 409
            else:
                messages.success(request, "Test report policy saved. Live email delivery is not activated.")
                return redirect("ims:tasker:report_home")
    return render(request, "tasker/coordination/report_policy.html", {"company": company, "form": form}, status=status)


@login_required
@require_GET
@never_cache
def jobs_home(request):
    _enabled("TASKER_COORDINATION_ENABLED")
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    jobs = OutboxJob.objects.order_by("-created_at", "pk")
    return render(request, "tasker/coordination/jobs.html", {"health": jobs_health(), "page_obj": Paginator(jobs, 30).get_page(request.GET.get("page"))})


@login_required
@require_POST
@never_cache
def jobs_retry(request, pk):
    _enabled("TASKER_COORDINATION_ENABLED")
    if not request.user.is_active or not request.user.is_superuser:
        raise PermissionDenied
    try:
        retry_failed(user=request.user, job_id=pk)
    except (ValueError, ValidationError) as exc:
        messages.error(request, _error(exc))
    else:
        messages.success(request, "Failed job queued for a bounded retry.")
    return redirect("ims:tasker:jobs_home")
