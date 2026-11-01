"""Content-minimal references to authoritative business actions, never Task copies."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class BusinessRecordLink(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open at source"
        IN_PROGRESS = "IN_PROGRESS", "In progress at source"
        COMPLETED = "COMPLETED", "Completed at source"
        CANCELLED = "CANCELLED", "Closed or covered by source work"
        UNASSIGNED = "UNASSIGNED", "Responsible owner needed"
        UNAVAILABLE = "UNAVAILABLE", "Source review needed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_app = models.CharField(max_length=32, default="credit_control")
    source_type = models.CharField(max_length=32)
    source_record_id = models.PositiveBigIntegerField()
    action_key = models.CharField(max_length=32)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasker_business_actions")
    company = models.ForeignKey("accounting.Company", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="tasker_business_actions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNAVAILABLE)
    title = models.CharField(max_length=160)
    due_date = models.DateField(null=True, blank=True)
    priority = models.CharField(max_length=10, default="MEDIUM")
    source_status = models.CharField(max_length=32, blank=True)
    source_version = models.CharField(max_length=64, blank=True)
    source_digest = models.CharField(max_length=64)
    reason_code = models.CharField(max_length=64, blank=True)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    source_updated_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(default=timezone.now)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("due_date", "created_at", "pk")
        constraints = [
            models.UniqueConstraint(fields=("source_app", "source_type", "source_record_id", "action_key"), name="tasker_business_action_identity"),
            models.CheckConstraint(condition=Q(source_record_id__gt=0), name="tasker_business_source_positive"),
            models.CheckConstraint(condition=Q(status__in=("OPEN", "IN_PROGRESS", "COMPLETED", "CANCELLED", "UNASSIGNED", "UNAVAILABLE")), name="tasker_business_status"),
        ]
        indexes = [models.Index(fields=("owner", "status", "due_date"), name="tasker_business_owner_idx"),
            models.Index(fields=("source_app", "source_type", "source_record_id"), name="tasker_business_source_idx")]

    def clean(self):
        super().clean()
        valid = {("credit_control", "collection_task"): "follow_up",
            ("credit_control", "promise_to_pay"): "review_promise",
            ("inventory", "replenishment_plan"): "replenish_stock"}
        from operations.registry import SOURCES
        valid.update({('operations',kind):'review_source' for kind in SOURCES})
        if valid.get((self.source_app, self.source_type)) != self.action_key:
            raise ValidationError("Choose a registered business source and action.")
        if self.priority not in ("LOW", "MEDIUM", "HIGH", "CRITICAL"):
            raise ValidationError("Choose a supported business action priority.")
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field) for field in
                    ("source_app", "source_type", "source_record_id", "action_key")):
                raise ValidationError("A business reference retains its original canonical identity.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    @property
    def state(self):
        return self.status

    @property
    def refreshed_at(self):
        return self.last_reconciled_at

    @property
    def status_label(self):
        if self.source_type == "replenishment_plan" and self.status == self.Status.COMPLETED:
            return "Replenishment need cleared"
        return self.get_status_display()

    def __str__(self):
        return f"Business action {self.pk}"

    def get_priority_display(self):
        return self.priority.title()

    def get_absolute_url(self):
        from django.urls import reverse
        return reverse("ims:tasker:business_detail", kwargs={"pk": self.pk})


class BusinessLinkEvent(models.Model):
    """Idempotent observed source state, not payment or productivity evidence."""
    link = models.ForeignKey(BusinessRecordLink, on_delete=models.PROTECT, related_name="events")
    event_key = models.CharField(max_length=64)
    status = models.CharField(max_length=16, choices=BusinessRecordLink.Status.choices)
    owner_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    source_version = models.CharField(max_length=64, blank=True)
    reason_code = models.CharField(max_length=64, blank=True)
    revision = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [models.UniqueConstraint(fields=("link", "event_key"), name="tasker_business_event_once")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Business source evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Business source evidence is retained.")


class BusinessSyncCursor(models.Model):
    key = models.CharField(primary_key=True, max_length=40)
    collection_after = models.PositiveBigIntegerField(default=0)
    promise_after = models.PositiveBigIntegerField(default=0)
    link_after = models.UUIDField(null=True, blank=True)
    phase = models.PositiveSmallIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
