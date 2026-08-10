from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class TaskSnapshot:
    id: int
    title: str
    description: str
    priority: str
    status: str
    progress: int
    due_date: date | None
    due_time: str | None
    estimated_minutes: int | None
    category: str | None
    carry_forward: bool


@dataclass(frozen=True)
class EmployeeDayContext:
    employee_id: int
    employee_name: str
    work_date: date
    generated_at: datetime
    timezone: str
    focus_area: str
    tasks: tuple[TaskSnapshot, ...]
    top_priority_ids: tuple[int, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIRecommendation:
    task_id: int | None
    title: str
    rationale: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AIPlanResult:
    summary: str
    recommendations: tuple[AIRecommendation, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskerAIProvider(Protocol):
    """Contract future provider adapters must implement outside views/models."""

    provider_name: str

    def plan_day(self, context: EmployeeDayContext) -> AIPlanResult: ...

    def suggest_next_task(self, context: EmployeeDayContext) -> AIRecommendation: ...

    def summarize_day(self, context: EmployeeDayContext) -> str: ...

    def answer(self, context: EmployeeDayContext, question: str) -> str: ...
