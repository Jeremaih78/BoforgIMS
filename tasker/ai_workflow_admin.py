"""Superuser-only monetary setup and immutable, content-free cost inspection."""
from django.contrib import admin
from tasker.models import (AIBudgetPolicy, AICompanyBudget, AIBudgetEnrollment, AIPriceCard,
    AISpendReservation, AISpendAttempt, AIWorkRequest, AIProposal, AIProposalEvent)


class OwnerSetupAdmin(admin.ModelAdmin):
    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIBudgetPolicy)
class AIBudgetPolicyAdmin(OwnerSetupAdmin):
    list_display = ("key", "is_enabled", "currency", "timezone_name", "employee_weekly_limit")


@admin.register(AICompanyBudget)
class AICompanyBudgetAdmin(OwnerSetupAdmin):
    list_display = ("company", "is_enabled", "weekly_limit", "charge_requester")


@admin.register(AIBudgetEnrollment)
class AIBudgetEnrollmentAdmin(OwnerSetupAdmin):
    list_display = ("user", "company", "is_active")
    list_filter = ("is_active", "company")


@admin.register(AIPriceCard)
class AIPriceCardAdmin(OwnerSetupAdmin):
    list_display = ("model", "version", "is_active", "is_verified", "valid_until")

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields) if obj else ()


class MoneyAuditAdmin(OwnerSetupAdmin):
    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AISpendReservation)
class AISpendReservationAdmin(MoneyAuditAdmin):
    list_display = ("usage_log", "user", "company", "period_start", "requires_review", "accounting_code")
    list_filter = ("company", "requires_review", "period_start")


@admin.register(AISpendAttempt)
class AISpendAttemptAdmin(MoneyAuditAdmin):
    list_display = ("reservation", "number", "status", "held_amount", "charged_amount")
    list_filter = ("status",)


# Request/proposal bodies remain in owner-only application screens; broad admin
# change-list access must not become a channel for private employee content.
