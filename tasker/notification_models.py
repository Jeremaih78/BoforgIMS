"""Targeted attention and one shared in-app/email delivery ledger."""
from datetime import time
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone

from tasker.planning_models import validate_zone


class NotificationPreference(models.Model):
    class DigestMode(models.TextChoices):
        IMMEDIATE = "IMMEDIATE", "When available"
        HOURLY = "HOURLY", "Hourly digest"

    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_notification_preference")
    timezone_name = models.CharField(max_length=64, default="Africa/Harare", validators=[validate_zone])
    quiet_enabled = models.BooleanField(default=False)
    quiet_start = models.TimeField(default=time(22))
    quiet_end = models.TimeField(default=time(7))
    respect_work_hours = models.BooleanField(default=True)
    suppress_during_focus = models.BooleanField(default=True)
    digest_mode = models.CharField(max_length=10, choices=DigestMode.choices, default=DigestMode.IMMEDIATE)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValidationError({"quiet_end": "Quiet start and end must differ."})
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(owner_id=self.owner_id).exists():
            raise ValidationError("Notification preferences retain their original owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Personal notification preferences"


class Notification(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_notifications")
    kind = models.CharField(max_length=32)
    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="notifications")
    request = models.ForeignKey("tasker.CoordinationRequest", on_delete=models.PROTECT, null=True, blank=True, related_name="notifications")
    report_snapshot = models.ForeignKey("tasker.ManagerReportSnapshot", on_delete=models.PROTECT, null=True, blank=True, related_name="notifications")
    dedupe_key = models.CharField(max_length=200, unique=True)
    source_revision = models.CharField(max_length=64, blank=True)
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    published_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    summary_count = models.PositiveIntegerField(default=1)
    grouped_into = models.ForeignKey("self", on_delete=models.PROTECT, null=True, blank=True, related_name="digest_items")

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("owner", "published_at", "read_at"), name="tasker_notify_owner_idx")]

    def __str__(self):
        return f"Targeted notification {self.pk}"


class NotificationDelivery(models.Model):
    class Channel(models.TextChoices):
        IN_APP = "IN_APP", "In app"
        EMAIL = "EMAIL", "Email"

    class State(models.TextChoices):
        QUEUED = "QUEUED", "Queued"
        HELD = "HELD", "Held for delivery window"
        TEST_ACCEPTED = "TEST_ACCEPTED", "Accepted by test adapter"
        PROVIDER_ACCEPTED = "PROVIDER_ACCEPTED", "Accepted by provider"
        DELIVERED = "DELIVERED", "Delivered in app"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"
        UNKNOWN = "UNKNOWN", "Delivery outcome unknown"

    notification = models.ForeignKey(Notification, on_delete=models.PROTECT, null=True, blank=True, related_name="deliveries")
    report_snapshot = models.ForeignKey("tasker.ManagerReportSnapshot", on_delete=models.PROTECT, null=True, blank=True, related_name="deliveries")
    recipient = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_deliveries")
    channel = models.CharField(max_length=10, choices=Channel.choices)
    state = models.CharField(max_length=20, choices=State.choices, default=State.QUEUED)
    dedupe_key = models.CharField(max_length=200, unique=True)
    attempt_count = models.PositiveIntegerField(default=0)
    error_code = models.CharField(max_length=80, blank=True)
    queued_at = models.DateTimeField(default=timezone.now, editable=False)
    attempted_at = models.DateTimeField(null=True, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    next_attempt_at = models.DateTimeField(null=True, blank=True)
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("-queued_at", "-pk")
        indexes = [models.Index(fields=("state", "next_attempt_at"), name="tasker_delivery_due_idx")]
        constraints = [models.CheckConstraint(condition=(Q(notification__isnull=False, report_snapshot__isnull=True) |
            Q(notification__isnull=True, report_snapshot__isnull=False)), name="tasker_delivery_one_source")]

    def __str__(self):
        return f"{self.get_channel_display()} delivery {self.pk}"
