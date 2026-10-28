"""Dependency service owns writes; admin never exposes inaccessible FK titles."""
from django.contrib import admin

from tasker.dependency_models import TaskDependency
from tasker.policies import visible_tasks_for


@admin.register(TaskDependency)
class TaskDependencyAdmin(admin.ModelAdmin):
    actions = ()
    list_display = ("id", "task_id", "prerequisite_id", "is_deleted", "updated_at")
    readonly_fields = ("id", "task_id", "prerequisite_id", "is_deleted", "created_at", "updated_at")
    fields = readonly_fields

    def get_queryset(self, request):
        visible = visible_tasks_for(request.user).values("pk") if request.user.has_perm("tasker.view_task") else []
        return super().get_queryset(request).filter(task_id__in=visible, prerequisite_id__in=visible)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and (obj is None or self.get_queryset(request).filter(pk=obj.pk).exists())
