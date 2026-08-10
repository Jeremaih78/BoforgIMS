def tasker_ui(request):
    """Expose module scope without coupling shared templates to a URL prefix."""
    match = getattr(request, "resolver_match", None)
    return {"is_tasker_request": bool(match and "tasker" in match.app_names)}
