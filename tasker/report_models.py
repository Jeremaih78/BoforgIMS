"""Factual end-day evidence. Reflection text and provider content stay elsewhere."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils import timezone

from .planning_models import validate_zone


class ReportPolicy(models.Model):
    class Consolidation(models.TextChoices):
        UNCONFIGURED = "UNCONFIGURED", "Not agreed"
        PER_SUBMISSION = "PER_SUBMISSION", "New team revision after each End day"
        AT_CUTOFF = "AT_CUTOFF", "Team report after the configured cutoff"

    class EmailMode(models.TextChoices):
        UNCONFIGURED = "UNCONFIGURED", "Not configured"
        TEST = "TEST", "Local test adapter only"

    company = models.OneToOneField("accounting.Company", on_delete=models.PROTECT, related_name="tasker_report_policy")
    configured = models.BooleanField(default=False)
    timezone_name = models.CharField(max_length=64, default="Africa/Harare", validators=[validate_zone])
    cutoff_time = models.TimeField(null=True, blank=True)
    consolidation = models.CharField(max_length=20, choices=Consolidation.choices, default=Consolidation.UNCONFIGURED)
    correction_policy = models.CharField(max_length=20, default="UNCONFIGURED", choices=(("UNCONFIGURED", "Not agreed"), ("APPEND_REVISION", "Append a factual revision")))
    missing_policy = models.CharField(max_length=20, default="UNCONFIGURED", choices=(("UNCONFIGURED", "Not agreed"), ("SHOW_MISSING", "Label missing submissions")))
    weekdays = models.JSONField(default=list, blank=True)
    email_mode = models.CharField(max_length=16, choices=EmailMode.choices, default=EmailMode.UNCONFIGURED)
    email_enabled = models.BooleanField(default=False, editable=False)
    retention_days = models.PositiveSmallIntegerField(null=True, blank=True, validators=[MinValueValidator(1), MaxValueValidator(3660)])
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="tasker_report_policy_changes")
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)
    reconcile_team_cursor = models.PositiveBigIntegerField(default=0, editable=False)

    def clean(self):
        super().clean()
        validate_zone(self.timezone_name)
        if not isinstance(self.weekdays, list) or len(self.weekdays) > 7 or any(type(day) is not int or day not in range(7) for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValidationError({"weekdays": "Choose distinct working weekdays."})
        if self.email_enabled:
            raise ValidationError("Live email is unavailable in Stage 8. Use the local test adapter.")
        if self.configured and (self.cutoff_time is None or not self.weekdays or self.consolidation == "UNCONFIGURED" or self.correction_policy != "APPEND_REVISION" or self.missing_policy != "SHOW_MISSING" or self.email_mode != "TEST"):
            raise ValidationError("Agree the cutoff, weekdays, consolidation, correction/missing policy and test adapter before configuring report delivery.")
        if self.pk and type(self).objects.filter(pk=self.pk).exclude(company_id=self.company_id).exists():
            raise ValidationError("A reporting policy keeps its original company.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return "End-day report delivery policy"


class ImmutableReportEvidence(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Report evidence is immutable. Append a new factual revision.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Report evidence is retained for audit; deletion requires a separately governed retention procedure.")


class EndDaySubmission(ImmutableReportEvidence):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_end_day_submissions")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, related_name="end_day_submissions")
    report_date = models.DateField()
    version = models.PositiveIntegerField()
    source_digest = models.CharField(max_length=64)
    facts = models.JSONField(default=dict)
    review = models.ForeignKey("tasker.DailyReview", on_delete=models.PROTECT, related_name="report_submissions")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    # Scheduler diagnostics may be updated without rewriting immutable facts.
    last_checked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ("-report_date", "-version", "-pk")
        constraints = [models.UniqueConstraint(fields=("owner", "team", "report_date", "version"), name="tasker_endday_revision_uniq")]
        indexes = [models.Index(fields=("team", "report_date", "owner"), name="tasker_endday_team_day_idx")]

    @property
    def submitted_at(self):
        return self.created_at

    def __str__(self):
        return f"End-day facts {self.report_date}, revision {self.version}"


class ManagerReportSnapshot(ImmutableReportEvidence):
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, related_name="manager_report_snapshots")
    report_date = models.DateField()
    version = models.PositiveIntegerField()
    source_digest = models.CharField(max_length=64)
    facts = models.JSONField(default=dict)
    policy_revision = models.CharField(max_length=64, default="unconfigured-v1")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    generated_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="generated_tasker_reports")

    class Meta:
        ordering = ("-report_date", "-version", "-pk")
        constraints = [models.UniqueConstraint(fields=("team", "report_date", "version"), name="tasker_team_report_revision_uniq")]
        indexes = [models.Index(fields=("team", "report_date", "created_at"), name="tasker_report_team_day_idx")]

    def __str__(self):
        return f"Team report {self.report_date}, revision {self.version}"
