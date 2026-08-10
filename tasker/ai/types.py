from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol


@dataclass(frozen=True)
class RenderedPrompt:
    template_id: int
    key: str
    version: int
    system: str
    user: str
    output_schema: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True)
class ProviderResult:
    text: str
    response_id: str = ""
    request_id: str = ""
    usage: TokenUsage = field(default_factory=TokenUsage)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderStreamEvent:
    delta: str = ""
    result: ProviderResult | None = None


@dataclass(frozen=True)
class AIResult:
    text: str
    conversation_id: int
    message_id: int
    usage_log_id: int
    usage: TokenUsage


@dataclass(frozen=True)
class AIStreamChunk:
    delta: str
    is_final: bool = False
    result: AIResult | None = None


class AIProvider(Protocol):
    provider_name: str

    def generate(self, **kwargs) -> ProviderResult: ...

    def stream(self, **kwargs) -> Iterator[ProviderStreamEvent]: ...

