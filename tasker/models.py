from __future__ import annotations

from core.storage import private_document_storage

import uuid
from datetime import timedelta

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone
from django.utils.text import slugify


colour_validator = RegexValidator(
    regex=r"^#[0-9A-Fa-f]{6}$",
    message="Enter a colour as a six-digit hex value, for example #4DA3FF.",
)


class ActiveQuerySet(models.QuerySet):
    """QuerySet that performs recoverable deletes by default."""

    def delete(self, *, user=None):
        now = timezone.now()
        updates = {"is_deleted": True, "deleted_at": now, "updated_at": now}
        if user is not None and getattr(user, "is_authenticated", False):
            updates["deleted_by"] = user
            updates["updated_by"] = user
        return self.update(**updates)

    def hard_delete(self):
        return super().delete()

    def restore(self, *, user=None):
        updates = {"is_deleted": False, "deleted_at": None, "deleted_by": None}
        if user is not None and getattr(user, "is_authenticated", False):
            updates["updated_by"] = user
        return self.update(**updates)


class ActiveManager(models.Manager.from_queryset(ActiveQuerySet)):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class AuditedSoftDeleteModel(models.Model):
    """Common lifecycle, audit, and future-AI metadata for Tasker records."""

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_created",
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_updated",
    )
    is_deleted = models.BooleanField(default=False, db_index=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="%(app_label)s_%(class)s_deleted",
    )
    ai_metadata = models.JSONField(default=dict, blank=True)
    ai_processed_at = models.DateTimeField(null=True, blank=True)

    objects = ActiveManager()
    all_objects = models.Manager.from_queryset(ActiveQuerySet)()

    class Meta:
        abstract = True

    def delete(self, using=None, keep_parents=False, *, user=None):
        self.is_deleted = True
        self.deleted_at = timezone.now()
        if user is not None and getattr(user, "is_authenticated", False):
            self.deleted_by = user
            self.updated_by = user
        self.save(
            using=using,
            update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_by", "updated_at"],
        )

    def hard_delete(self, using=None, keep_parents=False):
        return super().delete(using=using, keep_parents=keep_parents)

    def restore(self, *, user=None):
        self.is_deleted = False
        self.deleted_at = None
        self.deleted_by = None
        if user is not None and getattr(user, "is_authenticated", False):
            self.updated_by = user
        self.save(
            update_fields=["is_deleted", "deleted_at", "deleted_by", "updated_by", "updated_at"],
        )


class TaskCategory(AuditedSoftDeleteModel):
    name = models.CharField(max_length=100)
    slug = models.SlugField(max_length=120)
    description = models.TextField(blank=True)
    colour = models.CharField(max_length=7, default="#4DA3FF", validators=[colour_validator])
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        ordering = ["sort_order", "name"]
        verbose_name_plural = "Task categories"
        constraints = [
            models.UniqueConstraint(
                fields=["name"], condition=Q(is_deleted=False), name="tasker_category_active_name_uniq"
            ),
            models.UniqueConstraint(
                fields=["slug"], condition=Q(is_deleted=False), name="tasker_category_active_slug_uniq"
            ),
        ]
        indexes = [models.Index(fields=["is_active", "sort_order"], name="tasker_cat_active_sort_idx")]

    def __str__(self):
        return self.name

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _available_slug(self, self.name)
        super().save(*args, **kwargs)


