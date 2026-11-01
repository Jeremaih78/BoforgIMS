def tasker_ui(request):
    """Expose module scope without coupling shared templates to a URL prefix."""
    match = getattr(request, "resolver_match", None)
    is_tasker_request = bool(match and "tasker" in match.app_names)
    context = {"is_tasker_request": is_tasker_request}
    from django.conf import settings
    import uuid
    context["tasker_ai_workflow_enabled"] = getattr(settings, "TASKER_AI_WORKFLOW_ENABLED", False)
    context["tasker_strategy_enabled"] = getattr(settings, "TASKER_STRATEGY_ENABLED", False)
    context["ai_client_key"] = uuid.uuid4()
    context["tasker_direction_enabled"] = getattr(settings, "TASKER_DIRECTION_ENABLED", False)
    context["tasker_coordination_enabled"] = getattr(settings, "TASKER_COORDINATION_ENABLED", False)
    context["tasker_reports_enabled"] = getattr(settings, "TASKER_REPORTS_ENABLED", False)
    context['tasker_improvement_enabled']=getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False)
    context['tasker_recurrence_enabled']=getattr(settings,'TASKER_RECURRENCE_ENABLED',False)
    context["tasker_collections_adapter_enabled"] = getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False)
    context["tasker_replenishment_adapter_enabled"] = getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False)
    from .services.business_links import enabled as business_enabled
    context["tasker_business_adapters_enabled"] = business_enabled()
    from django_hosts.resolvers import reverse as host_reverse
    context['operations_home_url'] = host_reverse('ims:operations:home',host='ims')
    if is_tasker_request:
        from .services.pilot_access import released_features
        released=released_features(request.user)
        context['tasker_core_available']='core' in released
        context['tasker_now_available']='operator' in released
        context['tasker_ai_available']='ai' in released
        context['tasker_pilot_available']=getattr(settings,'TASKER_PILOT_ENABLED',False) and request.user.has_perm('tasker.view_pilot')
        for key,feature in (('tasker_ai_workflow_enabled','ai'),('tasker_strategy_enabled','strategy'),
            ('tasker_direction_enabled','strategy'),('tasker_coordination_enabled','coordination'),('tasker_reports_enabled','reports'),
            ('tasker_improvement_enabled','improvement'),('tasker_recurrence_enabled','recurrence'),
            ('tasker_business_adapters_enabled','business'),('tasker_collections_adapter_enabled','business'),('tasker_replenishment_adapter_enabled','business')):
            context[key]=context[key] and feature in released
        from .navigation import shell_navigation
        context.update(shell_navigation(request,allowed_features=released))
        if context['tasker_improvement_enabled'] and request.user.has_perm('tasker.view_task') and match.url_name in ('index','operator_home'):
            from .services.improvement import carry_candidates
            from .improvement_models import DayClosure
            from .services.work_dates import work_date
            candidates=carry_candidates(request.user)
            context['improvement_attention']={'carry_count':candidates.count(),'tasks':list(candidates[:3]),
                'closed':DayClosure.objects.filter(owner=request.user,plan__plan_date=work_date(request.user),closed=True).exists()}
        if (context["tasker_business_adapters_enabled"] and context.get("tasker_business_available")
                and match.url_name in ("index", "operator_home", "task_list")):
            from django.core.exceptions import PermissionDenied
            from .services.business_links import business_actions_for
            try:
                context["business_panel"] = business_actions_for(request.user, limit=5)
            except PermissionDenied:
                # Access can be revoked between navigation and source reads.
                context["business_panel"] = {"items": [], "total": 0, "omitted": 0}
        if context["tasker_reports_enabled"] and request.user.is_authenticated and request.user.has_perm("tasker.view_task"):
            from .services.reporting import report_teams_for
            context["tasker_reports_available"] = report_teams_for(request.user).exists()
        if context['tasker_core_available'] and request.user.has_perm("tasker.view_task"):
            from .capture_forms import CaptureCreateForm
            context["capture_create_form"] = CaptureCreateForm(prefix="quick_capture")
            context["capture_create_form"].fields["content"].widget.attrs["rows"] = 4
    if is_tasker_request and context.get('tasker_now_available') and request.user.has_perm("tasker.add_task"):
        from .forms import QuickAddTaskForm

        context["quick_add_form"] = QuickAddTaskForm()
    return context
