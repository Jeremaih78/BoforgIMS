from __future__ import annotations

import re
import string

from tasker.models import AIConfiguration, PromptTemplate

from .exceptions import AIPromptError
from .types import RenderedPrompt


VARIABLE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PromptManager:
    """Resolve and strictly render versioned database prompt templates."""

    def get_template(self, key: str) -> PromptTemplate:
        try:
            return PromptTemplate.objects.get(key=key, is_active=True)
        except PromptTemplate.DoesNotExist as exc:
            raise AIPromptError(f"No active prompt template exists for '{key}'.") from exc

    def render(self, *, key: str, variables: dict, configuration: AIConfiguration) -> RenderedPrompt:
        template = self.get_template(key)
        declared = set(template.variables)
        invalid = declared - {name for name in declared if VARIABLE_NAME.fullmatch(name)}
        if invalid:
            raise AIPromptError("The prompt template declares invalid variable names.")
        missing = declared - set(variables)
        if missing:
            raise AIPromptError(f"Missing prompt variables: {', '.join(sorted(missing))}.")
        safe_values = {name: str(variables[name]) for name in declared}
        system = self._format(template.system_template, safe_values)
        user = self._format(template.user_template, safe_values)
        if configuration.system_prompt_override.strip():
            system = configuration.system_prompt_override.strip()
        return RenderedPrompt(
            template_id=template.pk,
            key=template.key,
            version=template.version,
            system=system,
            user=user,
            output_schema=template.output_schema,
        )

    @staticmethod
    def _format(value: str, variables: dict[str, str]) -> str:
        try:
            fields = {
                field_name
                for _, field_name, _, _ in string.Formatter().parse(value)
                if field_name
            }
            if fields - set(variables):
                raise KeyError(next(iter(fields - set(variables))))
            return value.format_map(variables).strip()
        except (KeyError, ValueError) as exc:
            raise AIPromptError("The prompt template could not be rendered safely.") from exc