class Task(AuditedSoftDeleteModel):
    class Priority(models.TextChoices):
        CRITICAL = "CRITICAL", "Critical"
        HIGH = "HIGH", "High"
        MEDIUM = "MEDIUM", "Medium"
        LOW = "LOW", "Low"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        BLOCKED = "BLOCKED", "Blocked"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_tasks"
    )
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasker_tasks_assigned",
    )
    priority = models.CharField(max_length=10, choices=Priority.choices, default=Priority.MEDIUM)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.PENDING)
    estimated_duration = models.DurationField(null=True, blank=True)
    actual_duration = models.DurationField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    due_time = models.TimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    progress = models.PositiveSmallIntegerField(default=0)
    carry_forward = models.BooleanField(default=False)
    category = models.ForeignKey(
        TaskCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasks"
    )
    parent_task = models.ForeignKey(
        "self", on_delete=models.SET_NULL, null=True, blank=True, related_name="subtasks"
    )
    colour = models.CharField(max_length=7, blank=True, validators=[colour_validator])
    archived = models.BooleanField(default=False)
    slug = models.SlugField(max_length=280)
    notes = models.TextField(blank=True)
    blocked_reason = models.TextField(blank=True)
    ai_summary = models.TextField(blank=True)
    ai_recommendation = models.TextField(blank=True)
    ai_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    class Meta:
        ordering = ["due_date", "due_time", "-priority", "created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(progress__gte=0) & Q(progress__lte=100), name="tasker_task_progress_0_100"),
            models.CheckConstraint(condition=Q(ai_score__isnull=True) | (Q(ai_score__gte=0) & Q(ai_score__lte=100)), name="tasker_task_ai_score_0_100"),
            models.CheckConstraint(condition=Q(estimated_duration__isnull=True) | Q(estimated_duration__gte=timedelta(0)), name="tasker_task_est_duration_nonneg"),
            models.CheckConstraint(condition=Q(actual_duration__isnull=True) | Q(actual_duration__gte=timedelta(0)), name="tasker_task_actual_duration_nonneg"),
            models.CheckConstraint(condition=Q(due_time__isnull=True) | Q(due_date__isnull=False), name="tasker_task_time_requires_date"),
            models.UniqueConstraint(fields=["slug"], condition=Q(is_deleted=False), name="tasker_task_active_slug_uniq"),
        ]
        indexes = [
            models.Index(fields=["owner", "status", "due_date"], name="tasker_owner_status_due_idx"),
            models.Index(fields=["priority", "status"], name="tasker_priority_status_idx"),
            models.Index(fields=["carry_forward", "archived"], name="tasker_carry_archive_idx"),
            models.Index(fields=["parent_task", "is_deleted"], name="tasker_parent_deleted_idx"),
        ]
        permissions = [
            ("assign_task", "Can assign tasks to other users"),
            ("view_all_tasks", "Can view tasks owned by other users"),
            ("manage_tasker", "Can manage Boforg AI Tasker"),
        ]

    def __str__(self):
        return self.title

    def get_absolute_url(self):
        from django.urls import reverse

        return reverse("ims:tasker:task_detail", kwargs={"slug": self.slug})

    def clean(self):
        super().clean()
        if self.parent_task_id and self.parent_task_id == self.pk:
            raise ValidationError({"parent_task": "A task cannot be its own parent."})
        ancestor = self.parent_task
        visited = {self.pk} if self.pk else set()
        while ancestor is not None:
            if ancestor.pk in visited:
                raise ValidationError({"parent_task": "A task hierarchy cannot contain a cycle."})
            visited.add(ancestor.pk)
            ancestor = ancestor.parent_task
        if self.completed_at and self.started_at and self.completed_at < self.started_at:
            raise ValidationError({"completed_at": "Completion cannot be before the start."})
        if self.due_time and not self.due_date:
            raise ValidationError({"due_date": "Choose a due date when setting a due time."})
        if self.status == self.Status.BLOCKED and not (self.blocked_reason or "").strip():
            raise ValidationError({"blocked_reason": "Record why this task is blocked."})

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = _available_slug(self, self.title)
        super().save(*args, **kwargs)


