"""Private manager drafts and immutable records of explicitly committed changes."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .models import AuditedSoftDeleteModel


class StrategyScenario(AuditedSoftDeleteModel):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft preview"
        COMMITTED = "COMMITTED", "Committed"
        DISCARDED = "DISCARDED", "Discarded"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_scenarios")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, related_name="scenarios")
    title = models.CharField(max_length=140)
    changes = models.JSONField(default=list)
    source_proofs = models.JSONField(default=dict)
    preview = models.JSONField(default=dict)
    source_digest = models.CharField(max_length=64)
    preview_digest = models.CharField(max_length=64)
    request_digest = models.CharField(max_length=64)
    client_key = models.UUIDField(default=uuid.uuid4, editable=False)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    state = models.CharField(max_length=12, choices=State.choices, default=State.DRAFT)
    expires_at = models.DateTimeField()
    committed_at = models.DateTimeField(null=True, blank=True)
    discarded_at = models.DateTimeField(null=True, blank=True)
    notification_ids = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [
            models.UniqueConstraint(fields=("owner", "client_key"), name="tasker_scenario_retry_once"),
            models.CheckConstraint(condition=Q(state__in=("DRAFT", "COMMITTED", "DISCARDED")), name="tasker_scenario_state"),
        ]
        indexes = [models.Index(fields=("owner", "state", "created_at"), name="tasker_scenario_owner_idx")]

    def clean(self):
        super().clean()
        if self.pk:
            before = type(self).all_objects.filter(pk=self.pk).first()
            if before:
                immutable = ("owner_id", "team_id", "title", "changes", "source_proofs", "preview",
                    "source_digest", "preview_digest", "request_digest", "client_key", "expires_at")
                if any(getattr(before, name) != getattr(self, name) for name in immutable):
                    raise ValidationError("A scenario retains the exact preview and source evidence that was reviewed.")
                if before.state != self.State.DRAFT and any(getattr(before, name) != getattr(self, name)
                        for name in ("state", "revision", "committed_at", "discarded_at", "notification_ids")):
                    raise ValidationError("A closed scenario cannot be reopened or changed.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"Scenario {self.pk}: {self.get_state_display()}"


class StrategyScenarioEvent(models.Model):
    scenario = models.ForeignKey(StrategyScenario, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=16)
    revision = models.UUIDField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Scenario audit events are immutable.")
        if self.action not in ("CREATED", "COMMITTED", "DISCARDED"):
            raise ValidationError("Choose a fixed scenario audit action.")
        if type(self.details) is not dict or set(self.details) - {"task_ids", "notification_ids"}:
            raise ValidationError("Scenario audit details contain identifiers only.")
        for values in self.details.values():
            if type(values) is not list or len(values) > 40 or any(type(value) is not int or value < 1 for value in values):
                raise ValidationError("Scenario audit identifiers must be bounded positive integers.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Scenario audit events are immutable.")
