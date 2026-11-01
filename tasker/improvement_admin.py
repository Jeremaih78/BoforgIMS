"""Scoped, read-only evidence. Commands remain in the employee workspace."""
from django.contrib import admin
from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from tasker.improvement_models import (SOPTemplate,SOPVersion,SOPExecution,RecurringWorkflow,RoutineException,
    RoutineOccurrence,CarryDecision,WeeklyReview,Experiment,ExperimentResult,DayClosure,DayRevision)
from tasker.services.improvement import scoped_records,templates_for,experiments_for
from tasker.services.routines import routines_for


class ImprovementAdmin(admin.ModelAdmin):
    actions=None
    def has_add_permission(self,request):return False
    def has_change_permission(self,request,obj=None):return False
    def has_delete_permission(self,request,obj=None):return False
    def get_exclude(self,request,obj=None):
        return ('source_capture',) if self.model is Experiment else ()
    def has_view_permission(self,request,obj=None):
        return bool(getattr(settings,'TASKER_IMPROVEMENT_ENABLED',False) and super().has_view_permission(request,obj))
    def get_queryset(self,request):
        qs=super().get_queryset(request)
        if not self.has_view_permission(request):return qs.none()
        model=self.model
        try:
            from tasker.services.coordination import scoped_tasks_for
            if model in (SOPTemplate,WeeklyReview):return scoped_records(model,request.user)
            if model is Experiment:return experiments_for(request.user).filter(task__in=scoped_tasks_for(request.user))
            if model is RecurringWorkflow:return routines_for(request.user)
            if model is SOPVersion:return qs.filter(template__in=templates_for(request.user))
            if model is SOPExecution:return qs.filter(version__template__in=templates_for(request.user),task__in=scoped_tasks_for(request.user))
            if model is RoutineException:return qs.filter(routine__in=routines_for(request.user))
            if model is RoutineOccurrence:return qs.filter(routine__in=routines_for(request.user)).filter(Q(task__isnull=True)|Q(task__in=scoped_tasks_for(request.user)))
            if model is ExperimentResult:return qs.filter(experiment__in=experiments_for(request.user))
            if model is CarryDecision:
                return qs.filter(task__in=scoped_tasks_for(request.user))
            if model is DayClosure:return qs.filter(owner=request.user)
            if model is DayRevision:return qs.filter(closure__owner=request.user)
        except PermissionDenied:return qs.none()
        return qs.none()


for model in (SOPTemplate,SOPVersion,SOPExecution,RecurringWorkflow,RoutineException,RoutineOccurrence,
              CarryDecision,WeeklyReview,Experiment,ExperimentResult,DayClosure,DayRevision):
    admin.site.register(model,ImprovementAdmin)
