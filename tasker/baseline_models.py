"""Declared, evidence-backed baseline records; never activity surveillance."""
from __future__ import annotations

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import models
from django.db.models import Q

from .models import AuditedSoftDeleteModel


class BaselineStudy(AuditedSoftDeleteModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        COLLECTING = "COLLECTING", "Collecting baseline"
        CLOSED = "CLOSED", "Closed"

    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_baseline_studies")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="baseline_studies")
    name = models.CharField(max_length=180)
    role = models.CharField(max_length=120, blank=True)
    workload_type = models.CharField(max_length=120, blank=True)
    output_unit = models.CharField(max_length=80, blank=True)
    output_definition = models.TextField(blank=True)
    acceptance_criteria = models.TextField(blank=True)
    effort_definition = models.TextField(blank=True)
    quality_definition = models.TextField(blank=True)
    source_definition = models.TextField(blank=True)
    comparison_plan = models.TextField(blank=True)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    pilot_start_date = models.DateField(null=True, blank=True)
    collection_started_at = models.DateTimeField(null=True, blank=True, editable=False)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__isnull=True) | Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")), name="tasker_baseline_dates_order"),
            models.CheckConstraint(condition=Q(pilot_start_date__isnull=True) | Q(end_date__isnull=True) | Q(pilot_start_date__gt=models.F("end_date")), name="tasker_baseline_before_pilot"),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        if self.team_id and self.company_id and self.team.company_id != self.company_id:
            errors["team"] = "The team must belong to this company."
        if self.start_date and self.end_date and self.end_date < self.start_date:
            errors["end_date"] = "The baseline must end on or after its start date."
        if self.pilot_start_date and self.end_date and self.pilot_start_date <= self.end_date:
            errors["pilot_start_date"] = "The baseline must finish before employee rollout starts."
        if self.collection_started_at:
            from django.utils import timezone
            if self.pilot_start_date and self.pilot_start_date <= timezone.localdate(self.collection_started_at):
                errors["pilot_start_date"] = "Pilot rollout must start after baseline collection actually began."
            if self.status == self.Status.DRAFT:
                errors["status"] = "A study that started collection cannot return to draft."
        if self.pk:
            stored_start = type(self).all_objects.filter(pk=self.pk).values_list("collection_started_at", flat=True).first()
            if stored_start and stored_start != self.collection_started_at:
                errors["collection_started_at"] = "The server-recorded start of collection cannot be changed."
        if self.status != self.Status.DRAFT:
            for name in ("role", "workload_type", "output_unit", "output_definition", "acceptance_criteria", "effort_definition", "quality_definition", "source_definition", "comparison_plan", "start_date", "end_date"):
                if not getattr(self, name):
                    errors[name] = "Define this before starting baseline collection."
        if self.pk and BaselineObservation.all_objects.filter(study_id=self.pk).exists():
            previous = type(self).all_objects.get(pk=self.pk)
            for name in ("company_id", "team_id", "role", "workload_type", "output_unit", "output_definition", "acceptance_criteria", "effort_definition", "quality_definition", "source_definition", "comparison_plan", "start_date", "end_date"):
                if getattr(previous, name) != getattr(self, name):
                    errors[name.removesuffix("_id")] = "Create a new study to change a baseline definition after observations exist."
            if self.status == self.Status.DRAFT and previous.status != self.Status.DRAFT:
                errors["status"] = "A study with collected evidence cannot return to draft."
        if errors:
            raise ValidationError(errors)


class BaselineObservation(AuditedSoftDeleteModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft"
        ACCEPTED = "ACCEPTED", "Accepted"
        RETRACTED = "RETRACTED", "Retracted"

    study = models.ForeignKey(BaselineStudy, on_delete=models.PROTECT, related_name="observations")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_baseline_observations")
    observed_on = models.DateField()
    role = models.CharField(max_length=120, blank=True)
    workload_type = models.CharField(max_length=120, blank=True)
    staffed_hours = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(24)])
    accepted_output = models.DecimalField(max_digits=16, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0)])
    quality_passed = models.PositiveIntegerField(null=True, blank=True)
    rework_count = models.PositiveIntegerField(null=True, blank=True)
    evidence = models.TextField(blank=True)
    source_key = models.CharField(max_length=180)
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.DRAFT)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="tasker_baseline_accepted")
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="tasker_baseline_retracted")
    retraction_reason = models.CharField(max_length=500, blank=True)
    acceptance_history = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-observed_on", "employee_id"]
        constraints = [
            models.UniqueConstraint(fields=["study", "source_key"], name="tasker_baseline_source_uniq"),
            models.UniqueConstraint(fields=["study", "employee", "observed_on", "workload_type"], condition=~Q(status="RETRACTED"), name="tasker_baseline_workload_uniq"),
            models.CheckConstraint(condition=Q(staffed_hours__isnull=True) | Q(staffed_hours__gte=0, staffed_hours__lte=24), name="tasker_baseline_hours_range"),
            models.CheckConstraint(condition=Q(accepted_output__isnull=True) | Q(accepted_output__gte=0), name="tasker_baseline_output_pos"),
            models.CheckConstraint(condition=~Q(status="ACCEPTED") | Q(accepted_at__isnull=False, accepted_by__isnull=False), name="tasker_baseline_accept_audit"),
        ]
        indexes = [models.Index(fields=["study", "status", "observed_on"], name="tasker_baseline_study_idx")]

    def __str__(self):
        return f"{self.employee} · {self.observed_on} · {self.study}"

    def clean(self):
        super().clean()
        errors = {}
        if not self.study_id:
            return
        study = self.study
        # ModelForms intentionally do not expose these snapshots as editable.
        # Fill them before model validation, then enforce a single workload.
        if not self.role:
            self.role = study.role
        if not self.workload_type:
            self.workload_type = study.workload_type
        if study.is_deleted:
            errors["study"] = "The baseline study is unavailable."
        if self.observed_on and study.start_date and self.observed_on < study.start_date:
            errors["observed_on"] = "This date is before the baseline window."
        if self.observed_on and study.end_date and self.observed_on > study.end_date:
            errors["observed_on"] = "This date is after the baseline window."
        if self.observed_on and study.pilot_start_date and self.observed_on >= study.pilot_start_date:
            errors["observed_on"] = "Rollout observations cannot be labelled as pre-rollout baseline."
        from django.utils import timezone
        if self.observed_on and self.observed_on > timezone.localdate():
            errors["observed_on"] = "Future work cannot be recorded as observed evidence."
        if self.role != study.role:
            errors["role"] = "Use the study's defined role; create another study for a different role."
        if self.workload_type != study.workload_type:
            errors["workload_type"] = "Use the study's workload type; different workload mixes need separate studies."
        for name in ("staffed_hours", "accepted_output"):
            value = getattr(self, name)
            if value is not None and (not Decimal(value).is_finite() or value < 0):
                errors[name] = "Enter a finite non-negative measurement or leave it unknown."
        if self.status == self.Status.ACCEPTED and not self.evidence.strip():
            errors["evidence"] = "Evidence is required before accepting this observation."
        if errors:
            raise ValidationError(errors)

    def delete(self, *args, **kwargs):
        if self.status == self.Status.ACCEPTED:
            raise ValidationError("Retract accepted baseline evidence with a reason before removing it.")
        return super().delete(*args, **kwargs)
