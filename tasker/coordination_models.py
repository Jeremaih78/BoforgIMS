"""Explicit shared requests; receipt never silently transfers a Task."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class CoordinationRequest(models.Model):
    class Kind(models.TextChoices):
        BLOCKER = "BLOCKER", "Blocker"
        HANDOFF = "HANDOFF", "Handoff"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Awaiting acknowledgement"
        ACKNOWLEDGED = "ACKNOWLEDGED", "Acknowledged; action pending"
        RESOLVED = "RESOLVED", "Resolved"
        CANCELLED = "CANCELLED", "Cancelled"

    class EscalationPolicy(models.TextChoices):
        NONE = "NONE", "No automatic escalation"
        UNACKNOWLEDGED = "UNACKNOWLEDGED", "Escalate unacknowledged work after the expected response"

    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, related_name="coordination_requests")
    kind = models.CharField(max_length=10, choices=Kind.choices)
    raised_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="raised_coordination_requests")
    resolver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assigned_coordination_requests")
    reason = models.CharField(max_length=1000)
    requested_action = models.CharField(max_length=1000)
    expected_response_at = models.DateTimeField()
    raised_at = models.DateTimeField(default=timezone.now, editable=False)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.OPEN)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    client_key = models.UUIDField(default=uuid.uuid4, editable=False)
    acknowledged_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolved_at = models.DateTimeField(null=True, blank=True, editable=False)
    resolution = models.CharField(max_length=1000, blank=True)
    escalation_policy = models.CharField(max_length=20, choices=EscalationPolicy.choices, default=EscalationPolicy.NONE)
    escalated_at = models.DateTimeField(null=True, blank=True, editable=False)
    escalated_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="escalated_coordination_requests")
    source_owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="owned_coordination_sources")
    source_company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, null=True, blank=True, related_name="coordination_sources")
    source_team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="coordination_sources")
    blocks_task = models.BooleanField(default=True)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("expected_response_at", "raised_at", "pk")
        indexes = [models.Index(fields=("resolver", "status", "expected_response_at"), name="tasker_coord_resolver_idx")]
        constraints = [models.UniqueConstraint(fields=("raised_by", "client_key"), name="tasker_coord_client_uniq"),
            models.UniqueConstraint(fields=("task", "kind"), condition=Q(status__in=("OPEN", "ACKNOWLEDGED")), name="tasker_coord_active_kind_uniq")]

    def clean(self):
        super().clean()
        for name in ("reason", "requested_action"):
            text = getattr(self, name)
            if not isinstance(text, str) or not text.strip() or "\x00" in text:
                raise ValidationError({name: "Record a clear shared reason and requested action."})
        if not self.expected_response_at or timezone.is_naive(self.expected_response_at):
            raise ValidationError({"expected_response_at": "Choose an explicit timezone-aware response time."})
        if "\x00" in self.resolution:
            raise ValidationError({"resolution": "Use a resolution without null characters."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.get_kind_display()} request {self.pk}"


class CoordinationEvent(models.Model):
    request = models.ForeignKey(CoordinationRequest, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True)
    action = models.CharField(max_length=40)
    revision = models.UUIDField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Coordination history is immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.action