class DailyPlan(AuditedSoftDeleteModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_daily_plans")
    plan_date = models.DateField(default=timezone.localdate)
    focus_area = models.CharField(max_length=255, blank=True)
    motivational_quote = models.TextField(blank=True)
    quick_notes = models.TextField(blank=True)
    top_priorities = models.ManyToManyField(Task, blank=True, related_name="priority_daily_plans")
    planned_tasks = models.ManyToManyField(Task, blank=True, related_name="daily_plans")

    class Meta:
        ordering = ["-plan_date"]
        constraints = [models.UniqueConstraint(fields=["owner", "plan_date"], condition=Q(is_deleted=False), name="tasker_plan_owner_date_uniq")]
        indexes = [models.Index(fields=["plan_date", "owner"], name="tasker_plan_date_owner_idx")]

    def __str__(self):
        return f"{self.owner} — {self.plan_date:%d %b %Y}"


class DailyReview(AuditedSoftDeleteModel):
    plan = models.OneToOneField(DailyPlan, on_delete=models.CASCADE, related_name="review")
    accomplishments = models.TextField(blank=True)
    energy_level = models.PositiveSmallIntegerField(null=True, blank=True)
    most_productive_time = models.CharField(max_length=100, blank=True)
    biggest_distraction = models.TextField(blank=True)
    lessons_learned = models.TextField(blank=True)
    tomorrow_focus = models.TextField(blank=True)
    carry_over_tasks = models.ManyToManyField(Task, blank=True, related_name="daily_reviews_carried_over")

    class Meta:
        ordering = ["-plan__plan_date"]
        constraints = [models.CheckConstraint(condition=Q(energy_level__isnull=True) | (Q(energy_level__gte=1) & Q(energy_level__lte=5)), name="tasker_review_energy_1_5")]

    def __str__(self):
        return f"Review — {self.plan}"


class TaskComment(AuditedSoftDeleteModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="comments")
    body = models.TextField()

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["task", "created_at"], name="tasker_comment_task_date_idx")]

    def __str__(self):
        return f"Comment on {self.task}"


class TaskChecklistItem(AuditedSoftDeleteModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="checklist_items")
    title = models.CharField(max_length=255)
    position = models.PositiveSmallIntegerField(default=0)
    is_completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    completed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="tasker_checklist_items_completed")

    class Meta:
        ordering = ["position", "created_at"]
        indexes = [models.Index(fields=["task", "is_completed", "position"], name="tasker_check_task_done_idx")]

    def __str__(self):
        return self.title


class TaskTimeEntry(AuditedSoftDeleteModel):
    class EntryType(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        FOCUS = "FOCUS", "Focus mode"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="time_entries")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_time_entries")
    started_at = models.DateTimeField()
    ended_at = models.DateTimeField(null=True, blank=True)
    duration = models.DurationField(null=True, blank=True)
    notes = models.TextField(blank=True)
    entry_type = models.CharField(max_length=10, choices=EntryType.choices, default=EntryType.MANUAL)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.CheckConstraint(condition=Q(ended_at__isnull=True) | Q(ended_at__gte=models.F("started_at")), name="tasker_time_end_after_start"),
            models.CheckConstraint(condition=Q(duration__isnull=True) | Q(duration__gte=timedelta(0)), name="tasker_time_duration_nonneg"),
            models.CheckConstraint(
                condition=(Q(ended_at__isnull=True, duration__isnull=True) | Q(ended_at__isnull=False, duration__isnull=False)),
                name="tasker_time_end_duration_pair",
            ),
            models.UniqueConstraint(
                fields=["user"],
                condition=Q(ended_at__isnull=True, is_deleted=False),
                name="tasker_one_running_timer_user",
            ),
        ]
        indexes = [
            models.Index(fields=["task", "started_at"], name="tasker_time_task_start_idx"),
            models.Index(fields=["user", "started_at"], name="tasker_time_user_start_idx"),
        ]

    def __str__(self):
        return f"{self.task} — {self.started_at:%d %b %Y %H:%M}"

    def clean(self):
        super().clean()
        if self.ended_at and self.ended_at < self.started_at:
            raise ValidationError({"ended_at": "End time cannot be before start time."})
        if (self.ended_at is None) != (self.duration is None):
            raise ValidationError("End time and duration must either both be set or both be empty.")


def task_attachment_path(instance, filename):
    """Stable, non-user-derived storage path ready for local or S3 storage."""
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
    return f"tasker/attachments/{instance.task_id}/{uuid.uuid4().hex}.{suffix}"


