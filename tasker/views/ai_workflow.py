"""Private asynchronous assistance with explicit review and owner-controlled erasure."""
import uuid

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import Http404, JsonResponse
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tasker.ai.exceptions import AIServiceError
from tasker.ai_workflow_forms import AIWorkForm, AIRevisionForm
from tasker.services import ai_workflow as workflow
from tasker.services.ai_proposals import get_proposal, apply_proposal, undo_proposal, cancel_proposal


def _enabled():
    if not getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False):
        raise Http404


def _error(exc):
    if isinstance(exc, AIServiceError):
        return exc.user_message
    return "; ".join(exc.messages) if isinstance(exc, ValidationError) else "This action is unavailable."


@login_required
@permission_required(("tasker.use_ai", "tasker.view_task"), raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def ai_work_home(request):
    _enabled()
    initial = {key: request.GET[key] for key in ("feature", "task", "capture", "team") if key in request.GET}
    form = AIWorkForm(request.POST if request.method == "POST" else None, user=request.user, initial=initial)
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            data = form.cleaned_data
            try:
                item = workflow.create_work_request(user=request.user, feature=data["feature"],
                    client_key=data["client_key"], question=data["question"],
                    **{key+"_id": data[key].pk if data[key] else None for key in ("task", "capture", "team")})
            except (ValidationError, AIServiceError) as exc:
                form.add_error(None, _error(exc))
                status = 409
            else:
                return redirect("ims:tasker:ai_work_detail", pk=item.pk)
    return render(request, "tasker/ai_workflow/home.html", {"form": form,
        "availability": workflow.workflow_availability(request.user),
        "history": workflow.visible_work_requests(request.user)}, status=status)


def _detail(request, item, *, status=200, error="", selected=()):
    proposal = None
    if hasattr(item, "proposal") and not item.content_redacted_at:
        proposal = get_proposal(user=request.user, proposal_id=item.proposal.pk)
    payload = proposal.payload if proposal and not proposal.content_redacted_at else {}
    actions = []
    tasks = {value["id"]: value.get("title", "Task") for value in item.context_snapshot.get("tasks", [])}
    for action in payload.get("actions", []):
        kind = action["kind"]
        if kind == "CHECKLIST_ADD":
            label = f'Add checklist step to {tasks.get(action["task_id"], "task")}: {action["title"]}'
        elif kind == "CAPTURE_METADATA":
            label = f'Classify selected capture as {action["capture_kind"]}: {action["title"]}; tags: {", ".join(action["tags"])}'
        elif kind == "PLAN_INTENTIONS":
            names = ", ".join(tasks.get(pk, f"Task {pk}") for pk in action["task_ids"])
            priorities = ", ".join(tasks.get(pk, f"Task {pk}") for pk in action["priority_ids"])
            label = f'Set intentions for {action["plan_date"]}: {names}. Top priorities: {priorities or "none"}.'
        else:
            label = f'Schedule {tasks.get(action["task_id"], "task")}: {action["starts_at"]} to {action["ends_at"]}'
        actions.append({"id": action["id"], "label": label, "selected": action["id"] in selected})
    return render(request, "tasker/ai_workflow/detail.html", {"item": item, "proposal": proposal,
        "payload": payload, "actions": actions, "error": error, "retry_key": uuid.uuid4(),
        "pending": item.status in ("QUEUED", "RUNNING"),
        "retryable": item.status in ("FAILED", "UNKNOWN", "CANCELLED") and not item.content_redacted_at,
        "proposal_expired": bool(proposal and proposal.expires_at <= timezone.now()),
        "events": proposal.events.all()[:10] if proposal else []}, status=status)


@login_required
@permission_required(("tasker.use_ai", "tasker.view_task"), raise_exception=True)
@require_GET
@never_cache
def ai_work_detail(request, pk):
    _enabled()
    return _detail(request, workflow.get_work_request(user=request.user, request_id=pk))


@login_required
@permission_required(("tasker.use_ai", "tasker.view_task"), raise_exception=True)
@require_GET
@never_cache
def ai_work_status(request, pk):
    _enabled()
    item = workflow.get_work_request(user=request.user, request_id=pk)
    return JsonResponse({"status": item.status, "label": item.get_status_display(),
        "revision": str(item.revision), "pending": item.status in ("QUEUED", "RUNNING")})


@login_required
@require_POST
@never_cache
def ai_work_action(request, pk, action):
    # Erasure and cancellation remain available to an owner after AI access is revoked.
    if action == "redact":
        from tasker.ai.privacy import redact_request
        redact_request(user=request.user, request_id=pk)
        messages.success(request, "Saved AI content removed. Cost audit records are retained.")
        return redirect("ims:tasker:index")
    if action == "cancel":
        form = AIRevisionForm(request.POST)
        if not form.is_valid():
            raise PermissionDenied
        try:
            workflow.cancel_work_request(user=request.user, request_id=pk, **form.cleaned_data)
        except ValidationError as exc:
            messages.warning(request, _error(exc))
        if getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False) and request.user.has_perms(("tasker.use_ai", "tasker.view_task")):
            return redirect("ims:tasker:ai_work_detail", pk=pk)
        return redirect("ims:tasker:index")
    _enabled()
    item = workflow.get_work_request(user=request.user, request_id=pk)
    try:
        if action == "retry":
            new = workflow.retry_work_request(user=request.user, request_id=pk, client_key=request.POST.get("client_key"))
            return redirect("ims:tasker:ai_work_detail", pk=new.pk)
        if action == "feedback":
            try:
                rating = int(request.POST.get("rating", ""))
            except ValueError:
                raise ValidationError("Choose helpful or unhelpful.") from None
            workflow.record_feedback(user=request.user, request_id=pk, rating=rating, comment=request.POST.get("comment", ""))
            messages.success(request, "Feedback saved.")
        elif action in ("apply", "undo", "dismiss"):
            form = AIRevisionForm(request.POST)
            if not form.is_valid() or not hasattr(item, "proposal"):
                raise ValidationError("Reload and review the current suggestion.")
            args = {"user": request.user, "proposal_id": item.proposal.pk, **form.cleaned_data}
            if action == "apply":
                apply_proposal(**args, selected_action_ids=request.POST.getlist("actions"))
                messages.success(request, "Selected changes applied.")
            elif action == "undo":
                undo_proposal(**args)
                messages.success(request, "AI changes undone.")
            else:
                cancel_proposal(**args)
                messages.success(request, "Suggestion dismissed.")
        else:
            raise Http404
    except (ValidationError, AIServiceError) as exc:
        item.refresh_from_db()
        return _detail(request, item, status=409, error=_error(exc), selected=request.POST.getlist("actions"))
    return redirect("ims:tasker:ai_work_detail", pk=pk)


@login_required
@permission_required(("tasker.use_ai", "tasker.view_task"), raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def ai_budget(request):
    _enabled()
    error = ""
    if request.method == "POST":
        from tasker.ai.privacy import redact_ai_content
        try:
            cutoff = parse_datetime(request.POST.get("before", ""))
            result = redact_ai_content(user=request.user, before=cutoff)
        except (ValidationError, ValueError) as exc:
            error = _error(exc)
        else:
            messages.success(request, f'Removed content from {result["requests"]} requests and {result["conversations"]} conversations.' +
                (" Repeat to remove the remaining older content." if result["remaining"] else ""))
            return redirect("ims:tasker:ai_budget")
    from tasker.ai.budgets import company_budget_status, enrolled_company
    company = enrolled_company(request.user)
    company_status = None
    if company:
        try:
            company_status = company_budget_status(request.user, company)
        except PermissionDenied:
            pass
    return render(request, "tasker/ai_workflow/budget.html", {"availability": workflow.workflow_availability(request.user),
        "company_status": company_status, "error": error}, status=400 if error else 200)
