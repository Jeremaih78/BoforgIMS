"""Owner-private AI previews and content-free records of reviewed changes."""
import uuid
import re

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from .models import AuditedSoftDeleteModel


class AIProposal(AuditedSoftDeleteModel):
    class State(models.TextChoices):
        PENDING = "PENDING", "Ready for review"
        APPLIED = "APPLIED", "Applied"
        UNDONE = "UNDONE", "Undone"
        CANCELLED = "CANCELLED", "Cancelled"
        EXPIRED = "EXPIRED", "Expired"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="tasker_ai_proposals")
    request = models.OneToOneField("tasker.AIWorkRequest", on_delete=models.PROTECT, related_name="proposal")
    feature = models.CharField(max_length=32)
    scope = models.CharField(max_length=12, default="PERSONAL")
    state = models.CharField(max_length=12, choices=State.choices, default=State.PENDING)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    payload = models.JSONField(default=dict)
    payload_digest = models.CharField(max_length=64)
    context_digest = models.CharField(max_length=64)
    source_proofs = models.JSONField(default=dict)
    expires_at = models.DateTimeField()
    applied_at = models.DateTimeField(null=True, blank=True)
    undone_at = models.DateTimeField(null=True, blank=True)
    selected_action_ids = models.JSONField(default=list, blank=True)
    apply_receipt = models.JSONField(default=dict, blank=True)
    before_state = models.JSONField(default=dict, blank=True)
    after_state = models.JSONField(default=dict, blank=True)
    content_redacted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-pk")
        indexes = [models.Index(fields=("owner", "state", "expires_at"), name="tasker_ai_prop_owner_idx")]
        constraints = [
            models.CheckConstraint(condition=Q(state__in=("PENDING", "APPLIED", "UNDONE", "CANCELLED", "EXPIRED")), name="tasker_ai_prop_state"),
            models.CheckConstraint(condition=Q(scope__in=("PERSONAL", "MANAGER")), name="tasker_ai_prop_scope"),
        ]

    def clean(self):
        super().clean()
        if self.pk:
            previous = type(self).all_objects.filter(pk=self.pk).first()
            if previous and any(getattr(previous, field) != getattr(self, field) for field in (
                "owner_id", "request_id", "feature", "scope", "payload", "payload_digest", "context_digest", "source_proofs", "expires_at",
            )):
                raise ValidationError("A reviewed AI proposal keeps its original owner, content and source evidence.")
            if previous:
                transitions = {self.State.PENDING: {self.State.PENDING, self.State.APPLIED, self.State.CANCELLED, self.State.EXPIRED},
                               self.State.APPLIED: {self.State.APPLIED, self.State.UNDONE}}
                if self.state not in transitions.get(previous.state, {previous.state}):
                    raise ValidationError("An AI proposal cannot reopen a consumed review.")
                if self.state != previous.state and self.revision == previous.revision:
                    raise ValidationError("A reviewed state change needs a new revision.")
                if previous.state in (self.State.APPLIED, self.State.UNDONE) and any(
                        getattr(previous, field) != getattr(self, field)
                        for field in ("before_state", "after_state", "selected_action_ids", "applied_at")):
                    raise ValidationError("The original application receipt and source checkpoints are immutable.")
                if previous.state in (self.State.APPLIED, self.State.UNDONE):
                    allowed_receipt = dict(previous.apply_receipt)
                    if previous.state == self.State.APPLIED and self.state == self.State.UNDONE:
                        allowed_receipt["undo_review_revision"] = str(previous.revision)
                    if self.apply_receipt != allowed_receipt:
                        raise ValidationError("The original application receipt is immutable.")

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return f"AI proposal {self.pk}: {self.get_state_display()}"


class AIProposalEvent(models.Model):
    proposal = models.ForeignKey(AIProposal, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="tasker_ai_proposal_events")
    action = models.CharField(max_length=32)
    revision = models.UUIDField()
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-pk")

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValueError("AI proposal audit events are immutable.")
        if not isinstance(self.details, dict) or set(self.details) - {"action_ids", "reason_code"}:
            raise ValidationError("Proposal audit details contain identifiers and fixed reason codes only.")
        if self.action not in ("created", "applied", "undone", "cancelled", "expired", "redacted"):
            raise ValidationError("Choose a recognized proposal audit event.")
        ids = self.details.get("action_ids", [])
        if (type(ids) is not list or len(ids) > 20
                or any(type(value) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", value) for value in ids)
                or len(set(ids)) != len(ids)):
            raise ValidationError("Proposal audit details contain only displayed action identifiers.")
        if "reason_code" in self.details and self.details["reason_code"] not in ("owner_content_redaction", "owner_cancelled", "review_expired"):
            raise ValidationError("Proposal audit reasons use fixed content-free codes.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AI proposal audit events are immutable.")