class TaskAttachment(AuditedSoftDeleteModel):
    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(storage=private_document_storage, upload_to=task_attachment_path, null=True, blank=True)
    original_name = models.CharField(max_length=255)
    content_type = models.CharField(max_length=150, blank=True)
    file_size = models.PositiveBigIntegerField(default=0)
    description = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["task", "created_at"], name="tasker_attach_task_date_idx")]

    def __str__(self):
        return self.original_name


class TaskActivity(AuditedSoftDeleteModel):
    """Immutable, user-facing history event for a task."""

    class EventType(models.TextChoices):
        CREATED = "CREATED", "Created"
        UPDATED = "UPDATED", "Updated"
        STATUS = "STATUS", "Status changed"
        PROGRESS = "PROGRESS", "Progress changed"
        COMMENT = "COMMENT", "Comment added"
        CHECKLIST = "CHECKLIST", "Checklist updated"
        TIME = "TIME", "Time logged"
        DELETED = "DELETED", "Deleted"
        RESTORED = "RESTORED", "Restored"

    task = models.ForeignKey(Task, on_delete=models.CASCADE, related_name="activities")
    event_type = models.CharField(max_length=15, choices=EventType.choices)
    description = models.CharField(max_length=500)
    changes = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name_plural = "Task activities"
        indexes = [
            models.Index(fields=["task", "-created_at"], name="tasker_activity_task_idx"),
            models.Index(fields=["event_type", "created_at"], name="tasker_activity_type_idx"),
        ]

    def __str__(self):
        return f"{self.get_event_type_display()}: {self.task}"


class AIConfiguration(AuditedSoftDeleteModel):
    """Runtime-safe AI policy. Secrets always remain in environment variables."""

    class Provider(models.TextChoices):
        OPENAI = "OPENAI", "OpenAI"

    class ReasoningEffort(models.TextChoices):
        NONE = "none", "None"
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        XHIGH = "xhigh", "Extra high"

    class ResponseVerbosity(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"

    name = models.CharField(max_length=100, default="Default")
    provider = models.CharField(max_length=20, choices=Provider.choices, default=Provider.OPENAI)
    model = models.CharField(max_length=100, default="gpt-5.6-terra")
    reasoning_effort = models.CharField(
        max_length=10, choices=ReasoningEffort.choices, default=ReasoningEffort.LOW
    )
    response_verbosity = models.CharField(
        max_length=10, choices=ResponseVerbosity.choices, default=ResponseVerbosity.MEDIUM
    )
    system_prompt_override = models.TextField(blank=True)
    temperature = models.DecimalField(
        max_digits=3,
        decimal_places=2,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(2)],
        help_text="Leave empty when the selected model does not support temperature.",
    )
    max_output_tokens = models.PositiveIntegerField(
        default=2000, validators=[MinValueValidator(1), MaxValueValidator(100000)]
    )
    timeout_seconds = models.PositiveSmallIntegerField(
        default=45, validators=[MinValueValidator(1), MaxValueValidator(600)]
    )
    max_retries = models.PositiveSmallIntegerField(
        default=2, validators=[MinValueValidator(0), MaxValueValidator(10)]
    )
    retry_base_seconds = models.DecimalField(
        max_digits=4,
        decimal_places=2,
        default=0.5,
        validators=[MinValueValidator(0.1), MaxValueValidator(30)],
    )
    requests_per_minute = models.PositiveIntegerField(default=20, validators=[MinValueValidator(1)])
    daily_request_limit = models.PositiveIntegerField(default=100, validators=[MinValueValidator(1)])
    daily_token_limit = models.PositiveIntegerField(default=250000, validators=[MinValueValidator(1)])
    allow_streaming = models.BooleanField(default=True)
    store_provider_responses = models.BooleanField(
        default=False,
        help_text="Keep disabled unless data retention has been explicitly approved.",
    )
    is_enabled = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-is_active", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["provider"],
                condition=Q(is_active=True, is_deleted=False),
                name="tasker_ai_active_provider_uniq",
            ),
            models.CheckConstraint(condition=Q(max_output_tokens__gte=1), name="tasker_ai_cfg_tokens_pos"),
            models.CheckConstraint(condition=Q(timeout_seconds__gte=1), name="tasker_ai_cfg_timeout_pos"),
            models.CheckConstraint(condition=Q(max_retries__lte=10), name="tasker_ai_cfg_retries_max"),
            models.CheckConstraint(
                condition=Q(temperature__isnull=True) | Q(temperature__gte=0, temperature__lte=2),
                name="tasker_ai_cfg_temp_range",
            ),
            models.CheckConstraint(
                condition=Q(requests_per_minute__gte=1, daily_request_limit__gte=1, daily_token_limit__gte=1),
                name="tasker_ai_cfg_limits_pos",
            ),
        ]
        indexes = [models.Index(fields=["provider", "is_active"], name="tasker_ai_cfg_active_idx")]
        permissions = [
            ("use_ai", "Can use Boforg AI services"),
            ("view_ai_usage", "Can view organization AI usage"),
            ("manage_ai_configuration", "Can manage AI configuration"),
        ]

    def __str__(self):
        state = "enabled" if self.is_enabled else "disabled"
        return f"{self.name} ({self.model}, {state})"


