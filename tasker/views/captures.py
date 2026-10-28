"""Private-by-default capture; mutations use the common locked services."""
import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from tasker.capture_forms import CaptureCreateForm, CaptureEditForm, CapturePromotionForm, promotion_goals_for
from tasker.models import CaptureItem, DailyPlan
from tasker.organization import visible_teams_for
from tasker.services import captures as service
from tasker.services.goals import visible_goals_for, visible_initiatives_for
from tasker.policies import visible_tasks_for


def payload(item):
    return {"id": item.pk, "kind": item.kind, "title": item.title, "content": item.content,
            "url": item.url, "tags": item.tags, "pinned": item.pinned, "version": item.version,
            "visibility": item.visibility, "triage_state": item.triage_state,
            "shared_team_id": item.shared_team_id, "shared_team_name": str(item.shared_team) if item.shared_team_id else ""}


def _visible(request, pk, *, deleted=False):
    return get_object_or_404(service.visible_captures_for(request.user, include_deleted=deleted), pk=pk)


def _detail(request, item, *, status=200, form=None, error="", manual_conflict=False):
    owner = item.owner_id == request.user.pk
    initial = payload(item)
    initial["tags"] = ", ".join(item.tags)
    form = form or CaptureEditForm(initial=initial)
    return render(request, "tasker/capture/detail.html", {
        "item": item, "form": form, "editable": owner and not item.is_deleted,
        "is_owner": owner, "editor_data": {"item": payload(item), "version": item.version},
        "can_promote": owner and (request.user.has_perm("tasker.add_task") or promotion_goals_for(request.user).exists()),
        "share_teams": visible_teams_for(request.user) if owner else [], "capture_error": error, "manual_conflict": manual_conflict,
        "promoted_task_visible": bool(item.promoted_task_id and visible_tasks_for(request.user).filter(pk=item.promoted_task_id).exists()),
        "promoted_initiative_visible": bool(item.promoted_initiative_id and visible_initiatives_for(request.user).filter(pk=item.promoted_initiative_id).exists()),
    }, status=status)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def capture_list(request):
    state = request.GET.get("state", "inbox")
    if state not in ("inbox", "archive", "trash", "all"):
        state = "inbox"
    items = service.visible_captures_for(request.user, include_deleted=state == "trash")
    if state == "trash":
        items = items.filter(owner=request.user, is_deleted=True)
    elif state == "archive":
        items = items.filter(triage_state="ARCHIVED")
    elif state == "inbox":
        items = items.exclude(triage_state="ARCHIVED")
    query = request.GET.get("q", "").strip()[:300]
    kind = request.GET.get("kind", "")
    tag = request.GET.get("tag", "").strip().casefold()[:40]
    if query:
        items = items.filter(Q(title__icontains=query) | Q(content__icontains=query) | Q(original_text__icontains=query) | Q(url__icontains=query))
    if kind in dict(CaptureItem.Kind.choices):
        items = items.filter(kind=kind)
    if tag:
        items = items.filter(tags__contains=[tag])
    if request.GET.get("pinned") == "1":
        items = items.filter(pinned=True)
    page = Paginator(items.select_related("owner", "shared_team").order_by("-pinned", "-updated_at", "-pk"), 24).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return render(request, "tasker/capture/list.html", {"page": page, "state": state, "q": query, "kind": kind, "tag": tag, "kinds": CaptureItem.Kind.choices, "filter_query": params.urlencode()})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def capture_create(request):
    prefix = "quick_capture" if request.method == "POST" and "quick_capture-client_key" in request.POST else None
    form = CaptureCreateForm(request.POST if request.method == "POST" else None, prefix=prefix)
    if request.method == "POST" and form.is_valid():
        try:
            item = service.create_capture(user=request.user, **form.cleaned_data)
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Saved privately to your Inbox. No task was scheduled.")
            return redirect("ims:tasker:capture_detail", pk=item.pk)
    return render(request, "tasker/capture/create.html", {"form": form}, status=400 if request.method == "POST" else 200)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def capture_detail(request, pk):
    return _detail(request, _visible(request, pk, deleted=True))


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def capture_save(request, pk):
    item = _visible(request, pk)
    if item.owner_id != request.user.pk:
        raise PermissionDenied
    if request.content_type != "application/json":
        form = CaptureEditForm(request.POST)
        if form.is_valid():
            try:
                service.update_capture(user=request.user, pk=pk, **form.cleaned_data)
            except service.CaptureConflict as conflict:
                draft = request.POST.copy()
                draft["version"] = conflict.version
                return _detail(request, conflict.item, status=409, form=CaptureEditForm(draft), manual_conflict=True, error="A newer version exists. Your draft is retained. Compare the saved text below before explicitly replacing this version.")
            except ValidationError as error:
                form.add_error(None, error)
            else:
                messages.success(request, "Capture saved.")
                return redirect("ims:tasker:capture_detail", pk=pk)
        return _detail(request, item, status=400, form=form, error="The capture was not saved. Correct the form below.")
    try:
        data = json.loads(request.body)
        allowed = {"version", "kind", "title", "content", "url", "tags", "pinned"}
        if not isinstance(data, dict) or set(data) - allowed or type(data.get("version")) is not int:
            raise ValueError
        if any(key in data and not isinstance(data[key], str) for key in ("kind", "title", "content", "url")):
            raise ValueError
        if "pinned" in data and type(data["pinned"]) is not bool:
            raise ValueError
        tags = data.get("tags", [])
        if isinstance(tags, list):
            if not all(isinstance(tag, str) and "," not in tag for tag in tags):
                raise ValueError
            data["tags"] = ",".join(tags)
        elif not isinstance(tags, str):
            raise ValueError
    except (ValueError, TypeError, UnicodeDecodeError):
        return JsonResponse({"ok": False, "message": "Invalid save request. Your draft has not been saved."}, status=400)
    form = CaptureEditForm(data)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": dict(form.errors), "message": "Correct the highlighted fields. Your draft is retained."}, status=400)
    try:
        saved = service.update_capture(user=request.user, pk=pk, **form.cleaned_data)
    except service.CaptureConflict as conflict:
        return JsonResponse({"ok": False, "conflict": True, "version": conflict.version, "item": payload(conflict.item)}, status=409)
    except ValidationError as error:
        return JsonResponse({"ok": False, "message": " ".join(error.messages)}, status=400)
    return JsonResponse({"ok": True, "version": saved.version, "item": payload(saved)})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def capture_action(request, pk, action):
    item = _visible(request, pk, deleted=True)
    if item.owner_id != request.user.pk:
        raise PermissionDenied
    try:
        version = int(request.POST.get("version", ""))
        team = None
        if action == "share":
            team = get_object_or_404(visible_teams_for(request.user), pk=request.POST.get("team"))
        item = service.mutate_capture(user=request.user, pk=pk, version=version, action=action, team=team)
    except service.CaptureConflict as conflict:
        return _detail(request, conflict.item, status=409, error="This capture changed. Review the current version before repeating the action.")
    except (ValueError, ValidationError) as error:
        message = " ".join(error.messages) if isinstance(error, ValidationError) else "Use the current capture version."
        return _detail(request, item, status=400, error=message)
    messages.success(request, "Capture updated.")
    return redirect("ims:tasker:capture_detail", pk=item.pk)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_http_methods(["GET", "POST"])
