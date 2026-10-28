"""Read-only planning inspection; private exclusions stay owner-scoped."""
from django.contrib import admin

from .planning_models import Availability, PlanningEvent, ScheduleBlock, TaskEffortEstimate, WorkPolicy


class PlanningOwnerAdmin(admin.ModelAdmin):
    actions = ()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(owner=request.user)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and (obj is None or request.user.is_superuser or obj.owner_id == request.user.pk)

    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
    def get_readonly_fields(self, request, obj=None): return tuple(field.name for field in self.model._meta.fields)


@admin.register(WorkPolicy)
class WorkPolicyAdmin(PlanningOwnerAdmin):
    list_display = ("owner", "timezone_name", "configured", "buffer_minutes")


@admin.register(Availability)
class AvailabilityAdmin(PlanningOwnerAdmin):
    list_display = ("owner", "kind", "starts_at", "ends_at")


@admin.register(ScheduleBlock)
class ScheduleBlockAdmin(PlanningOwnerAdmin):
    list_display = ("owner", "state", "starts_at", "ends_at")


@admin.register(PlanningEvent)
class PlanningEventAdmin(PlanningOwnerAdmin):
    list_display = ("owner", "actor", "action", "created_at")


@admin.register(TaskEffortEstimate)
class TaskEffortEstimateAdmin(PlanningOwnerAdmin):
    list_display = ("id", "remaining_minutes", "updated_at")

    def get_queryset(self, request):
        queryset = admin.ModelAdmin.get_queryset(self, request)
        return queryset if request.user.is_superuser else queryset.filter(task__owner=request.user)

    def has_view_permission(self, request, obj=None):
        return admin.ModelAdmin.has_view_permission(self, request, obj) and (obj is None or request.user.is_superuser or obj.task.owner_id == request.user.pk)
