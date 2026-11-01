"""Explicit shared work confirmations; activity never stands in for presence."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class WorkDeclaration(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_work_declarations")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, related_name="work_declarations")
    current_task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="declared_current_work")
    next_task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="declared_next_work")
    current_task_version = models.CharField(max_length=64, blank=True)
    next_task_version = models.CharField(max_length=64, blank=True)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    confirmed_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=("user", "team"), name="tasker_work_decl_user_team"),
            models.CheckConstraint(condition=Q(current_task__isnull=True) | Q(next_task__isnull=True) | ~Q(current_task=models.F("next_task")), name="tasker_work_decl_distinct"),
        ]
        indexes = [models.Index(fields=("team", "confirmed_at"), name="tasker_work_decl_team_idx")]

    def clean(self):
        super().clean()
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(user_id=self.user_id, team_id=self.team_id).exists():
            raise ValidationError("A work declaration retains its original employee and team.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Explicit work confirmation"


class WorkDeclarationEvent(models.Model):
    declaration = models.ForeignKey(WorkDeclaration, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_work_confirmation_events")
    current_task_id = models.PositiveBigIntegerField(null=True, blank=True)
    next_task_id = models.PositiveBigIntegerField(null=True, blank=True)
    revision = models.UUIDField()
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Work confirmation evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Work confirmation evidence is retained.")
