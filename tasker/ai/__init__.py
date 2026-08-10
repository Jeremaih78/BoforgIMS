"""Audited, provider-neutral AI foundation for Boforg AI Tasker."""

from .contracts import (
    AIPlanResult,
    AIRecommendation,
    EmployeeDayContext,
    TaskSnapshot,
    TaskerAIProvider,
)
from .gateway import AIUnavailableError, TaskerAIGateway
from .service import AIService
from .streaming import streaming_http_response

__all__ = (
    "AIPlanResult",
    "AIRecommendation",
    "EmployeeDayContext",
    "TaskSnapshot",
    "TaskerAIProvider",
    "AIUnavailableError",
    "TaskerAIGateway",
    "AIService",
    "streaming_http_response",
)
