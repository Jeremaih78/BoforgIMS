"""Owner-private asynchronous AI requests; jobs contain only these record IDs."""
import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q

from .models import AuditedSoftDeleteModel


class AIWorkRequest(AuditedSoftDeleteModel):
    class Feature(models.TextChoices):
        CAPTURE = "CAPTURE", "Understand a capture"
        PLAN = "PLAN", "Suggest a feasible plan"
        EXPLAIN = "EXPLAIN", "Explain a task"
        BREAKDOWN = "BREAKDOWN", "Break a task into steps"
        BRIEFING = "BRIEFING", "Grounded briefing"
        COORDINATION_SUMMARY = "COORDINATION_SUMMARY", "Summarize blockers and handoffs"
        MEETING = "MEETING", "Review supplied meeting notes"

    class Scope(models.TextChoices):
        PERSONAL = "PERSONAL", "Personal"
        MANAGER = "MANAGER", "Shared team facts"

    class Status(models.TextChoices):
        QUEUED = "QUEUED", "Waiting for the AI worker"
        RUNNING = "RUNNING", "Preparing a suggestion"
        COMPLETED = "COMPLETED", "Ready for review"
        FAILED = "FAILED", "Unavailable"
        UNKNOWN = "UNKNOWN", "Provider outcome uncertain"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_work")
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, null=True, blank=True)
    feature = models.CharField(max_length=32, choices=Feature.choices)
    scope = models.CharField(max_length=12, choices=Scope.choices, default=Scope.PERSONAL)
    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, null=True, blank=True)
    capture = models.ForeignKey("tasker.CaptureItem", on_delete=models.PROTECT, null=True, blank=True)
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True)
    question = models.TextField(blank=True)
    context_snapshot = models.JSONField(default=dict, blank=True)
    context_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    client_key = models.UUIDField(default=uuid.uuid4)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.QUEUED)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    job = models.ForeignKey("tasker.OutboxJob", on_delete=models.PROTECT, null=True, blank=True)
    usage_log = models.ForeignKey("tasker.AIUsageLog", on_delete=models.PROTECT, null=True, blank=True)
    conversation = models.ForeignKey("tasker.AIConversation", on_delete=models.PROTECT, null=True, blank=True)
    dispatch_nonce = models.UUIDField(null=True, blank=True, editable=False)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    error_code = models.CharField(max_length=64, blank=True)
    result_summary = models.JSONField(default=dict, blank=True)
    request_metadata = models.JSONField(default=dict, blank=True)
    retry_of = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True)
    content_redacted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [
            models.Index(fields=("owner", "created_at"), name="tasker_ai_work_owner_idx"),
            models.Index(fields=("status", "expires_at"), name="tasker_ai_work_state_idx"),
        ]
        constraints = [
            models.UniqueConstraint(fields=("owner", "client_key"), name="tasker_ai_work_retry_uniq"),
            models.CheckConstraint(condition=Q(status__in=("QUEUED", "RUNNING", "COMPLETED", "FAILED", "UNKNOWN", "CANCELLED")), name="tasker_ai_work_valid_state"),
        ]

    def __str__(self):
        return f"{self.get_feature_display()}: {self.get_status_display()}"
