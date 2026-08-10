from __future__ import annotations

from .contracts import EmployeeDayContext, TaskerAIProvider


class AIUnavailableError(RuntimeError):
    pass


class TaskerAIGateway:
    """Compatibility boundary retained for the Phase 1 typed productivity contracts."""

    def __init__(self, *, provider: TaskerAIProvider | None = None, enabled: bool = False):
        self._provider = provider
        self._enabled = enabled

    @property
    def is_available(self):
        return self._enabled and self._provider is not None

    def plan_day(self, context: EmployeeDayContext):
        return self._require_provider().plan_day(context)

    def suggest_next_task(self, context: EmployeeDayContext):
        return self._require_provider().suggest_next_task(context)

    def summarize_day(self, context: EmployeeDayContext):
        return self._require_provider().summarize_day(context)

    def answer(self, context: EmployeeDayContext, question: str):
        return self._require_provider().answer(context, question)

    def _require_provider(self):
        if not self.is_available:
            raise AIUnavailableError("No productivity AI provider is enabled for this feature.")
        return self._provider
