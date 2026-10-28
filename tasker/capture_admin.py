"""Private captures are owner-scoped and read-only for staff inspection."""
from django.contrib import admin

from .capture_models import CaptureEvent, CaptureItem


class CaptureReadOnlyAdmin(admin.ModelAdmin):
    actions = ()
    list_per_page = 30

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        lookup = "owner_id" if self.model is CaptureItem else "capture__owner_id"
        return queryset.filter(**{lookup: request.user.pk})

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_view_permission(self, request, obj=None):
        if not super().has_view_permission(request, obj):
            return False
        if obj is None or request.user.is_superuser:
            return True
        owner_id = obj.owner_id if self.model is CaptureItem else CaptureItem.all_objects.filter(
            pk=obj.capture_id).values_list("owner_id", flat=True).first()
        return owner_id == request.user.pk

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(CaptureItem)
class CaptureItemAdmin(CaptureReadOnlyAdmin):
    list_display = ("id", "owner", "kind", "visibility", "triage_state", "version", "is_deleted")
    list_filter = ("kind", "visibility", "triage_state", "is_deleted")
    search_fields = ("title", "content", "original_text")

    def get_readonly_fields(self, request, obj=None):
        fields = super().get_readonly_fields(request, obj)
        return tuple(field for field in fields if field not in ("promoted_task", "promoted_initiative")) + ("work_task_id", "work_initiative_id")

    @admin.display(description="Promoted task ID")
    def work_task_id(self, obj):
        return obj.promoted_task_id or "—"

    @admin.display(description="Promoted initiative ID")
    def work_initiative_id(self, obj):
        return obj.promoted_initiative_id or "—"


@admin.register(CaptureEvent)
class CaptureEventAdmin(CaptureReadOnlyAdmin):
    list_display = ("capture_id", "actor", "action", "version", "created_at")
