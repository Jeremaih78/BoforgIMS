"""Prerequisites are separate from task hierarchy and preserve deleted evidence."""
import uuid

from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q

from .models import AuditedSoftDeleteModel


class TaskDependency(AuditedSoftDeleteModel):
    task = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, related_name="prerequisite_links")
    prerequisite = models.ForeignKey("tasker.Task", on_delete=models.PROTECT, related_name="dependent_links")

    class Meta:
        ordering = ("pk",)
        constraints = [
            models.UniqueConstraint(fields=("task", "prerequisite"), condition=Q(is_deleted=False), name="tasker_dependency_live_uniq"),
            models.CheckConstraint(condition=~Q(task=F("prerequisite")), name="tasker_dependency_no_self"),
        ]
        indexes = [models.Index(fields=("task", "is_deleted"), name="tasker_dependency_task_idx"),
                   models.Index(fields=("prerequisite", "is_deleted"), name="tasker_dependency_pre_idx")]

    def clean(self):
        super().clean()
        if self.task_id and self.task_id == self.prerequisite_id:
            raise ValidationError("A task cannot require itself.")

    def __str__(self):
        # Avoid resolving private FK titles in generic admin/select widgets.
        return f"Prerequisite link {self.pk or 'new'}"


class DependencyGraphState(models.Model):
    """Opaque coordination only; one revision per company/team or personal scope."""
    scope_key = models.CharField(primary_key=True, max_length=80)
    revision = models.UUIDField(default=uuid.uuid4, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return "Dependency graph revision"
