from django.contrib.auth.decorators import login_required, permission_required
from django.shortcuts import render

from tasker.services.ai_usage import build_ai_usage_dashboard


@login_required
@permission_required("tasker.view_ai_usage", raise_exception=True)
def ai_usage_dashboard(request):
    return render(
        request,
        "tasker/ai_usage_dashboard.html",
        {"usage": build_ai_usage_dashboard()},
    )
