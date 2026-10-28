def tasker_ui(request):
    """Expose module scope without coupling shared templates to a URL prefix."""
    match = getattr(request, "resolver_match", None)
    is_tasker_request = bool(match and "tasker" in match.app_names)
    context = {"is_tasker_request": is_tasker_request}
    from django.conf import settings
    context["tasker_direction_enabled"] = getattr(settings, "TASKER_DIRECTION_ENABLED", False)
    context["tasker_coordination_enabled"] = getattr(settings, "TASKER_COORDINATION_ENABLED", False)
    context["tasker_reports_enabled"] = getattr(settings, "TASKER_REPORTS_ENABLED", False)
    if is_tasker_request:
        from .navigation import shell_navigation
        context.update(shell_navigation(request))
        if context["tasker_reports_enabled"] and request.user.is_authenticated and request.user.has_perm("tasker.view_task"):
            from .services.reporting import report_teams_for
            context["tasker_reports_available"] = report_teams_for(request.user).exists()
        if request.user.has_perm("tasker.view_task"):
            from .capture_forms import CaptureCreateForm
            context["capture_create_form"] = CaptureCreateForm(prefix="quick_capture")
            context["capture_create_form"].fields["content"].widget.attrs["rows"] = 4
    if is_tasker_request and request.user.has_perm("tasker.add_task"):
        from .forms import QuickAddTaskForm

        context["quick_add_form"] = QuickAddTaskForm()
    return context
