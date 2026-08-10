def analytics_allowed(request):
    internal_paths = ("/ai/", "/ims/", "/admin/", "/django-admin/")
    internal_apps = {"admin", "ims", "tasker"}

    user = getattr(request, "user", None)

    # Block staff/admin users everywhere
    if user and user.is_authenticated and (user.is_staff or user.is_superuser):
        return {"allow_analytics": False}

    # Block internal system URLs
    path = (getattr(request, "path", "") or "").lower()
    match = getattr(request, "resolver_match", None)
    app_names = set(match.app_names) if match else set()
    if path.startswith(internal_paths) or app_names.intersection(internal_apps):
        return {"allow_analytics": False}

    return {"allow_analytics": True}
