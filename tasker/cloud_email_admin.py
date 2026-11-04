from django.contrib import admin
from .cloud_email_models import (
    StaffEmailPolicy,
    StaffEmailBinding,
    StaffEmailReceipt,
    StaffEmailAuthorization,
)


@admin.register(StaffEmailPolicy)
class StaffEmailPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "company",
        "enabled",
        "tenant_id",
        "client_id",
        "activation_after",
        "reminders_enabled",
    )
    readonly_fields = ("updated_at", "reminder_cursor")

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return self.has_module_permission(request)

    def has_change_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False


class EvidenceAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


for model in (StaffEmailBinding, StaffEmailReceipt, StaffEmailAuthorization):
    admin.site.register(model, EvidenceAdmin)
