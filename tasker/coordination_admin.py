"""Read-only operational evidence; mutations use scoped workflow services."""
from django.contrib import admin

from tasker.models import (CoordinationEvent, CoordinationRequest, EndDaySubmission,
    JobWorkerHeartbeat, ManagerReportSnapshot, Notification, NotificationDelivery,
    NotificationPreference, OutboxJob, ReportPolicy)


class OperationalEvidenceAdmin(admin.ModelAdmin):
    actions = ()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        return queryset if request.user.is_active and request.user.is_superuser else queryset.none()

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_view_permission(self, request, obj=None):
        return bool(request.user.is_active and request.user.is_superuser)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OutboxJob)
class OutboxJobAdmin(OperationalEvidenceAdmin):
    list_display = ("kind", "state", "attempts", "run_after", "last_error_code")
    list_filter = ("kind", "state")


@admin.register(JobWorkerHeartbeat)
class JobWorkerHeartbeatAdmin(OperationalEvidenceAdmin):
    list_display = ("name", "role", "seen_at", "completed")


@admin.register(CoordinationRequest)
class CoordinationRequestAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "kind", "status", "raised_at", "expected_response_at")
    list_filter = ("kind", "status")


@admin.register(Notification)
class NotificationAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "kind", "owner", "created_at", "published_at", "cancelled_at")


@admin.register(NotificationDelivery)
class NotificationDeliveryAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "channel", "state", "attempt_count", "error_code", "queued_at")
    list_filter = ("channel", "state")


@admin.register(ManagerReportSnapshot)
class ManagerReportSnapshotAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "team", "report_date", "version", "created_at")


@admin.register(EndDaySubmission)
class EndDaySubmissionAdmin(OperationalEvidenceAdmin):
    list_display = ("id", "owner", "team", "report_date", "version", "created_at")


@admin.register(ReportPolicy)
class ReportPolicyAdmin(OperationalEvidenceAdmin):
    list_display = ("company", "configured", "email_mode", "revision")


@admin.register(CoordinationEvent)
class CoordinationEventAdmin(OperationalEvidenceAdmin):
    list_display = ("request", "action", "created_at")


@admin.register(NotificationPreference)
class NotificationPreferenceAdmin(OperationalEvidenceAdmin):
    list_display = ("owner", "quiet_enabled", "digest_mode", "revision")
