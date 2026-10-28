"""Private deterministic guidance, bounded evidence and employee choices."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


DEFAULT_WEIGHTS = {"goal": 60, "deadline": 20, "urgency": 10, "unlock": 15,
                   "age": 5, "effort": 3, "context": 10}


def default_operator_weights():
    return dict(DEFAULT_WEIGHTS)


def validate_operator_weights(value):
    if not isinstance(value, dict) or set(value) != set(DEFAULT_WEIGHTS):
        raise ValidationError("Supply all seven operator ranking weights.")
    if any(type(weight) is not int or weight < (1 if key == "age" else 0) or weight > 100
           for key, weight in value.items()):
        raise ValidationError("Weights must be whole numbers from 0 to 100; age must be at least 1.")


class OperatorPolicy(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_operator_policy")
    weights = models.JSONField(default=default_operator_weights, validators=[validate_operator_weights])
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def clean(self):
        super().clean()
        validate_operator_weights(self.weights)
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(owner_id=self.owner_id).exists():
            raise ValidationError("An operator policy keeps its original owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Personal operator ranking policy"


class OperatorState(models.Model):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_operator_state")
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    reason = models.CharField(max_length=100, default="initial")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Operator freshness state"


class OperatorSnapshot(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_operator_snapshots")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    policy = models.JSONField(default=dict)
    candidates = models.JSONField(default=list)
    exclusions = models.JSONField(default=dict)
    state_revision = models.UUIDField()
    source_digest = models.CharField(max_length=64)
    selected_task_id = models.PositiveBigIntegerField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("owner", "created_at"), name="tasker_op_snapshot_owner_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Recommendation evidence is immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Private operator recommendation"


class OperatorOverride(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_operator_choices")
    snapshot = models.ForeignKey(OperatorSnapshot, on_delete=models.PROTECT, null=True, blank=True, related_name="choices")
    task_id = models.PositiveBigIntegerField(null=True, blank=True)
    action = models.CharField(max_length=32)
    reason = models.CharField(max_length=1000, blank=True)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("owner", "created_at"), name="tasker_op_choice_owner_idx")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Recorded operator choices are immutable.")
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Recorded operator choice"
