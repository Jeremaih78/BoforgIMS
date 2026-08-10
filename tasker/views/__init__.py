"""Public Tasker view API retained for stable URL configuration."""

from .dashboard import index
from .ai_usage import ai_usage_dashboard
from .assistant import assistant_apply_steps, assistant_day_action, assistant_home, assistant_task_action
from .focus import (
    focus_block,
    focus_complete,
    focus_checklist_toggle,
    focus_mode,
    focus_notes,
    focus_pause,
    focus_resume,
    focus_start,
    focus_stop,
)
from .planner import daily_plan, daily_review
from .tasks import (
    checklist_add,
    checklist_toggle,
    comment_add,
    progress_update,
    task_create,
    task_delete,
    task_detail,
    task_edit,
    task_list,
    time_add,
)

__all__ = (
    "index",
    "ai_usage_dashboard",
    "assistant_home",
    "assistant_day_action",
    "assistant_task_action",
    "assistant_apply_steps",
    "daily_plan",
    "daily_review",
    "task_list",
    "task_detail",
    "task_create",
    "task_edit",
    "task_delete",
    "comment_add",
    "checklist_add",
    "checklist_toggle",
    "time_add",
    "progress_update",
    "focus_mode",
    "focus_start",
    "focus_pause",
    "focus_resume",
    "focus_stop",
    "focus_block",
    "focus_complete",
    "focus_notes",
    "focus_checklist_toggle",
)
