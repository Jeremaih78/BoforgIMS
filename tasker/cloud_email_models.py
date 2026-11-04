"""Additive, default-off staff transport policy and evidence. No email in model saves."""

import uuid
from django.conf import settings
from django.db import models
from django.utils import timezone


def default_categories():
    return ["assignment", "request", "escalation", "update", "business"]


class StaffEmailPolicy(models.Model):
    company = models.OneToOneField("accounting.Company", on_delete=models.PROTECT)
    enabled = models.BooleanField(default=False)
    tenant_id = models.CharField(max_length=64, blank=True)
    client_id = models.UUIDField(null=True, blank=True)
    activation_after = models.DateTimeField(null=True, blank=True)
    categories = models.JSONField(default=default_categories)
    due_soon_days = models.PositiveSmallIntegerField(default=1)
    reminders_enabled = models.BooleanField(default=False)
    reminder_cursor = models.PositiveBigIntegerField(default=0, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        from django.core.exceptions import ValidationError
        from django.utils import timezone

        super().clean()
        allowed = {
            "assignment",
            "request",
            "escalation",
            "update",
            "business",
            "due_soon",
            "overdue",
            "report",
        }
        if (
            not isinstance(self.categories, list)
            or not all(isinstance(item, str) for item in self.categories)
            or set(self.categories) - allowed
        ):
            raise ValidationError("Choose supported staff email categories.")
        if self.activation_after and timezone.is_naive(self.activation_after):
            raise ValidationError("Choose a timezone-aware prospective activation watermark.")
        if self.enabled and (not self.tenant_id or not self.client_id or not self.activation_after):
            raise ValidationError(
                "Map the company and choose a prospective watermark before enabling policy."
            )
        if self.pk and type(self).objects.filter(pk=self.pk).exists():
            previous = type(self).objects.get(pk=self.pk)
            if self.staffemailbinding_set.exists() and any(
                getattr(previous, name) != getattr(self, name)
                for name in ("company_id", "tenant_id", "client_id")
            ):
                raise ValidationError(
                    "Existing transport evidence keeps its original company/client mapping."
                )


class StaffEmailPreference(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    assignments = models.BooleanField(default=True)
    requests = models.BooleanField(default=True)
    updates = models.BooleanField(default=True)
    reminders = models.BooleanField(default=False)
    reports = models.BooleanField(default=True)
    digest_reminders = models.BooleanField(default=False)


class StaffEmailBinding(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    delivery = models.OneToOneField(
        "tasker.NotificationDelivery", on_delete=models.PROTECT, related_name="cloud_email"
    )
    policy = models.ForeignKey(StaffEmailPolicy, on_delete=models.PROTECT)
    category = models.CharField(max_length=24)
    source_digest = models.CharField(max_length=64)
    occurrence = models.CharField(max_length=160, unique=True)
    payload = models.JSONField(default=dict)
    payload_digest = models.CharField(max_length=64, blank=True)
    phase = models.CharField(max_length=24, default="held")
    remote_message_id = models.UUIDField(null=True, unique=True)
    remote_version = models.PositiveIntegerField(default=0)
    remote_state = models.CharField(max_length=24, blank=True)
    cancel_requested = models.BooleanField(default=False)
    error_code = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    checked_at = models.DateTimeField(null=True)
    digest_members = models.JSONField(default=list, blank=True)


class StaffEmailReceipt(models.Model):
    event_id = models.UUIDField(primary_key=True)
    binding = models.ForeignKey(
        StaffEmailBinding, on_delete=models.PROTECT, related_name="receipts"
    )
    payload = models.JSONField()
    quarantined = models.BooleanField(default=False)
    received_at = models.DateTimeField(default=timezone.now)


class StaffEmailAuthorization(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    binding = models.ForeignKey(StaffEmailBinding, on_delete=models.PROTECT)
    request_id = models.UUIDField(unique=True)
    message_id = models.UUIDField()
    payload_digest = models.CharField(max_length=64)
    decision = models.CharField(max_length=8)
    reason = models.CharField(max_length=80)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(default=timezone.now)
