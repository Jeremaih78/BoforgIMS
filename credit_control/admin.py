from django.contrib import admin

from .models import (
    AuditEvent,
    CollectionTask,
    CreditorAccount,
    CreditorFollowUp,
    DebtorAccount,
    DebtorFollowUp,
    FollowUpTemplate,
    Notification,
    PaymentDispute,
    PromiseToPay,
    ReminderRule,
    RiskRule,
)


@admin.register(DebtorAccount)
class DebtorAccountAdmin(admin.ModelAdmin):
    list_display = ("customer", "total_outstanding", "overdue_balance", "risk_level", "collection_status", "next_follow_up_date")
    list_filter = ("risk_level", "collection_status", "is_active")
    search_fields = ("customer__name",)


@admin.register(CreditorAccount)
class CreditorAccountAdmin(admin.ModelAdmin):
    list_display = ("supplier", "total_outstanding", "overdue_balance", "risk_level", "payment_status", "is_critical_supplier")
    list_filter = ("risk_level", "payment_status", "is_critical_supplier", "is_active")
    search_fields = ("supplier__name",)


@admin.register(DebtorFollowUp)
class DebtorFollowUpAdmin(admin.ModelAdmin):
    list_display = ("customer", "invoice", "follow_up_date", "follow_up_type", "outcome", "status")
    list_filter = ("follow_up_type", "outcome", "status")
    search_fields = ("customer__name", "invoice__number", "subject")


@admin.register(CreditorFollowUp)
class CreditorFollowUpAdmin(admin.ModelAdmin):
    list_display = ("supplier", "bill", "follow_up_date", "follow_up_type", "outcome", "status")
    list_filter = ("follow_up_type", "outcome", "status")
    search_fields = ("supplier__name", "bill__doc_no", "subject")


@admin.register(PromiseToPay)
class PromiseToPayAdmin(admin.ModelAdmin):
    list_display = ("customer", "invoice", "promised_amount", "promised_date", "status", "actual_paid_amount")
    list_filter = ("status",)
    search_fields = ("customer__name", "invoice__number")


@admin.register(PaymentDispute)
class PaymentDisputeAdmin(admin.ModelAdmin):
    list_display = ("dispute_type", "customer", "supplier", "invoice", "bill", "status", "opened_date")
    list_filter = ("dispute_type", "status")
    search_fields = ("customer__name", "supplier__name", "description")


@admin.register(FollowUpTemplate)
class FollowUpTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "audience", "channel", "tone", "is_active")
    list_filter = ("audience", "channel", "tone", "is_active")


@admin.register(CollectionTask)
class CollectionTaskAdmin(admin.ModelAdmin):
    list_display = ("task_type", "customer", "supplier", "due_date", "priority", "status", "assigned_to")
    list_filter = ("task_type", "priority", "status")


@admin.register(ReminderRule)
class ReminderRuleAdmin(admin.ModelAdmin):
    list_display = ("name", "audience", "offset_days", "task_type", "priority", "is_active")
    list_filter = ("audience", "is_active", "priority")


@admin.register(RiskRule)
class RiskRuleAdmin(admin.ModelAdmin):
    list_display = ("key", "label", "weight", "threshold_low", "threshold_medium", "threshold_high", "is_active")


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("user", "title", "level", "is_read", "created_at")
    list_filter = ("level", "is_read")


@admin.register(AuditEvent)
class AuditEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "model_name", "object_id", "action", "user", "created_at")
    list_filter = ("event_type", "action")
