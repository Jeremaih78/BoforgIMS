from django.contrib import admin, messages

from .models import (
    AIConfiguration,
    AIConversation,
    AIFeedback,
    AIMessage,
    AIUsageLog,
    DailyPlan,
    DailyReview,
    Task,
    TaskActivity,
    TaskAttachment,
    TaskCategory,
    TaskChecklistItem,
    TaskComment,
    TaskTimeEntry,
    PromptTemplate,
)


class AuditedSoftDeleteAdmin(admin.ModelAdmin):
    readonly_fields = (
        "created_at",
        "updated_at",
        "created_by",
        "updated_by",
        "deleted_at",
        "deleted_by",
        "ai_processed_at",
    )
    actions = ("soft_delete_selected", "restore_selected")

    def get_queryset(self, request):
        return self.model.all_objects.get_queryset()

    def save_model(self, request, obj, form, change):
        if not change and obj.created_by_id is None:
            obj.created_by = request.user
        obj.updated_by = request.user
        super().save_model(request, obj, form, change)

    def save_formset(self, request, form, formset, change):
        """Apply the same actor audit to checklist/comment admin inlines."""
        instances = formset.save(commit=False)
        for deleted_object in formset.deleted_objects:
            deleted_object.delete(user=request.user)
        for instance in instances:
            if instance.created_by_id is None:
                instance.created_by = request.user
            instance.updated_by = request.user
            instance.save()
        formset.save_m2m()

    @admin.action(description="Soft delete selected records")
    def soft_delete_selected(self, request, queryset):
        count = queryset.filter(is_deleted=False).delete(user=request.user)
        self.message_user(request, f"{count} record(s) soft deleted.", messages.SUCCESS)

    @admin.action(description="Restore selected records")
    def restore_selected(self, request, queryset):
        count = queryset.filter(is_deleted=True).restore(user=request.user)
        self.message_user(request, f"{count} record(s) restored.", messages.SUCCESS)

    def delete_model(self, request, obj):
        obj.delete(user=request.user)

    def delete_queryset(self, request, queryset):
        queryset.delete(user=request.user)


@admin.register(TaskCategory)
class TaskCategoryAdmin(AuditedSoftDeleteAdmin):
    list_display = ("name", "colour", "is_active", "sort_order", "is_deleted")
    list_filter = ("is_active", "is_deleted")
    search_fields = ("name", "description")
    prepopulated_fields = {"slug": ("name",)}


class ChecklistInline(admin.TabularInline):
    model = TaskChecklistItem
    extra = 0
    fields = ("title", "position", "is_completed", "completed_at", "completed_by")


class CommentInline(admin.StackedInline):
    model = TaskComment
    extra = 0
    fields = ("body",)


@admin.register(Task)
class TaskAdmin(AuditedSoftDeleteAdmin):
    list_display = ("title", "owner", "priority", "status", "due_date", "progress", "is_deleted")
    list_filter = ("priority", "status", "category", "carry_forward", "archived", "is_deleted")
    search_fields = ("title", "description", "notes", "owner__username")
    autocomplete_fields = ("owner", "assigned_by", "category", "parent_task")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (ChecklistInline, CommentInline)


@admin.register(DailyPlan)
class DailyPlanAdmin(AuditedSoftDeleteAdmin):
    list_display = ("plan_date", "owner", "focus_area", "is_deleted")
    list_filter = ("plan_date", "is_deleted")
    search_fields = ("owner__username", "focus_area", "quick_notes")
    autocomplete_fields = ("owner", "top_priorities", "planned_tasks")
    filter_horizontal = ("top_priorities", "planned_tasks")


@admin.register(DailyReview)
class DailyReviewAdmin(AuditedSoftDeleteAdmin):
    list_display = ("plan", "energy_level", "tomorrow_focus", "is_deleted")
    list_filter = ("energy_level", "is_deleted")
    autocomplete_fields = ("plan", "carry_over_tasks")
    filter_horizontal = ("carry_over_tasks",)


@admin.register(TaskComment)
class TaskCommentAdmin(AuditedSoftDeleteAdmin):
    list_display = ("task", "created_by", "created_at", "is_deleted")
    list_filter = ("is_deleted", "created_at")
    search_fields = ("task__title", "body", "created_by__username")
    autocomplete_fields = ("task",)


@admin.register(TaskChecklistItem)
class TaskChecklistItemAdmin(AuditedSoftDeleteAdmin):
    list_display = ("title", "task", "is_completed", "position", "is_deleted")
    list_filter = ("is_completed", "is_deleted")
    search_fields = ("title", "task__title")
    autocomplete_fields = ("task", "completed_by")


