from django.contrib import admin, messages
from django.db import transaction
from django.utils import timezone
from django.core.exceptions import PermissionDenied

from tasker.services.focus import close_task_timers
from tasker.services.lifecycle import prepare_task_state, requires_timer_cleanup
from tasker.services.tasks import soft_delete_task, sync_checklist_progress

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
            if isinstance(instance, TaskChecklistItem):
                previous = TaskChecklistItem.all_objects.filter(pk=instance.pk).first() if instance.pk else None
                if instance.is_completed:
                    instance.completed_at = previous.completed_at if previous and previous.is_completed else timezone.now()
                    instance.completed_by = previous.completed_by if previous and previous.is_completed else request.user
                else:
                    instance.completed_at = None
                    instance.completed_by = None
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


class ScopedWorkAdminMixin:
    """Staff inspect authorized work; edits use the normal validated services.

    A Django permission/group must never bypass the reporting-team boundary.
    Superusers retain the existing explicit operational recovery workflow.
    """
    task_lookup = "task_id"

    def get_queryset(self, request):
        from tasker.policies import visible_tasks_for
        queryset = super().get_queryset(request)
        if request.user.is_superuser and request.user.is_active:
            return queryset
        lookup = "pk__in" if self.model is Task else f"{self.task_lookup}__in"
        return queryset.filter(**{lookup: visible_tasks_for(request.user).values("pk")})

    def has_add_permission(self, request):
        return request.user.is_active and request.user.is_superuser and super().has_add_permission(request)

    def has_change_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser and super().has_change_permission(request, obj)

    def has_delete_permission(self, request, obj=None):
        return request.user.is_active and request.user.is_superuser and super().has_delete_permission(request, obj)

    def get_actions(self, request):
        return super().get_actions(request) if request.user.is_superuser else {}

    def get_readonly_fields(self, request, obj=None):
        if not request.user.is_superuser:
            return tuple(field.name for field in self.model._meta.fields)
        return super().get_readonly_fields(request, obj)


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
class TaskAdmin(ScopedWorkAdminMixin, AuditedSoftDeleteAdmin):
    readonly_fields = AuditedSoftDeleteAdmin.readonly_fields + ("notes",)
    list_display = ("title", "owner", "priority", "status", "due_date", "progress", "is_deleted")
    list_filter = ("priority", "status", "category", "carry_forward", "archived", "is_deleted")
    search_fields = ("title", "description", "notes", "owner__username")
    autocomplete_fields = ("owner", "assigned_by", "category", "parent_task")
    prepopulated_fields = {"slug": ("title",)}
    inlines = (ChecklistInline, CommentInline)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        from tasker.organization import validate_task_scope
        if not request.user.is_active or not request.user.is_superuser:
            raise PermissionDenied
        previous = Task.all_objects.select_for_update().get(pk=obj.pk) if change else None
        validate_task_scope(request.user, obj, previous=previous, lock=True)
        if previous:
            # Working notes have their own versioned editor; admin edits cannot
            # overwrite a concurrent focus save with an old form snapshot.
            obj.notes = previous.notes
        prepare_task_state(obj, previous_status=previous.status if previous else None)
        super().save_model(request, obj, form, change)
        if requires_timer_cleanup(obj) or (previous and previous.owner_id != obj.owner_id):
            close_task_timers(task=obj, actor=request.user)
        if previous and (obj.archived or obj.status in (Task.Status.COMPLETED,Task.Status.CANCELLED) or any(getattr(previous,field)!=getattr(obj,field) for field in ("owner_id","company_id","team_id"))):
            from tasker.services.scheduling import invalidate_task_schedule
            invalidate_task_schedule(task=obj,actor=request.user)
        if previous and any(getattr(previous, field) != getattr(obj, field) for field in ("owner_id", "company_id", "team_id")):
            from .models import OrganizationEvent
            OrganizationEvent.objects.create(
                company=obj.company, team=obj.team, actor=request.user,
                action="task.admin_scope_changed", object_type="task", object_id=str(obj.pk),
                details={field: {"from": getattr(previous, field), "to": getattr(obj, field)} for field in ("owner_id", "company_id", "team_id")},
            )

    def delete_model(self, request, obj):
        if not obj.is_deleted:
            soft_delete_task(task=obj, user=request.user)

    def delete_queryset(self, request, queryset):
        for task in queryset.filter(is_deleted=False).iterator(chunk_size=200):
            soft_delete_task(task=task, user=request.user)

    @admin.action(description="Soft delete selected records")
    def soft_delete_selected(self, request, queryset):
        count = queryset.filter(is_deleted=False).count()
        self.delete_queryset(request, queryset)
        self.message_user(request, f"{count} task(s) soft deleted; active timers closed.", messages.SUCCESS)

    @transaction.atomic
    def save_formset(self, request, form, formset, change):
        if formset.model is TaskChecklistItem:
            Task.all_objects.select_for_update().get(pk=form.instance.pk)
        super().save_formset(request, form, formset, change)
        if formset.model is TaskChecklistItem and not form.instance.is_deleted:
            sync_checklist_progress(task=form.instance, user=request.user)


class PersonalContentAdmin(AuditedSoftDeleteAdmin):
    """Staff capability grants no cross-employee private content access.

    Superusers retain explicit operational access. Content is read-only here;
    normal owner workflows preserve lifecycle and version checks.
    """

    owner_lookup = "owner_id"
    actions = ()

    def get_queryset(self, request):
        queryset = super().get_queryset(request)
        if request.user.is_superuser:
            return queryset
        return queryset.filter(**{self.owner_lookup: request.user.pk})

    def get_readonly_fields(self, request, obj=None):
        return tuple(field.name for field in self.model._meta.fields) + tuple(
            field.name for field in self.model._meta.many_to_many
        )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(DailyPlan)
