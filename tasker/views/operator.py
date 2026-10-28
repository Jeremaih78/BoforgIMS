"""Personal guidance and explicit user-controlled execution."""
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from tasker.operator_forms import OperatorActionForm, OperatorPolicyForm
from tasker.operator_models import DEFAULT_WEIGHTS, OperatorPolicy
from tasker.services.operator import build_operator, save_operator_policy
from tasker.services.operator_actions import OperatorActionConflict, block_recommendation, execute_recommendation
from tasker.services.operator_presentation import present_operator


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_GET
@never_cache
def operator_home(request):
    return render(request, "tasker/operator/home.html", {"operator": present_operator(build_operator(user=request.user))})


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
@never_cache
def operator_policy(request):
    policy = OperatorPolicy.objects.filter(owner=request.user).first()
    form = OperatorPolicyForm(request.POST if request.method == "POST" else None,
        initial={**(policy.weights if policy else DEFAULT_WEIGHTS), "expected_revision": str(policy.revision) if policy else "default-v1"})
    status = 200
    if request.method == "POST":
        status = 400
        if form.is_valid():
            try:
                save_operator_policy(user=request.user, weights=form.weights(), expected_revision=form.cleaned_data["expected_revision"])
            except (ValueError, ValidationError) as exc:
                form.add_error(None, "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc))
                status = 409
            else:
                messages.success(request, "Ranking preferences saved. New guidance uses this policy revision.")
                return redirect("ims:tasker:operator_home")
    return render(request, "tasker/operator/policy.html", {"form": form}, status=status)


def _action(request, *, block=False):
    form = OperatorActionForm(request.POST)
    error = "Review the recommendation and supply the required fields."
    status = 400
    if form.is_valid():
        data = form.cleaned_data
        try:
            if block:
                block_recommendation(user=request.user, snapshot_id=data["snapshot_id"], task_id=data["task_id"], reason=data["reason"])
            else:
                entry = execute_recommendation(user=request.user, snapshot_id=data["snapshot_id"], task_id=data["task_id"],
                    reason=data["reason"], choose_another=data["choose_another"])
        except (OperatorActionConflict, ValueError, ValidationError) as exc:
            error = "; ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
            status = 409
        else:
            if block:
                messages.success(request, "Blocker recorded. Waiting work is retained; guidance has been refreshed.")
                return redirect("ims:tasker:operator_home")
            return redirect("ims:tasker:focus_mode", slug=entry.task.slug)
    return render(request, "tasker/operator/action_error.html", {"error": error,
        "retained_reason": request.POST.get("reason", "")[:1000]}, status=status)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def operator_start(request):
    return _action(request)


@login_required
@permission_required("tasker.change_task", raise_exception=True)
@permission_required("tasker.view_task", raise_exception=True)
@require_POST
@never_cache
def operator_block(request):
    return _action(request, block=True)
