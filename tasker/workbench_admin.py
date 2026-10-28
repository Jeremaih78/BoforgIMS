"""Read-only owner-local inspection; saved views do not grant task authority."""
from django.contrib import admin

from .models import SavedWorkView, WorkbenchPreference


class OwnerWorkAdmin(admin.ModelAdmin):
    actions = ()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_superuser else queryset.filter(owner=request.user)

    def has_view_permission(self, request, obj=None):
        return super().has_view_permission(request, obj) and (obj is None or request.user.is_superuser or obj.owner_id == request.user.pk)

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SavedWorkView)
class SavedWorkViewAdmin(OwnerWorkAdmin):
    list_display = ("name", "owner", "updated_at")


@admin.register(WorkbenchPreference)
class WorkbenchPreferenceAdmin(OwnerWorkAdmin):
    list_display = ("owner", "preset", "layout", "updated_at")
