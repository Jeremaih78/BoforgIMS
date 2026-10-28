"""Self-service arrival ledger and currently authorized team history."""
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from tasker.arrival_forms import ArrivalCorrectionForm
from tasker.arrival_models import ArrivalDeclaration
from tasker.services.arrival import (
    arrival_teams_for, correct_arrival, current_business_zone, declared_datetime,
    register_arrival, today_arrival, visible_arrivals_for,
)


def _home_context(request, *, failed_record=None, failed_form=None):
    zone_error, local_today, today_record = None, None, None
    try:
        zone = current_business_zone(request.user)
        local_today = timezone.now().astimezone(zone).date()
        today_record = today_arrival(request.user)
    except ValidationError as exc:
        zone_error = " ".join(exc.messages)
        zone = None
    teams = arrival_teams_for(request.user)
    selected_team = None
    if request.GET.get("team"):
        raw=request.GET["team"]
        if not raw.isdecimal() or len(raw)>18 or int(raw)<1:
            raise Http404
        try:
            team_id = forms.IntegerField(min_value=1).clean(request.GET["team"])
        except ValidationError:
            raise Http404 from None
        selected_team = get_object_or_404(teams, pk=team_id)
    date_field = forms.DateField(required=False)
    date_error = None
    try:
        selected_date = date_field.clean(request.GET.get("date")) or local_today
    except ValidationError:
        selected_date, date_error = local_today, "Choose a valid date."
    if selected_team:
        records = visible_arrivals_for(request.user, team=selected_team, local_date=selected_date)
    else:
        records = visible_arrivals_for(request.user)
    if failed_record is not None:
        selected_team = None
        records = visible_arrivals_for(request.user).filter(pk=failed_record)
    page = Paginator(records, 30).get_page(request.GET.get("page"))
    rows = []
    for record in page.object_list:
        form = None
        if record.user_id == request.user.pk:
            form = failed_form if failed_record == record.pk else ArrivalCorrectionForm(arrival=record, prefix=f"arrival-{record.pk}")
        rows.append({"record": record, "form": form})
    filter_query = request.GET.copy()
    filter_query.pop("page", None)
    return {"arrival_today": today_record, "local_today": local_today, "arrival_timezone": zone.key if zone else None,
        "timezone_error": zone_error, "teams": teams, "selected_team": selected_team, "selected_date": selected_date,
        "date_error": date_error, "page": page, "arrival_rows": rows, "filter_query": filter_query.urlencode()}


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def arrival_home(request):
    return render(request, "tasker/arrival/home.html", _home_context(request))


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def arrival_register(request):
    try:
        record, created = register_arrival(user=request.user)
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, "Arrival registered." if created else "Your arrival is already registered for this local date.")
    return redirect("ims:tasker:arrival_home")


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def arrival_correct(request, pk):
    if pk < 1 or len(str(pk))>18:
        raise Http404
    record = get_object_or_404(ArrivalDeclaration, pk=pk, user_id=request.user.pk)
    form = ArrivalCorrectionForm(request.POST, arrival=record, prefix=f"arrival-{record.pk}")
    if form.is_valid():
        try:
            correct_arrival(user=request.user, arrival_id=record.pk,
                effective_at=declared_datetime(record, form.cleaned_data["declared_time"]),
                reason=form.cleaned_data["reason"], client_key=form.cleaned_data["client_key"],
                expected_revision=form.cleaned_data["expected_revision"])
        except ValidationError as exc:
            form.add_error(None, " ".join(exc.messages))
        else:
            messages.success(request, "Correction recorded. The original declaration and full history are preserved.")
            return redirect("ims:tasker:arrival_home")
    return render(request, "tasker/arrival/home.html",
        _home_context(request, failed_record=record.pk, failed_form=form), status=400)