class PromptTemplate(AuditedSoftDeleteModel):
    """Versioned, centrally managed prompt content."""

    key = models.SlugField(max_length=120)
    name = models.CharField(max_length=150)
    purpose = models.TextField(blank=True)
    version = models.PositiveIntegerField(default=1, validators=[MinValueValidator(1)])
    system_template = models.TextField()
    user_template = models.TextField()
    variables = models.JSONField(default=list, blank=True)
    output_schema = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["key", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["key", "version"],
                condition=Q(is_deleted=False),
                name="tasker_prompt_key_ver_uniq",
            ),
            models.UniqueConstraint(
                fields=["key"],
                condition=Q(is_active=True, is_deleted=False),
                name="tasker_prompt_active_key_uniq",
            ),
            models.CheckConstraint(condition=Q(version__gte=1), name="tasker_prompt_version_pos"),
        ]
        indexes = [models.Index(fields=["key", "is_active"], name="tasker_prompt_active_idx")]

    def clean(self):
        super().clean()
        if not isinstance(self.variables, list) or not all(
            isinstance(item, str) and item for item in self.variables
        ):
            raise ValidationError({"variables": "Variables must be a list of non-empty names."})

    def __str__(self):
        return f"{self.name} v{self.version}"


class AIConversation(AuditedSoftDeleteModel):
    class Status(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        ARCHIVED = "ARCHIVED", "Archived"

    session_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_conversations"
    )
    title = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.ACTIVE)
    agent_type = models.SlugField(max_length=80, default="assistant")
    last_message_at = models.DateTimeField(null=True, blank=True)
    provider_conversation_id = models.CharField(max_length=255, blank=True)
    context_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-created_at"]
        indexes = [
            models.Index(fields=["owner", "status", "-last_message_at"], name="tasker_ai_conv_owner_idx"),
            models.Index(fields=["agent_type", "status"], name="tasker_ai_conv_agent_idx"),
        ]

    def __str__(self):
        return self.title or f"Conversation {self.session_id}"


class AIMessage(AuditedSoftDeleteModel):
    class Role(models.TextChoices):
        SYSTEM = "SYSTEM", "System"
        USER = "USER", "User"
        ASSISTANT = "ASSISTANT", "Assistant"
        TOOL = "TOOL", "Tool"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"
        INTERRUPTED = "INTERRUPTED", "Interrupted"

    conversation = models.ForeignKey(
        AIConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=12, choices=Role.choices)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.COMPLETED)
    sequence = models.PositiveIntegerField()
    content = models.TextField(blank=True)
    prompt_template = models.ForeignKey(
        PromptTemplate, on_delete=models.PROTECT, null=True, blank=True, related_name="messages"
    )
    provider_response_id = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    message_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["sequence"]
        constraints = [
            models.UniqueConstraint(
                fields=["conversation", "sequence"],
                condition=Q(is_deleted=False),
                name="tasker_ai_msg_sequence_uniq",
            ),
            models.CheckConstraint(
                condition=Q(total_tokens=models.F("input_tokens") + models.F("output_tokens")),
                name="tasker_ai_msg_token_total",
            ),
        ]
        indexes = [
            models.Index(fields=["conversation", "sequence"], name="tasker_ai_msg_conv_idx"),
            models.Index(fields=["role", "created_at"], name="tasker_ai_msg_role_idx"),
        ]

    def __str__(self):
        return f"{self.get_role_display()} message {self.sequence}"


