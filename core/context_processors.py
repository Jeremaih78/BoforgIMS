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


def ims_navigation(request):
    from core.permissions import allowed
    user = getattr(request, 'user', None)
    if not user or not getattr(user, 'is_active', False):
        return {'ims_can_view_finance': False}
    return {'ims_can_view_finance': allowed(user, 'accounting.view_journalentry')}
