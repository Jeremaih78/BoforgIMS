"""Immutable self-reported arrival declarations and their correction ledger."""
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


class ArrivalDeclaration(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_arrivals")
    local_date = models.DateField(db_index=True, editable=False)
    timezone_name = models.CharField(max_length=64, editable=False)
    registered_at = models.DateTimeField(default=timezone.now, editable=False)
    teams = models.ManyToManyField("tasker.Team", blank=True, editable=False, related_name="arrival_declarations")

    class Meta:
        ordering = ["-local_date", "-registered_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["user", "local_date"], name="tasker_arrival_user_day_uniq")]

    def __str__(self):
        return f"Arrival declaration {self.pk}: {self.local_date}"

    @property
    def latest_correction(self):
        if hasattr(self, "correction_history"):
            return self.correction_history[0] if self.correction_history else None
        return self.corrections.order_by("-created_at", "-pk").first()

    @property
    def effective_at(self):
        correction = self.latest_correction
        return correction.effective_at if correction else self.registered_at

    @property
    def revision(self):
        correction = self.latest_correction
        return correction.pk if correction else 0

    def clean(self):
        super().clean()
        try:
            zone = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError, TypeError):
            raise ValidationError({"timezone_name": "Choose a valid business timezone."}) from None
        if not self.registered_at or timezone.is_naive(self.registered_at):
            raise ValidationError({"registered_at": "A server timestamp with a timezone is required."})
        if self.registered_at.astimezone(zone).date() != self.local_date:
            raise ValidationError({"local_date": "Arrival date must match its recorded timezone."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Arrival declarations are immutable. Add a correction instead.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Arrival declarations form an audit ledger and cannot be deleted here.")


class ArrivalCorrection(models.Model):
    arrival = models.ForeignKey(ArrivalDeclaration, on_delete=models.PROTECT, related_name="corrections")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_arrival_corrections")
    client_key = models.UUIDField(default=uuid.uuid4, editable=False)
    effective_at = models.DateTimeField()
    reason = models.CharField(max_length=1000)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ["-created_at", "-pk"]
        constraints = [models.UniqueConstraint(fields=["arrival", "client_key"], name="tasker_arrival_correction_uniq")]

    def __str__(self):
        return f"Arrival correction {self.pk}"

    def clean(self):
        super().clean()
        if self.arrival_id and self.actor_id != self.arrival.user_id:
            raise ValidationError("Only the employee can correct their own declaration.")
        if not self.reason or not self.reason.strip():
            raise ValidationError({"reason": "Explain why the declared time needs correction."})
        if not self.effective_at or timezone.is_naive(self.effective_at):
            raise ValidationError({"effective_at": "A timezone-aware declared time is required."})
        if self.arrival_id and self.effective_at.astimezone(ZoneInfo(self.arrival.timezone_name)).date() != self.arrival.local_date:
            raise ValidationError({"effective_at": "The correction must stay on the original local date."})
        if self.effective_at > timezone.now():
            raise ValidationError({"effective_at": "A declared arrival cannot be in the future."})

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("Arrival corrections are immutable. Add another correction instead.")
        self.full_clean()
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Arrival corrections form an audit ledger and cannot be deleted here.")