class AIUsageLog(AuditedSoftDeleteModel):
    class Status(models.TextChoices):
        STARTED = "STARTED", "Started"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        RATE_LIMITED = "RATE_LIMITED", "Rate limited"
        DISABLED = "DISABLED", "AI disabled"
        INTERRUPTED = "INTERRUPTED", "Interrupted"

    request_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tasker_ai_usage",
    )
    conversation = models.ForeignKey(
        AIConversation, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_logs"
    )
    message = models.ForeignKey(
        AIMessage, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_logs"
    )
    configuration = models.ForeignKey(
        AIConfiguration, on_delete=models.SET_NULL, null=True, blank=True, related_name="usage_logs"
    )
    provider = models.CharField(max_length=30, default="OPENAI")
    model = models.CharField(max_length=100, blank=True)
    operation = models.SlugField(max_length=100)
    status = models.CharField(max_length=15, choices=Status.choices, default=Status.STARTED)
    provider_request_id = models.CharField(max_length=255, blank=True)
    provider_response_id = models.CharField(max_length=255, blank=True)
    input_tokens = models.PositiveIntegerField(default=0)
    cached_input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    reasoning_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    latency_ms = models.PositiveIntegerField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=1)
    was_streamed = models.BooleanField(default=False)
    started_at = models.DateTimeField(default=timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    error_type = models.CharField(max_length=100, blank=True)
    error_code = models.CharField(max_length=100, blank=True)
    error_message = models.CharField(max_length=500, blank=True)
    request_metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_tokens=models.F("input_tokens") + models.F("output_tokens")),
                name="tasker_ai_usage_token_total",
            ),
            models.CheckConstraint(
                condition=Q(completed_at__isnull=True) | Q(completed_at__gte=models.F("started_at")),
                name="tasker_ai_usage_time_order",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "started_at"], name="tasker_ai_usage_user_idx"),
            models.Index(fields=["status", "started_at"], name="tasker_ai_usage_status_idx"),
            models.Index(fields=["model", "started_at"], name="tasker_ai_usage_model_idx"),
            models.Index(fields=["operation", "started_at"], name="tasker_ai_usage_op_idx"),
        ]

    def __str__(self):
        return f"{self.operation}: {self.get_status_display()}"


class AIFeedback(AuditedSoftDeleteModel):
    class Rating(models.IntegerChoices):
        UNHELPFUL = -1, "Unhelpful"
        HELPFUL = 1, "Helpful"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_feedback"
    )
    message = models.ForeignKey(AIMessage, on_delete=models.PROTECT, related_name="feedback")
    rating = models.SmallIntegerField(choices=Rating.choices)
    category = models.CharField(max_length=100, blank=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "message"],
                condition=Q(is_deleted=False),
                name="tasker_ai_feedback_user_uniq",
            ),
            models.CheckConstraint(condition=Q(rating__in=(-1, 1)), name="tasker_ai_feedback_rating"),
        ]
        indexes = [models.Index(fields=["rating", "created_at"], name="tasker_ai_feedback_idx")]

    def __str__(self):
        return f"{self.get_rating_display()} by {self.user}"


def _available_slug(instance, value):
    base = slugify(value)[:240] or "item"
    candidate = base
    queryset = instance.__class__.all_objects.all()
    if instance.pk:
        queryset = queryset.exclude(pk=instance.pk)
    counter = 2
    while queryset.filter(slug=candidate, is_deleted=False).exists():
        candidate = f"{base[:230]}-{counter}"
        counter += 1
    return candidate