class DailyPlanAdmin(PersonalContentAdmin):
    list_display = ("plan_date", "owner", "focus_area", "is_deleted")
    list_filter = ("plan_date", "is_deleted")
    search_fields = ("owner__username", "focus_area", "quick_notes")
    autocomplete_fields = ("owner", "top_priorities", "planned_tasks")
    filter_horizontal = ("top_priorities", "planned_tasks")


@admin.register(DailyReview)
class DailyReviewAdmin(PersonalContentAdmin):
    owner_lookup = "plan__owner_id"
    list_display = ("plan", "energy_level", "tomorrow_focus", "is_deleted")
    list_filter = ("energy_level", "is_deleted")
    autocomplete_fields = ("plan", "carry_over_tasks")
    filter_horizontal = ("carry_over_tasks",)


@admin.register(TaskComment)
class TaskCommentAdmin(ScopedWorkAdminMixin, AuditedSoftDeleteAdmin):
    list_display = ("task", "created_by", "created_at", "is_deleted")
    list_filter = ("is_deleted", "created_at")
    search_fields = ("task__title", "body", "created_by__username")
    autocomplete_fields = ("task",)


@admin.register(TaskChecklistItem)
class TaskChecklistItemAdmin(ScopedWorkAdminMixin, AuditedSoftDeleteAdmin):
    list_display = ("title", "task", "is_completed", "position", "is_deleted")
    list_filter = ("is_completed", "is_deleted")
    search_fields = ("title", "task__title")
    autocomplete_fields = ("task", "completed_by")

    def get_readonly_fields(self, request, obj=None):
        return super().get_readonly_fields(request, obj) + (("task", "is_deleted") if obj else ("is_deleted",))

    @staticmethod
    def _lock_parents(queryset):
        ids = list(queryset.values_list("task_id", flat=True).distinct())
        return list(Task.all_objects.select_for_update().filter(pk__in=ids).order_by("pk"))

    @staticmethod
    def _sync(parents, user):
        for task in parents:
            if not task.is_deleted:
                sync_checklist_progress(task=task, user=user)

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        parent = Task.all_objects.select_for_update().get(pk=obj.task_id)
        previous = TaskChecklistItem.all_objects.get(pk=obj.pk) if change else None
        if obj.is_completed:
            obj.completed_at = previous.completed_at if previous and previous.is_completed else timezone.now()
            obj.completed_by = previous.completed_by if previous and previous.is_completed else request.user
        else:
            obj.completed_at = None
            obj.completed_by = None
        super().save_model(request, obj, form, change)
        self._sync([parent], request.user)

    @transaction.atomic
    def delete_model(self, request, obj):
        parents = self._lock_parents(TaskChecklistItem.all_objects.filter(pk=obj.pk))
        super().delete_model(request, obj)
        self._sync(parents, request.user)

    @transaction.atomic
    def delete_queryset(self, request, queryset):
        parents = self._lock_parents(queryset)
        super().delete_queryset(request, queryset)
        self._sync(parents, request.user)

    @admin.action(description="Soft delete selected records")
    def soft_delete_selected(self, request, queryset):
        count = queryset.filter(is_deleted=False).count()
        self.delete_queryset(request, queryset)
        self.message_user(request, f"{count} checklist item(s) soft deleted.", messages.SUCCESS)

    @admin.action(description="Restore selected records")
    @transaction.atomic
    def restore_selected(self, request, queryset):
        parents = self._lock_parents(queryset)
        count = queryset.filter(is_deleted=True).restore(user=request.user)
        self._sync(parents, request.user)
        self.message_user(request, f"{count} checklist item(s) restored.", messages.SUCCESS)


@admin.register(TaskTimeEntry)
class TaskTimeEntryAdmin(ScopedWorkAdminMixin, AuditedSoftDeleteAdmin):
    list_display = ("task", "user", "entry_type", "started_at", "ended_at", "duration", "is_deleted")
    list_filter = ("entry_type", "is_deleted", "started_at")
    search_fields = ("task__title", "user__username", "notes")
    autocomplete_fields = ("task", "user")


@admin.register(TaskAttachment)
class TaskAttachmentAdmin(ScopedWorkAdminMixin, AuditedSoftDeleteAdmin):
    list_display = ("original_name", "task", "content_type", "file_size", "created_at", "is_deleted")
    list_filter = ("content_type", "is_deleted")
    search_fields = ("original_name", "description", "task__title")
    autocomplete_fields = ("task",)


@admin.register(TaskActivity)
class TaskActivityAdmin(ScopedWorkAdminMixin, admin.ModelAdmin):
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
class AIConversationAdmin(PersonalContentAdmin):
    list_display = ("session_id", "owner", "title", "agent_type", "status", "last_message_at")
    list_filter = ("status", "agent_type", "is_deleted")
    search_fields = ("session_id", "owner__username", "title")
    autocomplete_fields = ("owner",)


@admin.register(AIMessage)
class AIMessageAdmin(PersonalContentAdmin):
    owner_lookup = "conversation__owner_id"
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
class AIFeedbackAdmin(PersonalContentAdmin):
    owner_lookup = "user_id"
    list_display = ("message", "user", "rating", "category", "created_at", "is_deleted")
    list_filter = ("rating", "category", "is_deleted")
    search_fields = ("user__username", "comment", "message__provider_response_id")
    autocomplete_fields = ("user", "message")


from . import direction_admin  # noqa: E402,F401
from . import capture_admin  # noqa: E402,F401

from . import workbench_admin  # noqa: E402,F401
from . import planning_admin, dependency_admin, arrival_admin  # noqa: E402,F401
from . import operator_admin  # noqa: E402,F401
from . import coordination_admin  # noqa: E402,F401
