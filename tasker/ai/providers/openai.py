from __future__ import annotations

from collections.abc import Iterator

import openai
from openai import OpenAI

from tasker.models import AIConfiguration

from ..exceptions import AIConfigurationError, AIProviderError, AIStreamInterruptedError, AITimeoutError
from ..settings import AIEnvironmentSettings
from ..types import ProviderResult, ProviderStreamEvent, TokenUsage


class OpenAIProvider:
    provider_name = "OPENAI"

    def __init__(self, *, configuration: AIConfiguration, client=None, environment=None):
        self.configuration = configuration
        self.environment = environment or AIEnvironmentSettings.load()
        if client is None:
            if not self.environment.api_key:
                raise AIConfigurationError("OPENAI_API_KEY is not configured.")
            client = OpenAI(
                api_key=self.environment.api_key,
                organization=self.environment.organization or None,
                project=self.environment.project or None,
                timeout=float(configuration.timeout_seconds),
                max_retries=0,
            )
        self.client = client

    def generate(self, *, instructions, input_items, safety_identifier, output_schema=None):
        try:
            response = self.client.responses.create(
                **self._request_params(
                    instructions=instructions,
                    input_items=input_items,
                    safety_identifier=safety_identifier,
                    output_schema=output_schema,
                    stream=False,
                )
            )
        except Exception as exc:
            raise self._translate_error(exc) from exc
        return self._result(response)

    def stream(self, *, instructions, input_items, safety_identifier, output_schema=None) -> Iterator[ProviderStreamEvent]:
        stream = None
        completed = False
        try:
            stream = self.client.responses.create(
                **self._request_params(
                    instructions=instructions,
                    input_items=input_items,
                    safety_identifier=safety_identifier,
                    output_schema=output_schema,
                    stream=True,
                )
            )
            for event in stream:
                event_type = getattr(event, "type", "")
                if event_type == "response.output_text.delta":
                    yield ProviderStreamEvent(delta=getattr(event, "delta", ""))
                elif event_type == "response.completed":
                    completed = True
                    yield ProviderStreamEvent(result=self._result(event.response))
                elif event_type in ("response.failed", "response.incomplete"):
                    raise AIStreamInterruptedError(
                        provider_code=event_type,
                        request_id=getattr(getattr(event, "response", None), "_request_id", "") or "",
                    )
            if not completed:
                raise AIStreamInterruptedError(provider_code="stream_ended_without_completion")
        except AIProviderError:
            raise
        except Exception as exc:
            raise self._translate_error(exc) from exc
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()

    def _request_params(self, *, instructions, input_items, safety_identifier, stream, output_schema=None):
        params = {
            "model": self.configuration.model,
            "instructions": instructions,
            "input": input_items,
            "max_output_tokens": self.configuration.max_output_tokens,
            "store": self.configuration.store_provider_responses,
            "safety_identifier": safety_identifier,
            "stream": stream,
        }
        if self.configuration.temperature is not None:
            params["temperature"] = float(self.configuration.temperature)
        if self.configuration.reasoning_effort != AIConfiguration.ReasoningEffort.NONE:
            params["reasoning"] = {"effort": self.configuration.reasoning_effort}
        text = {"verbosity": self.configuration.response_verbosity}
        if output_schema:
            text["format"] = {
                "type": "json_schema",
                "name": "tasker_assistant_response",
                "schema": output_schema,
                "strict": True,
            }
        params["text"] = text
        return params

    @staticmethod
    def _result(response):
        return ProviderResult(
            text=getattr(response, "output_text", "") or "",
            response_id=getattr(response, "id", "") or "",
            request_id=getattr(response, "_request_id", "") or "",
            usage=_usage(response),
            metadata={"service_tier": getattr(response, "service_tier", None)},
        )

    @staticmethod
    def _translate_error(exc):
        request_id = getattr(exc, "request_id", "") or ""
        provider_code = str(getattr(exc, "code", "") or "")
        status_code = getattr(exc, "status_code", None)
        retry_after = _retry_after_seconds(exc)
        if isinstance(exc, openai.APITimeoutError):
            return AITimeoutError(provider_code=provider_code, request_id=request_id, status_code=status_code)
        if isinstance(exc, openai.RateLimitError):
            return AIProviderError(
                "The OpenAI rate limit was reached.",
                transient=True,
                provider_code=provider_code or "rate_limit",
                request_id=request_id,
                status_code=429,
                retry_after=retry_after,
            )
        if isinstance(exc, openai.APIConnectionError):
            return AIProviderError(
                "The OpenAI service could not be reached.",
                transient=True,
                provider_code=provider_code or "connection_error",
                request_id=request_id,
            )
        if isinstance(exc, openai.APIStatusError):
            transient = status_code in (408, 409, 429) or bool(status_code and status_code >= 500)
            return AIProviderError(
                "OpenAI returned an unsuccessful response.",
                transient=transient,
                provider_code=provider_code or f"http_{status_code}",
                request_id=request_id,
                status_code=status_code,
                retry_after=retry_after,
            )
        if isinstance(exc, AIProviderError):
            return exc
        return AIProviderError("The AI provider returned an unexpected error.", transient=False)


def _usage(response):
    usage = getattr(response, "usage", None)
    if usage is None:
        return TokenUsage()
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    return TokenUsage(
        input_tokens=getattr(usage, "input_tokens", 0) or 0,
        output_tokens=getattr(usage, "output_tokens", 0) or 0,
        cached_input_tokens=getattr(input_details, "cached_tokens", 0) or 0,
        reasoning_tokens=getattr(output_details, "reasoning_tokens", 0) or 0,
    )


def _retry_after_seconds(exc):
    headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
    try:
        if headers.get("retry-after-ms"):
            return max(0, float(headers["retry-after-ms"]) / 1000)
        if headers.get("retry-after"):
            return max(0, float(headers["retry-after"]))
    except (TypeError, ValueError):
        return None
    return None
