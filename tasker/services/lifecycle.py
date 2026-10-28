"""Shared field policy for explicit task acceptance and reopening.

Checklist completion describes steps, not acceptance. Explicit Complete/status
actions and submitting 100 to the progress action accept the task. Checklist
edits do not reopen accepted work. Non-executable states close focus timers.
"""

from django.utils import timezone

from tasker.models import Task


def require_completion_acceptance(task, *, accepted=False):
    """A legacy blank outcome stays unknown; an explicit definition is reviewed."""
    if task.status != Task.Status.COMPLETED and task.definition_of_done.strip() and accepted is not True:
        raise ValueError("Review the definition of done and explicitly confirm acceptance before completing this task.")


def prepare_task_state(task, *, previous_status=None):
    if task.status == Task.Status.IN_PROGRESS and not task.started_at:
        task.started_at = timezone.now()
    if task.status == Task.Status.COMPLETED:
        task.progress = 100
        if previous_status != Task.Status.COMPLETED or not task.completed_at:
            task.completed_at = timezone.now()
    else:
        task.completed_at = None
    if task.status != Task.Status.BLOCKED:
        task.blocked_reason = ""


def requires_timer_cleanup(task):
    return task.archived or task.is_deleted or task.status in (
        Task.Status.BLOCKED, Task.Status.COMPLETED, Task.Status.CANCELLED,
    )


def save_without_task_activity(task, *, update_fields):
    """Suppress a derived-field save without suppressing the next real action."""
    previous = getattr(task, "_skip_tasker_activity", False)
    task._skip_tasker_activity = True
    try:
        task.save(update_fields=update_fields)
    finally:
        task._skip_tasker_activity = previous
