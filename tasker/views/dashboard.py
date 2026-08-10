from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

from tasker.forms import QuickAddTaskForm, QuickNotesForm
from tasker.services.dashboard import build_today_dashboard, create_quick_task, save_quick_notes
from tasker.ai.personal_assistant import personal_ai_availability


@login_required
@permission_required("tasker.view_task", raise_exception=True)
@require_http_methods(["GET", "POST"])
def index(request):
    quick_add_form = QuickAddTaskForm()
    quick_notes_form = None

    if request.method == "POST":
        action = request.POST.get("action")
        if action == "quick_add":
            if not request.user.has_perm("tasker.add_task"):
                raise PermissionDenied
            quick_add_form = QuickAddTaskForm(request.POST)
            if quick_add_form.is_valid():
                create_quick_task(
                    user=request.user,
                    title=quick_add_form.cleaned_data["title"],
                    priority=quick_add_form.cleaned_data["priority"],
                    due_time=quick_add_form.cleaned_data["due_time"],
                    category=quick_add_form.cleaned_data["category"],
                    estimated_duration=quick_add_form.estimated_duration(),
                )
                messages.success(request, "Task added to today.")
                next_url = request.POST.get("next", "")
                if next_url and url_has_allowed_host_and_scheme(
                    next_url,
                    allowed_hosts={request.get_host()},
                    require_https=request.is_secure(),
                ):
                    return redirect(next_url)
                return redirect("ims:tasker:index")
        elif action == "quick_notes":
            if not (
                request.user.has_perm("tasker.add_dailyplan")
                and request.user.has_perm("tasker.change_dailyplan")
            ):
                raise PermissionDenied
            quick_notes_form = QuickNotesForm(request.POST)
            if quick_notes_form.is_valid():
                save_quick_notes(
                    user=request.user,
                    notes=quick_notes_form.cleaned_data["quick_notes"],
                )
                messages.success(request, "Quick notes saved.")
                return redirect("ims:tasker:index")
        else:
            raise PermissionDenied

    dashboard = build_today_dashboard(user=request.user)
    if quick_notes_form is None:
        quick_notes_form = QuickNotesForm(initial={"quick_notes": dashboard.quick_notes})
    return render(
        request,
        "tasker/index.html",
        {
            "dashboard": dashboard,
            "quick_add_form": quick_add_form,
            "quick_notes_form": quick_notes_form,
            "ai_availability": personal_ai_availability(request.user),
        },
    )
