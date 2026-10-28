"""One PostgreSQL transactional outbox; payloads reference canonical records."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class OutboxJob(models.Model):
    class State(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=64)
    dedupe_key = models.CharField(max_length=200, unique=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
        related_name="tasker_outbox_jobs")
    payload = models.JSONField(default=dict)
    payload_digest = models.CharField(max_length=64)
    state = models.CharField(max_length=12, choices=State.choices, default=State.QUEUED)
    run_after = models.DateTimeField(default=timezone.now)
    attempts = models.PositiveSmallIntegerField(default=0)
    max_attempts = models.PositiveSmallIntegerField(default=5)
    deferrals = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True, editable=False)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error_code = models.CharField(max_length=64, blank=True)
    result = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ("run_after", "created_at", "pk")
        indexes = [models.Index(fields=("state", "run_after"), name="tasker_job_due_idx"),
            models.Index(fields=("state", "lease_expires_at"), name="tasker_job_lease_idx"),
            models.Index(fields=("owner", "created_at"), name="tasker_job_owner_idx")]
        constraints = [models.CheckConstraint(condition=Q(max_attempts__gte=1, max_attempts__lte=20), name="tasker_job_attempt_limit"),
            models.CheckConstraint(condition=Q(state__in=("QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED")), name="tasker_job_valid_state")]

    def __str__(self):
        return f"{self.kind}: {self.get_state_display()}"


class JobWorkerHeartbeat(models.Model):
    name = models.CharField(max_length=100, primary_key=True)
    role = models.CharField(max_length=12, default="worker")
    seen_at = models.DateTimeField(default=timezone.now)
    last_job_id = models.UUIDField(null=True, blank=True)
    completed = models.PositiveBigIntegerField(default=0)

    def __str__(self):
        return f"{self.role}: {self.name}"
