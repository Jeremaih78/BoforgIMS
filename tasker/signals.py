from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from .models import Task, TaskActivity


TRACKED_FIELDS = (
    "title",
    "owner_id",
    "priority",
    "status",
    "estimated_duration",
    "actual_duration",
    "due_date",
    "due_time",
    "started_at",
    "completed_at",
    "progress",
    "carry_forward",
    "category_id",
    "parent_task_id",
    "archived",
    "blocked_reason",
    "is_deleted",
)


@receiver(pre_save, sender=Task)
def capture_task_changes(sender, instance, **kwargs):
    if not instance.pk:
        instance._tasker_changes = {}
        return
    previous = sender.all_objects.filter(pk=instance.pk).values(*TRACKED_FIELDS).first()
    if previous is None:
        instance._tasker_changes = {}
        return
    instance._tasker_changes = {
        field: {"from": _json_value(previous[field]), "to": _json_value(getattr(instance, field))}
        for field in TRACKED_FIELDS
        if previous[field] != getattr(instance, field)
    }


@receiver(post_save, sender=Task)
def record_task_activity(sender, instance, created, **kwargs):
    if getattr(instance, "_skip_tasker_activity", False):
        return
    changes = getattr(instance, "_tasker_changes", {})
    actor = instance.created_by if created else (instance.updated_by or instance.created_by)
    if created:
        event_type = TaskActivity.EventType.CREATED
        description = "Task created"
    elif not changes:
        return
    elif changes.get("is_deleted", {}).get("to") is True:
        event_type = TaskActivity.EventType.DELETED
        description = "Task moved to deleted items"
    elif changes.get("is_deleted", {}).get("to") is False:
        event_type = TaskActivity.EventType.RESTORED
        description = "Task restored"
    elif "status" in changes:
        event_type = TaskActivity.EventType.STATUS
        description = f"Status changed to {instance.get_status_display()}"
    elif set(changes) == {"progress"}:
        event_type = TaskActivity.EventType.PROGRESS
        description = f"Progress updated to {instance.progress}%"
    else:
        event_type = TaskActivity.EventType.UPDATED
        description = "Task details updated"
    TaskActivity.objects.create(
        task=instance,
        event_type=event_type,
        description=description,
        changes=changes,
        created_by=actor,
        updated_by=actor,
    )


def _json_value(value):
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return str(value)
    return value
