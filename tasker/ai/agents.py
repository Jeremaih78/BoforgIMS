from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class AgentDefinition:
    key: str
    name: str
    prompt_key: str
    description: str = ""
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)
    requires_confirmation_for: tuple[str, ...] = field(default_factory=tuple)


class AgentRegistry:
    """Extension registry only; Milestone 1 does not execute tools or agents."""

    def __init__(self):
        self._agents: dict[str, AgentDefinition] = {}

    def register(self, agent: AgentDefinition):
        if agent.key in self._agents:
            raise ValueError(f"Agent '{agent.key}' is already registered.")
        self._agents[agent.key] = agent

    def get(self, key):
        return self._agents[key]

    def all(self):
        return tuple(self._agents.values())


agent_registry = AgentRegistry()

