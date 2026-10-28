"""Private durable captures; explicit sharing never changes the retained source."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator, URLValidator
from django.db import models
from django.db.models import Q
from django.urls import reverse

from .models import AuditedSoftDeleteModel


def normalize_tags(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, (list, tuple)) or any(not isinstance(tag, str) for tag in value):
        raise ValidationError({"tags": "Enter a list of text tags."})
    result = []
    for tag in value:
        tag = " ".join(tag.strip().split()).casefold()
        if len(tag) > 40:
            raise ValidationError({"tags": "Each tag must be at most 40 characters."})
        if tag and tag not in result:
            result.append(tag)
    if len(result) > 12:
        raise ValidationError({"tags": "Use at most 12 tags."})
    return result


class CaptureItem(AuditedSoftDeleteModel):
    class Kind(models.TextChoices):
        TASK = "TASK", "Task"
        IDEA = "IDEA", "Idea"
        NOTE = "NOTE", "Note"
        LINK = "LINK", "Link"

    class Visibility(models.TextChoices):
        PRIVATE = "PRIVATE", "Private"
        TEAM = "TEAM", "Shared with team"

    class TriageState(models.TextChoices):
        INBOX = "INBOX", "Inbox"
        ARCHIVED = "ARCHIVED", "Archived"
        PROMOTED = "PROMOTED", "Promoted"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_captures")
    client_key = models.UUIDField(default=uuid.uuid4, editable=False)
    submission_fingerprint = models.CharField(max_length=64, blank=True, editable=False)
    kind = models.CharField(max_length=8, choices=Kind.choices, default=Kind.NOTE)
    title = models.CharField(max_length=255, blank=True)
    content = models.TextField(validators=[MaxLengthValidator(100000)])
    original_text = models.TextField(editable=False, validators=[MaxLengthValidator(100000)])
    url = models.CharField(max_length=2048, blank=True, validators=[URLValidator(schemes=["http", "https"])])
    tags = models.JSONField(default=list, blank=True)
    pinned = models.BooleanField(default=False)
    visibility = models.CharField(max_length=8, choices=Visibility.choices, default=Visibility.PRIVATE)
    shared_team = models.ForeignKey("tasker.Team", on_delete=models.PROTECT, null=True, blank=True, related_name="shared_captures")
    triage_state = models.CharField(max_length=10, choices=TriageState.choices, default=TriageState.INBOX)
    version = models.PositiveBigIntegerField(default=1, editable=False)
    legacy_plan = models.OneToOneField("tasker.DailyPlan", on_delete=models.PROTECT, null=True, blank=True, related_name="imported_capture", editable=False)
    promoted_task = models.OneToOneField("tasker.Task", on_delete=models.PROTECT, null=True, blank=True, related_name="source_capture", editable=False)
    promoted_initiative = models.OneToOneField("tasker.Initiative", on_delete=models.PROTECT, null=True, blank=True, related_name="source_capture", editable=False)

    class Meta:
        ordering = ["-pinned", "-updated_at", "-pk"]
        constraints = [
            models.UniqueConstraint(fields=["owner", "client_key"], name="tasker_capture_client_uniq"),
            models.CheckConstraint(condition=Q(version__gte=1), name="tasker_capture_version_gte1"),
            models.CheckConstraint(condition=Q(visibility="PRIVATE", shared_team__isnull=True) | Q(visibility="TEAM", shared_team__isnull=False), name="tasker_capture_visibility_team"),
            models.CheckConstraint(condition=Q(promoted_task__isnull=True) | Q(promoted_initiative__isnull=True), name="tasker_capture_one_promotion"),
            models.CheckConstraint(condition=~Q(triage_state="PROMOTED") | Q(promoted_task__isnull=False) | Q(promoted_initiative__isnull=False), name="tasker_capture_promoted_target"),
        ]
        indexes = [
            models.Index(fields=["owner", "is_deleted", "triage_state"], name="tasker_capture_owner_state_idx"),
            models.Index(fields=["shared_team", "visibility"], name="tasker_capture_team_vis_idx"),
        ]

    def __str__(self):
        return self.title or f"{self.get_kind_display()} {self.pk}"

    def get_absolute_url(self):
        return reverse("ims:tasker:capture_detail", kwargs={"pk": self.pk})

    def clean(self):
        super().clean()
        if not isinstance(self.content, str) or not self.content.strip():
            raise ValidationError({"content": "Write something to capture."})
        if self.kind == self.Kind.LINK and not self.url:
            raise ValidationError({"url": "Enter the HTTP or HTTPS link."})
        self.tags = normalize_tags(self.tags)
        if self.visibility == self.Visibility.PRIVATE and self.shared_team_id:
            raise ValidationError({"shared_team": "Private captures cannot have a shared team."})
        if self.visibility == self.Visibility.TEAM and not self.shared_team_id:
            raise ValidationError({"shared_team": "Choose a team before sharing."})
        if self.legacy_plan_id and self.legacy_plan.owner_id != self.owner_id:
            raise ValidationError({"legacy_plan": "Only your own scratchpad can be imported."})
        if self.pk:
            previous = type(self).all_objects.get(pk=self.pk)
            if any(getattr(previous, field) != getattr(self, field) for field in ("owner_id", "client_key", "submission_fingerprint", "original_text", "legacy_plan_id")):
                raise ValidationError("A capture keeps its original owner, text and source identity.")
            for field in ("promoted_task_id", "promoted_initiative_id"):
                if getattr(previous, field) and getattr(previous, field) != getattr(self, field):
                    raise ValidationError("Promotion provenance cannot be replaced.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)


class CaptureEvent(models.Model):
    """Content-free audit; its visibility follows the source owner in admin."""
    capture = models.ForeignKey(CaptureItem, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tasker_capture_events")
    action = models.CharField(max_length=40)
    version = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-pk"]

    def __str__(self):
        return f"Capture {self.capture_id}: {self.action}"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("Capture audit events are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("Capture audit events are immutable.")
