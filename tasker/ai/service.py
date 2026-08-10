from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from django.utils.text import slugify

from tasker.models import AIConfiguration, AIConversation, AIMessage, AIUsageLog, PromptTemplate

from .configuration import AIConfigurationManager
from .conversations import ConversationManager
from .exceptions import (
    AIConfigurationError,
    AIDisabledError,
    AIPermissionError,
    AIProviderError,
    AIRateLimitError,
    AIServiceError,
    AIStreamInterruptedError,
)
from .prompts import PromptManager
from .providers import OpenAIProvider
from .rate_limits import AIRateLimiter
from .settings import AIEnvironmentSettings
from .types import AIResult, AIStreamChunk, ProviderResult


logger = logging.getLogger("tasker.ai")


@dataclass
class _PreparedRequest:
    user: object
    configuration: AIConfiguration
    conversation: AIConversation
    assistant_message: AIMessage
    usage_log: AIUsageLog
    instructions: str
    input_items: list[dict]
    output_schema: dict
    safety_identifier: str
    started_monotonic: float


class AIService:
    """Single audited application boundary for all future Tasker AI features."""

    RETRYABLE_MAX_DELAY_SECONDS = 30
    SAFE_METADATA_KEYS = {"source", "feature", "agent", "schema_version"}

    def __init__(
        self,
        *,
        configuration_manager=None,
        prompt_manager=None,
        conversation_manager=None,
        rate_limiter=None,
        provider_factory=None,
        sleep=time.sleep,
        random_source=random.random,
    ):
        self.configuration_manager = configuration_manager or AIConfigurationManager()
        self.prompt_manager = prompt_manager or PromptManager()
        self.conversation_manager = conversation_manager or ConversationManager()
        self.rate_limiter = rate_limiter or AIRateLimiter()
        self.provider_factory = provider_factory or self._default_provider
        self.sleep = sleep
        self.random_source = random_source

    def generate_text(
        self,
        *,
        user,
        operation,
        prompt_key,
        variables,
        conversation=None,
        agent_type="assistant",
        metadata=None,
    ) -> AIResult:
        prepared = self._prepare(
            user=user,
            operation=operation,
            prompt_key=prompt_key,
            variables=variables,
            conversation=conversation,
            agent_type=agent_type,
            metadata=metadata,
            streaming=False,
        )
        try:
            provider = self.provider_factory(prepared.configuration)
            provider_result, attempts = self._with_retry(
                lambda: provider.generate(
                    instructions=prepared.instructions,
                    input_items=prepared.input_items,
                    safety_identifier=prepared.safety_identifier,
                    output_schema=prepared.output_schema,
                ),
                prepared.configuration,
            )
            return self._succeed(prepared, provider_result, attempts)
        except AIServiceError as exc:
            self._fail(prepared, exc, attempts=getattr(exc, "attempts", 1))
            raise
        except Exception as exc:
            safe_error = AIProviderError("The AI provider returned an unexpected error.")
            self._fail(prepared, safe_error)
            raise safe_error from exc

    def stream_text(self, **kwargs):
        return self._stream_text(**kwargs)

    def _stream_text(
        self,
        *,
        user,
        operation,
        prompt_key,
        variables,
        conversation=None,
        agent_type="assistant",
        metadata=None,
    ):
        prepared = self._prepare(
            user=user,
            operation=operation,
            prompt_key=prompt_key,
            variables=variables,
            conversation=conversation,
            agent_type=agent_type,
            metadata=metadata,
            streaming=True,
        )
        finalized = False
        emitted = False
        attempts = 0
        pieces = []
        try:
            provider = self.provider_factory(prepared.configuration)
            while attempts <= prepared.configuration.max_retries:
                attempts += 1
                try:
                    final_provider_result = None
                    for event in provider.stream(
                        instructions=prepared.instructions,
                        input_items=prepared.input_items,
                        safety_identifier=prepared.safety_identifier,
                        output_schema=prepared.output_schema,
                    ):
                        if event.delta:
                            emitted = True
                            pieces.append(event.delta)
                            yield AIStreamChunk(delta=event.delta)
                        if event.result is not None:
                            final_provider_result = event.result
                    if final_provider_result is None:
                        raise AIStreamInterruptedError(provider_code="missing_final_response")
                    if not final_provider_result.text:
                        final_provider_result = ProviderResult(
                            text="".join(pieces),
                            response_id=final_provider_result.response_id,
                            request_id=final_provider_result.request_id,
                            usage=final_provider_result.usage,
                            metadata=final_provider_result.metadata,
                        )
                    result = self._succeed(prepared, final_provider_result, attempts)
                    finalized = True
                    yield AIStreamChunk(delta="", is_final=True, result=result)
                    return
                except AIProviderError as exc:
                    if emitted or not exc.transient or attempts > prepared.configuration.max_retries:
                        raise
                    self._sleep_before_retry(
                        configuration=prepared.configuration, attempts=attempts, error=exc
                    )
        except GeneratorExit:
            error = AIStreamInterruptedError(provider_code="consumer_disconnected")
            if not finalized:
                self._fail(prepared, error, attempts=attempts, status=AIUsageLog.Status.INTERRUPTED)
                finalized = True
            raise
        except AIServiceError as exc:
            if not finalized:
                self._fail(prepared, exc, attempts=attempts)
                finalized = True
            raise
        except Exception as exc:
            safe_error = AIProviderError("The AI provider returned an unexpected error.")
            if not finalized:
                self._fail(prepared, safe_error, attempts=attempts)
            raise safe_error from exc

    def _prepare(
        self,
        *,
        user,
        operation,
        prompt_key,
        variables,
        conversation,
        agent_type,
        metadata,
        streaming,
    ):
        started = time.monotonic()
        operation = slugify(str(operation))[:100] or "unknown"
        try:
            configuration = self.configuration_manager.current(require_enabled=False)
        except AIServiceError as exc:
            usage_log = AIUsageLog.objects.create(
                user=user if getattr(user, "is_authenticated", False) else None,
                provider="OPENAI",
                operation=operation,
                was_streamed=streaming,
                request_metadata=self._safe_metadata(metadata),
                created_by=user if getattr(user, "is_authenticated", False) else None,
                updated_by=user if getattr(user, "is_authenticated", False) else None,
            )
            self._fail_log_only(
                usage_log,
                exc,
                started_monotonic=started,
                status=AIUsageLog.Status.FAILED,
            )
            raise
        usage_log = AIUsageLog.objects.create(
            user=user if getattr(user, "is_authenticated", False) else None,
            configuration=configuration,
            provider=configuration.provider,
            model=configuration.model,
            operation=operation,
            was_streamed=streaming,
            request_metadata=self._safe_metadata(metadata),
            created_by=user if getattr(user, "is_authenticated", False) else None,
            updated_by=user if getattr(user, "is_authenticated", False) else None,
        )
        try:
            if not getattr(user, "is_authenticated", False) or not user.has_perm("tasker.use_ai"):
                raise AIPermissionError()
            if not configuration.is_enabled:
                raise AIDisabledError()
            if streaming and not configuration.allow_streaming:
                raise AIConfigurationError("Streaming is disabled in the active AI configuration.")
            self.rate_limiter.check(user=user, configuration=configuration)
            rendered = self.prompt_manager.render(
                key=prompt_key, variables=variables, configuration=configuration
            )
            environment = AIEnvironmentSettings.load()
            if len(rendered.system) + len(rendered.user) > environment.max_prompt_characters:
                raise AIConfigurationError("The rendered prompt exceeds the configured size limit.")
            prompt_template = PromptTemplate.objects.get(pk=rendered.template_id)
            conversation, assistant_message, input_items = self._prepare_conversation(
                user=user,
                conversation=conversation,
                agent_type=agent_type,
                metadata=metadata,
                prompt_template=prompt_template,
                rendered=rendered,
                usage_log=usage_log,
            )
            return _PreparedRequest(
                user=user,
                configuration=configuration,
                conversation=conversation,
                assistant_message=assistant_message,
                usage_log=usage_log,
                instructions=rendered.system,
                input_items=input_items,
                output_schema=rendered.output_schema,
                safety_identifier=salted_hmac("tasker.ai.safety", str(user.pk)).hexdigest(),
                started_monotonic=started,
            )
        except AIServiceError as exc:
            status = (
                AIUsageLog.Status.DISABLED
                if isinstance(exc, AIDisabledError)
                else AIUsageLog.Status.RATE_LIMITED
                if isinstance(exc, AIRateLimitError)
                else AIUsageLog.Status.FAILED
            )
            self._fail_log_only(usage_log, exc, started_monotonic=started, status=status)
            raise
        except Exception as exc:
            safe_error = AIConfigurationError("The AI request could not be prepared.")
            self._fail_log_only(
                usage_log,
                safe_error,
                started_monotonic=started,
                status=AIUsageLog.Status.FAILED,
            )
            raise safe_error from exc

    def _with_retry(self, callback, configuration):
        attempts = 0
        while attempts <= configuration.max_retries:
            attempts += 1
            try:
                return callback(), attempts
            except AIProviderError as exc:
                if not exc.transient or attempts > configuration.max_retries:
                    exc.attempts = attempts
                    raise
                self._sleep_before_retry(configuration=configuration, attempts=attempts, error=exc)

    @transaction.atomic
    def _prepare_conversation(
        self,
        *,
        user,
        conversation,
        agent_type,
        metadata,
        prompt_template,
        rendered,
        usage_log,
    ):
        if conversation is None:
            conversation = self.conversation_manager.create(
                user=user, agent_type=agent_type, metadata=self._safe_metadata(metadata)
            )
        else:
            conversation = self.conversation_manager.get_for_user(
                user=user, conversation_id=conversation.pk
            )
        user_message = self.conversation_manager.append(
            conversation=conversation,
            role=AIMessage.Role.USER,
            content=rendered.user,
            user=user,
            prompt_template=prompt_template,
            metadata={"prompt_key": rendered.key, "prompt_version": rendered.version},
        )
        input_items = self.conversation_manager.provider_history(conversation=conversation)
        assistant_message = self.conversation_manager.append(
            conversation=conversation,
            role=AIMessage.Role.ASSISTANT,
            content="",
            user=user,
            status=AIMessage.Status.PENDING,
            prompt_template=prompt_template,
        )
        usage_log.conversation = conversation
        usage_log.message = assistant_message
        usage_log.request_metadata = {
            **usage_log.request_metadata,
            "prompt_key": rendered.key,
            "prompt_version": rendered.version,
            "user_message_id": user_message.pk,
        }
        usage_log.save(update_fields=["conversation", "message", "request_metadata", "updated_at"])
        return conversation, assistant_message, input_items

    def _sleep_before_retry(self, *, configuration, attempts, error=None):
        base = float(configuration.retry_base_seconds)
        calculated = base * (2 ** (attempts - 1)) + self.random_source() * (base / 4)
        provider_delay = getattr(error, "retry_after", None) or 0
        delay = min(self.RETRYABLE_MAX_DELAY_SECONDS, max(calculated, provider_delay))
        self.sleep(delay)

    @transaction.atomic
    def _succeed(self, prepared, provider_result, attempts):
        elapsed = self._elapsed_ms(prepared.started_monotonic)
        usage = provider_result.usage
        message = AIMessage.objects.select_for_update().get(pk=prepared.assistant_message.pk)
        message.content = provider_result.text
        message.status = AIMessage.Status.COMPLETED
        message.provider_response_id = provider_result.response_id
        message.input_tokens = usage.input_tokens
        message.output_tokens = usage.output_tokens
        message.total_tokens = usage.total_tokens
        message.latency_ms = elapsed
        message.updated_by = prepared.user
        message.save()

        log = AIUsageLog.objects.select_for_update().get(pk=prepared.usage_log.pk)
        log.status = AIUsageLog.Status.SUCCEEDED
        log.provider_request_id = provider_result.request_id
        log.provider_response_id = provider_result.response_id
        log.input_tokens = usage.input_tokens
        log.cached_input_tokens = usage.cached_input_tokens
        log.output_tokens = usage.output_tokens
        log.reasoning_tokens = usage.reasoning_tokens
        log.total_tokens = usage.total_tokens
        log.latency_ms = elapsed
        log.attempts = attempts
        log.completed_at = timezone.now()
        log.updated_by = prepared.user
        log.save()
        logger.info(
            "AI request succeeded",
            extra={"ai_request_uuid": str(log.request_uuid), "operation": log.operation, "tokens": log.total_tokens},
        )
        return AIResult(
            text=message.content,
            conversation_id=prepared.conversation.pk,
            message_id=message.pk,
            usage_log_id=log.pk,
            usage=usage,
        )

    @transaction.atomic
    def _fail(self, prepared, error, *, attempts=1, status=None):
        status = status or (
            AIUsageLog.Status.INTERRUPTED
            if isinstance(error, AIStreamInterruptedError)
            else AIUsageLog.Status.RATE_LIMITED
            if isinstance(error, AIRateLimitError) or getattr(error, "status_code", None) == 429
            else AIUsageLog.Status.FAILED
        )
        message = AIMessage.objects.select_for_update().get(pk=prepared.assistant_message.pk)
        message.status = (
            AIMessage.Status.INTERRUPTED
            if status == AIUsageLog.Status.INTERRUPTED
            else AIMessage.Status.FAILED
        )
        message.error_code = error.code
        message.updated_by = prepared.user
        message.save(update_fields=["status", "error_code", "updated_by", "updated_at"])
        self._fail_log_only(
            AIUsageLog.objects.select_for_update().get(pk=prepared.usage_log.pk),
            error,
            started_monotonic=prepared.started_monotonic,
            attempts=attempts,
            status=status,
        )

    def _fail_log_only(self, log, error, *, started_monotonic, attempts=1, status):
        log.status = status
        log.provider_request_id = getattr(error, "request_id", "")
        log.error_type = error.__class__.__name__
        log.error_code = getattr(error, "provider_code", "") or error.code
        log.error_message = error.user_message[:500]
        log.latency_ms = self._elapsed_ms(started_monotonic)
        log.attempts = max(1, attempts)
        log.completed_at = timezone.now()
        log.save()
        logger.warning(
            "AI request failed",
            extra={"ai_request_uuid": str(log.request_uuid), "operation": log.operation, "error_code": log.error_code},
        )

    @classmethod
    def _safe_metadata(cls, metadata):
        metadata = metadata or {}
        return {
            key: str(value)[:100]
            for key, value in metadata.items()
            if key in cls.SAFE_METADATA_KEYS and isinstance(value, (str, int, float, bool))
        }

    @staticmethod
    def _elapsed_ms(started):
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _default_provider(configuration):
        if configuration.provider == AIConfiguration.Provider.OPENAI:
            return OpenAIProvider(configuration=configuration)
        raise AIConfigurationError(f"Unsupported AI provider: {configuration.provider}.")
