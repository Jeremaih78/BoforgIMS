def analytics_allowed(request):
    internal_paths = ("/ai/", "/ims/", "/admin/", "/django-admin/")

    user = getattr(request, "user", None)

    # Block staff/admin users everywhere
    if user and user.is_authenticated and (user.is_staff or user.is_superuser):
        return {"allow_analytics": False}

    # Block internal system URLs
    path = (getattr(request, "path", "") or "").lower()
    if path.startswith(internal_paths):
        return {"allow_analytics": False}

    return {"allow_analytics": True}
