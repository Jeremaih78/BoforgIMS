"""Explicit responsibility for a global stock pool; no purchase or stock posting."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ReplenishmentPlan(models.Model):
    class ReviewState(models.TextChoices):
        NEEDS_REVIEW = "NEEDS_REVIEW", "Needs review"
        REVIEWING = "REVIEWING", "Reviewing replenishment"
        WAITING_DECISION = "WAITING_DECISION", "Waiting for a procurement decision"
        AWAITING_STOCK = "AWAITING_STOCK", "Following up expected stock"

    class NeedState(models.TextChoices):
        UNKNOWN = "UNKNOWN", "Not yet observed"
        LOW = "LOW", "Replenishment review needed"
        CLEAR = "CLEAR", "Replenishment need cleared"
        INVALID = "INVALID", "Stock records need review"
        INACTIVE = "INACTIVE", "Product is not actively stock tracked"

    product = models.OneToOneField("inventory.Product", on_delete=models.PROTECT,
        related_name="replenishment_plan")
    company = models.ForeignKey("accounting.Company", on_delete=models.PROTECT,
        related_name="replenishment_plans")
    assigned_to = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="replenishment_plans")
    is_enabled = models.BooleanField(default=False)
    review_state = models.CharField(max_length=20, choices=ReviewState.choices,
        default=ReviewState.NEEDS_REVIEW)
    next_review_on = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, help_text="Standing procurement notes, visible only at the inventory source.")
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    observed_need_state = models.CharField(max_length=10, choices=NeedState.choices,
        default=NeedState.UNKNOWN, editable=False)
    cycle = models.UUIDField(default=uuid.uuid4, editable=False)
    last_observed_at = models.DateTimeField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, editable=False, related_name="created_replenishment_plans")
    created_at = models.DateTimeField(default=timezone.now, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("product__name", "pk")
        permissions = [("manage_replenishment", "Can configure procurement responsibility in assigned companies")]
        constraints = [
            models.CheckConstraint(condition=Q(review_state__in=("NEEDS_REVIEW", "REVIEWING", "WAITING_DECISION", "AWAITING_STOCK")),
                name="inventory_replenish_review_state"),
            models.CheckConstraint(condition=Q(observed_need_state__in=("UNKNOWN", "LOW", "CLEAR", "INVALID", "INACTIVE")),
                name="inventory_replenish_need_state"),
        ]
        indexes = [models.Index(fields=("company", "assigned_to", "is_enabled"), name="inventory_replenish_owner_idx")]

    def clean(self):
        super().clean()
        if self.pk:
            previous = type(self).objects.filter(pk=self.pk).only("product_id", "company_id").first()
            if previous:
                if previous.product_id != self.product_id:
                    raise ValidationError({"product": "A procurement responsibility keeps its original global product."})
                if previous.company_id != self.company_id and not getattr(self, "_allow_company_move", False):
                    raise ValidationError({"company": "Use the authorized responsibility service to change company mapping."})

    def save(self, *args, **kwargs):
        try:
            self.full_clean()
            self.revision = uuid.uuid4()
            if kwargs.get("update_fields") is not None:
                kwargs["update_fields"] = set(kwargs["update_fields"]) | {"revision", "updated_at"}
            return super().save(*args, **kwargs)
        finally:
            if hasattr(self, "_allow_company_move"):
                del self._allow_company_move

    def __str__(self):
        return f"Procurement responsibility {self.pk or 'new'}"


class ReplenishmentEvent(models.Model):
    """Retained source decisions/observations, never accepted financial output."""
    plan = models.ForeignKey(ReplenishmentPlan, on_delete=models.PROTECT, related_name="events")
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT,
        null=True, blank=True, related_name="replenishment_events")
    client_key = models.UUIDField(null=True, blank=True)
    action = models.CharField(max_length=24)
    request_digest = models.CharField(max_length=64, blank=True)
    previous_revision = models.UUIDField(null=True, blank=True)
    result_revision = models.UUIDField()
    cycle = models.UUIDField()
    details = models.JSONField(default=dict)
    created_at = models.DateTimeField(default=timezone.now, editable=False)

    class Meta:
        ordering = ("-created_at", "-pk")
        constraints = [models.UniqueConstraint(fields=("actor", "client_key"),
            condition=Q(client_key__isnull=False), name="inventory_replenish_action_once")]

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValidationError("Procurement source evidence is immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("Procurement source evidence is retained.")
