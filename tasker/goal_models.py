"""Company direction and explicitly accepted business evidence.

These models are imported by tasker.models after its common base classes.
Task completion and checklist progress never create measurement records.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from .models import AuditedSoftDeleteModel


def _finite(value, field):
    if value is not None:
        try:
            finite = Decimal(value).is_finite()
        except (InvalidOperation, TypeError, ValueError):
            finite = False
        if not finite:
            raise ValidationError({field: "Enter a finite number."})


class EvidenceIntegrityModel(AuditedSoftDeleteModel):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class Goal(EvidenceIntegrityModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draft — definitions pending"
        ACTIVE = "ACTIVE", "Active"
        CLOSED = "CLOSED", "Closed"

    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_goals")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="goals")
    title = models.CharField(max_length=255)
    reference = models.SlugField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="owned_tasker_goals")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    evidence_policy = models.TextField(blank=True, help_text="Define authoritative evidence and what counts. Blank means unknown.")

    class Meta:
        ordering = ["title", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__isnull=True) | Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")), name="tasker_goal_horizon_order"),
            models.UniqueConstraint(fields=["company", "reference"], condition=~Q(reference=""), name="tasker_goal_company_ref_uniq"),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.team_id and (self.team.company_id != self.company_id or self.team.is_deleted):
            raise ValidationError({"team": "Choose a live team from this company."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date must be on or after the start date."})
        if self.pk:
            previous = type(self).all_objects.filter(pk=self.pk).values("company_id", "team_id").first()
            if previous and (previous["company_id"] != self.company_id or previous["team_id"] != self.team_id):
                if self.key_results.exists() or self.initiatives.exists():
                    raise ValidationError("A goal with results or initiatives cannot change company/team. Create a new goal instead.")

    def get_absolute_url(self):
        return reverse("ims:tasker:goal_detail", kwargs={"pk": self.pk})


class KeyResult(EvidenceIntegrityModel):
    class Direction(models.TextChoices):
        HIGHER = "HIGHER", "Higher is better"
        LOWER = "LOWER", "Lower is better"

    class Aggregation(models.TextChoices):
        SUM = "SUM", "Baseline plus accepted changes"
        LATEST = "LATEST", "Latest accepted absolute observation"

    goal = models.ForeignKey(Goal, on_delete=models.PROTECT, related_name="key_results")
    title = models.CharField(max_length=255)
    unit = models.CharField(max_length=60, blank=True, help_text="For example USD, orders, hours. Blank means unknown.")
    baseline = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    target = models.DecimalField(max_digits=20, decimal_places=4, null=True, blank=True)
    direction = models.CharField(max_length=6, choices=Direction.choices, default=Direction.HIGHER)
    aggregation = models.CharField(max_length=6, choices=Aggregation.choices, default=Aggregation.SUM)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="owned_tasker_results")
    start_date = models.DateField(null=True, blank=True)
    end_date = models.DateField(null=True, blank=True)
    evidence_policy = models.TextField(blank=True)

    class Meta:
        ordering = ["title", "pk"]
        constraints = [
            models.CheckConstraint(condition=Q(start_date__isnull=True) | Q(end_date__isnull=True) | Q(end_date__gte=models.F("start_date")), name="tasker_kr_horizon_order"),
            models.CheckConstraint(condition=Q(baseline__isnull=True) | Q(target__isnull=True) | Q(direction="HIGHER", target__gt=models.F("baseline")) | Q(direction="LOWER", target__lt=models.F("baseline")), name="tasker_kr_target_direction"),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        _finite(self.baseline, "baseline")
        _finite(self.target, "target")
        if self.baseline is not None and self.target is not None:
            if self.direction == self.Direction.HIGHER and self.target <= self.baseline:
                raise ValidationError({"target": "A higher-is-better target must exceed the baseline."})
            if self.direction == self.Direction.LOWER and self.target >= self.baseline:
                raise ValidationError({"target": "A lower-is-better target must be below the baseline."})
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The end date must be on or after the start date."})
        if self.goal_id and self.goal.is_deleted:
            raise ValidationError({"goal": "Choose a live goal."})
        if self.pk and self.outcomes.filter(accepted_at__isnull=False).exists():
            previous = type(self).all_objects.get(pk=self.pk)
            if any(getattr(previous, field) != getattr(self, field) for field in ("goal_id", "unit", "baseline", "target", "direction", "aggregation")):
                raise ValidationError("Accepted evidence fixes the metric definition. Create a new result for a changed measurement policy.")


class Initiative(EvidenceIntegrityModel):
    goal = models.ForeignKey(Goal, on_delete=models.PROTECT, related_name="initiatives")
    key_result = models.ForeignKey(KeyResult, on_delete=models.PROTECT, null=True, blank=True, related_name="initiatives")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="owned_tasker_initiatives")

    class Meta:
        ordering = ["title", "pk"]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.key_result_id and (self.key_result.goal_id != self.goal_id or self.key_result.is_deleted):
            raise ValidationError({"key_result": "Choose a live measurable result from this goal."})
        if self.goal_id and self.goal.is_deleted:
            raise ValidationError({"goal": "Choose a live goal."})
        if self.pk and self.task_contributions.exists():
            previous = type(self).all_objects.get(pk=self.pk)
            if previous.goal_id != self.goal_id or previous.key_result_id != self.key_result_id:
                raise ValidationError("An initiative linked to tasks cannot change its goal/result. Create a new initiative instead.")


class TaskContribution(EvidenceIntegrityModel):
    task = models.OneToOneField("tasker.Task", on_delete=models.PROTECT, related_name="primary_contribution")
    initiative = models.ForeignKey(Initiative, on_delete=models.PROTECT, related_name="task_contributions")

    class Meta:
        ordering = ["task_id"]

    def __str__(self):
        return f"Task {self.task_id} → {self.initiative}"

    def clean(self):
        super().clean()
        if self.task_id and self.initiative_id:
            goal = self.initiative.goal
            if self.task.company_id != goal.company_id or (goal.team_id and self.task.team_id != goal.team_id):
                raise ValidationError({"initiative": "The task and initiative must have matching company and team scope."})
            if self.task.is_deleted or self.initiative.is_deleted or goal.is_deleted:
                raise ValidationError("Only live tasks and initiatives can be linked.")
        if self.pk:
            previous = type(self).all_objects.get(pk=self.pk)
            if previous.initiative_id != self.initiative_id and OutcomeMeasurement.all_objects.filter(task_id=self.task_id, accepted_at__isnull=False).exists():
                raise ValidationError("A task with accepted evidence keeps its primary contribution. Create a new task for another contribution.")


class OutcomeMeasurement(EvidenceIntegrityModel):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Awaiting acceptance"
        ACCEPTED = "ACCEPTED", "Accepted"
        RETRACTED = "RETRACTED", "Retracted"

    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT, related_name="tasker_outcomes")
    team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="outcomes")
    key_result = models.ForeignKey(KeyResult, on_delete=models.PROTECT, related_name="outcomes")
    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="outcomes")
    employee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_outcomes")
    occurred_on = models.DateField()
    value = models.DecimalField(max_digits=20, decimal_places=4)
    evidence = models.TextField(help_text="Record an authoritative reference and the accepted output definition; exclude private notes.")
    source_type = models.SlugField(max_length=50, default="manual")
    source_key = models.CharField(max_length=150, help_text="Stable business occurrence ID; reuse on retries, never for a second output.")
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="accepted_tasker_outcomes")
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="retracted_tasker_outcomes")
    retraction_reason = models.CharField(max_length=500, blank=True)
    acceptance_history = models.JSONField(default=list, blank=True, editable=False)

    class Meta:
        ordering = ["-occurred_on", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["company", "source_type", "source_key"], name="tasker_outcome_source_uniq"),
            models.CheckConstraint(condition=~Q(status="ACCEPTED") | Q(accepted_at__isnull=False, accepted_by__isnull=False), name="tasker_outcome_accept_audit"),
            models.CheckConstraint(condition=~Q(status="RETRACTED") | Q(retracted_at__isnull=False, retraction_reason__gt=""), name="tasker_outcome_retract_audit"),
        ]
        indexes = [models.Index(fields=["key_result", "status", "occurred_on"], name="tasker_outcome_result_idx")]

    def __str__(self):
        return f"{self.key_result}: {self.value} ({self.get_status_display()})"

    def clean(self):
        super().clean()
        _finite(self.value, "value")
        if self.occurred_on and self.occurred_on > timezone.localdate():
            raise ValidationError({"occurred_on": "Future work cannot count as an actual business outcome."})
        if not self.evidence.strip() or not self.source_key.strip():
            raise ValidationError("Evidence and a stable source occurrence ID are required.")
        if not self.key_result_id:
            return
        goal = self.key_result.goal
        if goal.company_id != self.company_id or (goal.team_id and goal.team_id != self.team_id):
            raise ValidationError({"key_result": "The measurable result must match this outcome's company and team."})
        if self.team_id and (self.team.company_id != self.company_id or self.team.is_deleted):
            raise ValidationError({"team": "Choose a live team in this company."})
        if self.task_id:
            task = self.task
            if task.company_id != self.company_id or task.team_id != self.team_id or task.owner_id != self.employee_id:
                raise ValidationError({"task": "The task must match the outcome's employee, company and team."})
            contribution = TaskContribution.objects.filter(task_id=self.task_id, initiative__is_deleted=False, initiative__key_result_id=self.key_result_id, initiative__goal_id=goal.pk).exists()
            if not contribution:
                raise ValidationError({"task": "Link the task's primary initiative to this measurable result first."})

    def save(self, *args, **kwargs):
        previous = type(self).all_objects.filter(pk=self.pk).first() if self.pk else None
        if previous and previous.accepted_at:
            immutable = ("company_id", "team_id", "key_result_id", "task_id", "employee_id", "occurred_on", "value", "evidence", "source_type", "source_key")
            if any(getattr(previous, field) != getattr(self, field) for field in immutable):
                raise ValidationError("Accepted evidence cannot be edited. Retract it and record a corrected source occurrence.")
        if self.status != (previous.status if previous else self.Status.DRAFT) and not getattr(self, "_acceptance_service", False):
            raise ValidationError("Use the explicit acceptance/retraction service to change evidence status.")
        return super().save(*args, **kwargs)
