"""Local planning records. A plan is neither attendance nor a deadline."""
from datetime import datetime, time, timezone as datetime_timezone
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q
from django.utils import timezone

from .models import AuditedSoftDeleteModel


def default_weekdays():
    return [0, 1, 2, 3, 4]


def validate_zone(value):
    try:
        if not isinstance(value, str) or len(value) > 64 or "\x00" in value:
            raise ValueError
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError):
        raise ValidationError("Choose a valid IANA timezone, such as Africa/Harare.") from None


class WorkPolicy(AuditedSoftDeleteModel):
    owner = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_work_policy")
    timezone_name = models.CharField(max_length=64, default="Africa/Harare", validators=[validate_zone])
    weekdays = models.JSONField(default=default_weekdays, blank=True)
    start_time = models.TimeField(default=time(8))
    end_time = models.TimeField(default=time(17))
    buffer_minutes = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MaxValueValidator(1440)])
    configured = models.BooleanField(default=False)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)

    def clean(self):
        super().clean()
        validate_zone(self.timezone_name)
        if not isinstance(self.weekdays, list) or len(self.weekdays) > 7 or any(type(day) is not int or day not in range(7) for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValidationError({"weekdays": "Choose distinct weekdays from Monday through Sunday."})
        if self.configured and self.buffer_minutes is None:
            raise ValidationError({"buffer_minutes": "Set an explicit interruption buffer, including zero if appropriate."})
        if self.start_time == self.end_time:
            raise ValidationError({"end_time": "Work start and end cannot be the same time."})
        if self.pk and type(self).all_objects.filter(pk=self.pk).exclude(owner_id=self.owner_id).exists():
            raise ValidationError("A work policy keeps its original owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Work hours and capacity policy"


class Availability(AuditedSoftDeleteModel):
    class Kind(models.TextChoices):
        LEAVE = "LEAVE", "Leave / unavailable"
        COMMITMENT = "COMMITMENT", "Fixed commitment"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_availability")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    private_note = models.CharField(max_length=1000, blank=True)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)

    class Meta:
        ordering = ("starts_at", "pk")
        indexes = [models.Index(fields=("owner", "starts_at", "ends_at"), name="tasker_avail_owner_time_idx")]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="tasker_avail_positive_span")]

    def clean(self):
        super().clean()
        validate_span(self.starts_at, self.ends_at, maximum_days=366)
        if "\x00" in self.private_note:
            raise ValidationError({"private_note": "Notes cannot contain null characters."})
        if self.pk and type(self).all_objects.filter(pk=self.pk).exclude(owner_id=self.owner_id).exists():
            raise ValidationError("An availability record keeps its original owner.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return self.get_kind_display()


class ScheduleBlock(AuditedSoftDeleteModel):
    class State(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACCEPTED = "ACCEPTED", "Accepted"
        CANCELLED = "CANCELLED", "Cancelled"

    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, related_name="schedule_blocks")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_schedule_blocks")
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    state = models.CharField(max_length=10, choices=State.choices, default=State.DRAFT)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    override_reason = models.CharField(max_length=1000, blank=True)
    accepted_at = models.DateTimeField(null=True, blank=True)
    acceptance_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ("starts_at", "pk")
        indexes = [models.Index(fields=("owner", "state", "starts_at"), name="tasker_sched_owner_time_idx"),
                   models.Index(fields=("task", "state"), name="tasker_sched_task_state_idx")]
        constraints = [models.CheckConstraint(condition=Q(ends_at__gt=F("starts_at")), name="tasker_sched_positive_span"),
                       models.CheckConstraint(condition=Q(state__in=("DRAFT", "ACCEPTED", "CANCELLED")), name="tasker_sched_valid_state")]

    def clean(self):
        super().clean()
        validate_span(self.starts_at, self.ends_at, maximum_days=1)
        if self.state != self.State.CANCELLED and self.task_id and self.task.owner_id != self.owner_id:
            raise ValidationError("A work block must belong to the task's current owner.")
        if "\x00" in self.override_reason:
            raise ValidationError({"override_reason": "Reasons cannot contain null characters."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Planned work block"


class TaskEffortEstimate(AuditedSoftDeleteModel):
    task = models.OneToOneField("tasker.Task", on_delete=models.PROTECT, related_name="remaining_effort_estimate")
    remaining_minutes = models.PositiveIntegerField(null=True, blank=True, validators=[MaxValueValidator(525600)])
    logged_seconds_at_estimate = models.PositiveBigIntegerField(default=0)
    reason = models.CharField(max_length=1000, blank=True)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)

    def clean(self):
        super().clean()
        if "\x00" in self.reason:
            raise ValidationError({"reason": "Reasons cannot contain null characters."})

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "Remaining effort estimate"


class PlanningEvent(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_planning_events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tasker_planning_actions")
    action = models.CharField(max_length=64)
    object_type = models.CharField(max_length=32)
    object_id = models.PositiveBigIntegerField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("owner", "created_at"), name="tasker_plan_event_owner_idx")]

    def __str__(self):
        return self.action


def validate_span(start, end, *, maximum_days):
    if not isinstance(start, datetime) or not isinstance(end, datetime) or timezone.is_naive(start) or timezone.is_naive(end):
        raise ValidationError("Choose timezone-aware start and end times.")
    seconds = (end.astimezone(datetime_timezone.utc) - start.astimezone(datetime_timezone.utc)).total_seconds()
    if seconds <= 0 or seconds > maximum_days * 86400:
        raise ValidationError(f"Choose a positive interval of at most {maximum_days} day(s).")