def capture_promote(request, pk):
    item = _visible(request, pk)
    if item.owner_id != request.user.pk:
        raise PermissionDenied
    form = CapturePromotionForm(request.POST if request.method == "POST" else None, user=request.user,
                                initial={"version": item.version, "title": item.title or item.content.splitlines()[0][:255], "target": "task"})
    if not form.fields["target"].choices:
        raise PermissionDenied
    if request.method == "POST" and form.is_valid():
        values = dict(form.cleaned_data)
        values.pop("confirm_copy")
        try:
            service.promote_capture(user=request.user, pk=pk, **values)
        except service.CaptureConflict:
            form.add_error(None, "This capture changed. Return to it and review the saved text before promoting.")
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, "Work record ready. Repeating promotion will reuse this same record. Your original capture visibility is unchanged.")
            return redirect("ims:tasker:capture_detail", pk=pk)
    return render(request, "tasker/capture/promote.html", {"form": form, "item": item}, status=400 if request.method == "POST" else 200)


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_GET
def capture_legacy(request):
    if not request.user.has_perm("tasker.view_dailyplan"):
        raise PermissionDenied
    plans = DailyPlan.all_objects.filter(owner=request.user).exclude(quick_notes="").order_by("-plan_date", "-pk")
    imported = set(CaptureItem.all_objects.filter(owner=request.user, legacy_plan__isnull=False).values_list("legacy_plan_id", flat=True))
    page = Paginator(plans, 20).get_page(request.GET.get("page"))
    return render(request, "tasker/capture/legacy.html", {"page": page, "imported_plan_ids": imported})


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@never_cache
@require_POST
def capture_import_legacy(request, plan_pk):
    if not request.user.has_perm("tasker.view_dailyplan"):
        raise PermissionDenied
    get_object_or_404(DailyPlan.all_objects, pk=plan_pk, owner=request.user)
    try:
        item = service.import_legacy_notes(user=request.user, plan_pk=plan_pk)
    except ValidationError as error:
        messages.error(request, " ".join(error.messages))
        return redirect("ims:tasker:capture_legacy")
    return redirect("ims:tasker:capture_detail", pk=item.pk)
