from dataclasses import dataclass, field

from django.conf import settings


@dataclass(frozen=True)
class AIEnvironmentSettings:
    api_key: str = field(repr=False)
    organization: str
    project: str
    max_prompt_characters: int

    @classmethod
    def load(cls):
        return cls(
            api_key=getattr(settings, "TASKER_AI_OPENAI_API_KEY", ""),
            organization=getattr(settings, "TASKER_AI_OPENAI_ORGANIZATION", ""),
            project=getattr(settings, "TASKER_AI_OPENAI_PROJECT", ""),
            max_prompt_characters=getattr(settings, "TASKER_AI_MAX_PROMPT_CHARACTERS", 50000),
        )