@admin.register(TaskTimeEntry)
class TaskTimeEntryAdmin(AuditedSoftDeleteAdmin):
    list_display = ("task", "user", "entry_type", "started_at", "ended_at", "duration", "is_deleted")
    list_filter = ("entry_type", "is_deleted", "started_at")
    search_fields = ("task__title", "user__username", "notes")
    autocomplete_fields = ("task", "user")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(AuditedSoftDeleteAdmin):
    list_display = ("original_name", "task", "content_type", "file_size", "created_at", "is_deleted")
    list_filter = ("content_type", "is_deleted")
    search_fields = ("original_name", "description", "task__title")
    autocomplete_fields = ("task",)


@admin.register(TaskActivity)
class TaskActivityAdmin(admin.ModelAdmin):
    list_display = ("task", "event_type", "description", "created_by", "created_at")
    list_filter = ("event_type", "created_at")
    search_fields = ("task__title", "description", "created_by__username")
    readonly_fields = tuple(field.name for field in TaskActivity._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIConfiguration)
class AIConfigurationAdmin(AuditedSoftDeleteAdmin):
    list_display = ("name", "provider", "model", "is_enabled", "is_active", "updated_at")
    list_filter = ("provider", "is_enabled", "is_active", "is_deleted")
    search_fields = ("name", "model")
    fieldsets = (
        ("Provider", {"fields": ("name", "provider", "model", "reasoning_effort", "response_verbosity", "is_enabled", "is_active")}),
        ("Prompt policy", {"fields": ("system_prompt_override", "temperature", "max_output_tokens")}),
        ("Reliability", {"fields": ("timeout_seconds", "max_retries", "retry_base_seconds", "allow_streaming")}),
        ("Usage limits", {"fields": ("requests_per_minute", "daily_request_limit", "daily_token_limit")}),
        ("Privacy", {"fields": ("store_provider_responses",)}),
        ("Audit", {"classes": ("collapse",), "fields": AuditedSoftDeleteAdmin.readonly_fields + ("is_deleted",)}),
    )

    def has_module_permission(self, request):
        return request.user.has_perm("tasker.manage_ai_configuration") or request.user.is_superuser


@admin.register(PromptTemplate)
class PromptTemplateAdmin(AuditedSoftDeleteAdmin):
    list_display = ("key", "name", "version", "is_active", "updated_at", "is_deleted")
    list_filter = ("is_active", "is_deleted", "version")
    search_fields = ("key", "name", "purpose", "system_template", "user_template")
    readonly_fields = AuditedSoftDeleteAdmin.readonly_fields


@admin.register(AIConversation)
class AIConversationAdmin(AuditedSoftDeleteAdmin):
    list_display = ("session_id", "owner", "title", "agent_type", "status", "last_message_at")
    list_filter = ("status", "agent_type", "is_deleted")
    search_fields = ("session_id", "owner__username", "title")
    autocomplete_fields = ("owner",)


@admin.register(AIMessage)
class AIMessageAdmin(AuditedSoftDeleteAdmin):
    list_display = ("conversation", "sequence", "role", "status", "total_tokens", "created_at")
    list_filter = ("role", "status", "is_deleted")
    search_fields = ("conversation__session_id", "provider_response_id")
    autocomplete_fields = ("conversation", "prompt_template")
    readonly_fields = AuditedSoftDeleteAdmin.readonly_fields + (
        "conversation", "role", "status", "sequence", "content", "prompt_template",
        "provider_response_id", "input_tokens", "output_tokens", "total_tokens", "latency_ms",
        "error_code", "message_metadata",
    )

    def has_add_permission(self, request):
        return False


@admin.register(AIUsageLog)
class AIUsageLogAdmin(admin.ModelAdmin):
    list_display = ("request_uuid", "user", "operation", "model", "status", "total_tokens", "latency_ms", "started_at")
    list_filter = ("status", "provider", "model", "was_streamed", "started_at")
    search_fields = ("request_uuid", "provider_request_id", "provider_response_id", "user__username", "operation")
    readonly_fields = tuple(field.name for field in AIUsageLog._meta.fields)
    date_hierarchy = "started_at"

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AIFeedback)
class AIFeedbackAdmin(AuditedSoftDeleteAdmin):
    list_display = ("message", "user", "rating", "category", "created_at", "is_deleted")
    list_filter = ("rating", "category", "is_deleted")
    search_fields = ("user__username", "comment", "message__provider_response_id")
    autocomplete_fields = ("user", "message")
