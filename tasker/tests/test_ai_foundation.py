from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.test import TestCase, override_settings
from django.urls import reverse

from tasker.ai.conversations import ConversationManager
from tasker.ai.agents import AgentDefinition, AgentRegistry
from tasker.ai.exceptions import (
    AIConfigurationError,
    AIDisabledError,
    AIPermissionError,
    AIProviderError,
    AIPromptError,
    AIRateLimitError,
    AIStreamInterruptedError,
)
from tasker.ai.prompts import PromptManager
from tasker.ai.providers.openai import OpenAIProvider
from tasker.ai.rate_limits import AIRateLimiter
from tasker.ai.service import AIService
from tasker.ai.settings import AIEnvironmentSettings
from tasker.ai.streaming import server_sent_event_stream
from tasker.ai.types import AIResult, AIStreamChunk, ProviderResult, ProviderStreamEvent, TokenUsage
from tasker.models import AIConfiguration, AIConversation, AIMessage, AIUsageLog, PromptTemplate


class NoopRateLimiter:
    def check(self, **kwargs):
        return None


class FakeProvider:
    provider_name = "FAKE"

    def __init__(self, *, failures=0, transient=True):
        self.failures = failures
        self.transient = transient
        self.calls = 0

    def generate(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise AIProviderError("Temporary failure", transient=self.transient, provider_code="temporary")
        return ProviderResult(
            text="Foundation response",
            response_id="resp_123",
            request_id="req_123",
            usage=TokenUsage(input_tokens=12, output_tokens=8, cached_input_tokens=4, reasoning_tokens=2),
        )

    def stream(self, **kwargs):
        self.calls += 1
        yield ProviderStreamEvent(delta="Foundation ")
        yield ProviderStreamEvent(delta="response")
        yield ProviderStreamEvent(
            result=ProviderResult(
                text="Foundation response",
                response_id="resp_stream",
                request_id="req_stream",
                usage=TokenUsage(input_tokens=10, output_tokens=5),
            )
        )


class AIFoundationServiceTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="ai-user", password="test-pass")
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="use_ai")
        )
        self.configuration = AIConfiguration.objects.get(provider=AIConfiguration.Provider.OPENAI)
        self.configuration.is_enabled = True
        self.configuration.max_retries = 2
        self.configuration.save()

    def service(self, provider, **kwargs):
        return AIService(
            provider_factory=lambda configuration: provider,
            rate_limiter=NoopRateLimiter(),
            sleep=kwargs.get("sleep", lambda seconds: None),
            random_source=lambda: 0,
        )

    def test_generate_records_conversation_messages_usage_and_request_ids(self):
        provider = FakeProvider()
        result = self.service(provider).generate_text(
            user=self.user,
            operation="foundation-test",
            prompt_key="foundation-general",
            variables={"input": "Verify the service boundary."},
            metadata={"feature": "foundation", "secret": "must-not-log"},
        )
        log = AIUsageLog.objects.get(pk=result.usage_log_id)
        messages = AIMessage.objects.filter(conversation_id=result.conversation_id)
        self.assertEqual(result.text, "Foundation response")
        self.assertEqual(messages.count(), 2)
        self.assertEqual(log.status, AIUsageLog.Status.SUCCEEDED)
        self.assertEqual(log.total_tokens, 20)
        self.assertEqual(log.cached_input_tokens, 4)
        self.assertEqual(log.reasoning_tokens, 2)
        self.assertEqual(log.provider_request_id, "req_123")
        self.assertNotIn("secret", log.request_metadata)
        self.assertNotIn("Verify the service boundary", str(log.request_metadata))

    def test_transient_provider_failure_retries_with_audited_attempt_count(self):
        provider = FakeProvider(failures=1)
        delays = []
        result = self.service(provider, sleep=delays.append).generate_text(
            user=self.user,
            operation="retry-test",
            prompt_key="foundation-general",
            variables={"input": "Retry safely."},
        )
        log = AIUsageLog.objects.get(pk=result.usage_log_id)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(log.attempts, 2)
        self.assertEqual(len(delays), 1)

    def test_retry_honors_provider_backoff_without_exceeding_cap(self):
        class BackoffProvider(FakeProvider):
            def generate(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise AIProviderError("Rate limited", transient=True, retry_after=5)
                return super().generate(**kwargs)

        provider = BackoffProvider()
        delays = []
        self.service(provider, sleep=delays.append).generate_text(
            user=self.user,
            operation="backoff-test",
            prompt_key="foundation-general",
            variables={"input": "Back off safely."},
        )
        self.assertEqual(delays, [5])

    def test_non_transient_provider_failure_does_not_retry(self):
        provider = FakeProvider(failures=3, transient=False)
        with self.assertRaises(AIProviderError):
            self.service(provider).generate_text(
                user=self.user,
                operation="failure-test",
                prompt_key="foundation-general",
                variables={"input": "Fail safely."},
            )
        log = AIUsageLog.objects.latest("created_at")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(log.status, AIUsageLog.Status.FAILED)
        self.assertEqual(log.error_code, "temporary")
        self.assertNotIn("Fail safely", log.error_message)

    def test_streaming_yields_deltas_then_finalizes_usage(self):
        chunks = list(
            self.service(FakeProvider()).stream_text(
                user=self.user,
                operation="stream-test",
                prompt_key="foundation-general",
                variables={"input": "Stream safely."},
            )
        )
        self.assertEqual("".join(chunk.delta for chunk in chunks), "Foundation response")
        self.assertTrue(chunks[-1].is_final)
        log = AIUsageLog.objects.get(pk=chunks[-1].result.usage_log_id)
        self.assertTrue(log.was_streamed)
        self.assertEqual(log.status, AIUsageLog.Status.SUCCEEDED)

    def test_disabled_and_unauthorized_requests_are_logged_without_provider_call(self):
        self.configuration.is_enabled = False
        self.configuration.save(update_fields=["is_enabled", "updated_at"])
        with self.assertRaises(AIDisabledError):
            self.service(FakeProvider()).generate_text(
                user=self.user,
                operation="disabled-test",
                prompt_key="foundation-general",
                variables={"input": "Do not call."},
            )
        self.assertEqual(AIUsageLog.objects.latest("created_at").status, AIUsageLog.Status.DISABLED)

        self.configuration.is_enabled = True
        self.configuration.save(update_fields=["is_enabled", "updated_at"])
        other = get_user_model().objects.create_user(username="no-ai")
        with self.assertRaises(AIPermissionError):
            self.service(FakeProvider()).generate_text(
                user=other,
                operation="unauthorized-test",
                prompt_key="foundation-general",
                variables={"input": "Do not call."},
            )
        self.assertEqual(AIUsageLog.objects.latest("created_at").status, AIUsageLog.Status.FAILED)

    def test_conversation_manager_prevents_cross_employee_access(self):
        conversation = ConversationManager().create(user=self.user)
        other = get_user_model().objects.create_user(username="other-ai-user")
        with self.assertRaises(AIPermissionError):
            ConversationManager().get_for_user(user=other, conversation_id=conversation.pk)

    def test_prompt_manager_requires_declared_variables_and_uses_config_override(self):
        with self.assertRaises(AIPromptError):
            PromptManager().render(
                key="foundation-general", variables={}, configuration=self.configuration
            )
        self.configuration.system_prompt_override = "Controlled system prompt"
        rendered = PromptManager().render(
            key="foundation-general",
            variables={"input": "Hello"},
            configuration=self.configuration,
        )
        self.assertEqual(rendered.system, "Controlled system prompt")
        self.assertEqual(rendered.user, "Hello")

    def test_missing_configuration_attempt_is_still_logged(self):
        self.configuration.delete(user=self.user)
        with self.assertRaises(AIConfigurationError):
            self.service(FakeProvider()).generate_text(
                user=self.user,
                operation="missing-config",
                prompt_key="foundation-general",
                variables={"input": "Do not call."},
            )
        log = AIUsageLog.objects.latest("created_at")
        self.assertEqual(log.status, AIUsageLog.Status.FAILED)
        self.assertEqual(log.error_code, "ai_configuration_error")

    def test_stream_interruption_after_output_is_not_retried(self):
        class InterruptedProvider(FakeProvider):
            def stream(self, **kwargs):
                self.calls += 1
                yield ProviderStreamEvent(delta="Partial")
                raise AIStreamInterruptedError(provider_code="connection_lost")

        provider = InterruptedProvider()
        with self.assertRaises(AIStreamInterruptedError):
            list(
                self.service(provider).stream_text(
                    user=self.user,
                    operation="interrupted-stream",
                    prompt_key="foundation-general",
                    variables={"input": "Stream."},
                )
            )
        self.assertEqual(provider.calls, 1)
        self.assertEqual(AIUsageLog.objects.latest("created_at").status, AIUsageLog.Status.INTERRUPTED)


class AIRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="limited-user")
        self.configuration = AIConfiguration.objects.get(provider="OPENAI")
        self.configuration.requests_per_minute = 1
        self.configuration.daily_request_limit = 100
        self.configuration.daily_token_limit = 1000
        self.configuration.save()

    def test_per_minute_limit_is_enforced(self):
        limiter = AIRateLimiter()
        limiter.check(user=self.user, configuration=self.configuration)
        with self.assertRaises(AIRateLimitError):
            limiter.check(user=self.user, configuration=self.configuration)


class AIModelIntegrityTests(TestCase):
    def test_only_one_active_configuration_per_provider(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            AIConfiguration.objects.create(name="Duplicate", provider="OPENAI", is_active=True)

    def test_only_one_active_prompt_version_per_key(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            PromptTemplate.objects.create(
                key="foundation-general",
                name="Duplicate",
                version=2,
                system_template="System",
                user_template="{input}",
                variables=["input"],
                is_active=True,
            )


@override_settings(
    STORAGES={
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }
)
class AIUsageDashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="usage-manager", password="pass")
        self.url = reverse("ims:tasker:ai_usage_dashboard")

    def test_dashboard_requires_usage_permission(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_dashboard_renders_aggregates_without_message_content(self):
        self.user.user_permissions.add(
            Permission.objects.get(content_type__app_label="tasker", codename="view_ai_usage")
        )
        AIUsageLog.objects.create(
            user=self.user,
            operation="foundation-test",
            status=AIUsageLog.Status.SUCCEEDED,
            model="test-model",
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
            error_message="",
        )
        self.client.force_login(self.user)
        response = self.client.get(self.url)
        self.assertContains(response, "AI Usage")
        self.assertContains(response, "test-model")
        self.assertContains(response, "15")
        self.assertContains(response, "No prompt content logged")


class OpenAIProviderContractTests(TestCase):
    @override_settings(TASKER_AI_OPENAI_API_KEY="test-key")
    def test_non_streaming_uses_responses_api_configuration(self):
        configuration = AIConfiguration.objects.get(provider="OPENAI")
        configuration.temperature = None
        response = SimpleNamespace(
            output_text="Provider response",
            id="resp_provider",
            _request_id="req_provider",
            service_tier="default",
            usage=SimpleNamespace(
                input_tokens=7,
                output_tokens=3,
                input_tokens_details=SimpleNamespace(cached_tokens=2),
                output_tokens_details=SimpleNamespace(reasoning_tokens=1),
            ),
        )

        class Responses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return response

        responses = Responses()
        client = SimpleNamespace(responses=responses)
        provider = OpenAIProvider(
            configuration=configuration,
            client=client,
            environment=AIEnvironmentSettings("test-key", "", "", 50000),
        )
        result = provider.generate(
            instructions="System", input_items=[{"role": "user", "content": "Hello"}], safety_identifier="safe"
        )
        self.assertEqual(result.text, "Provider response")
        self.assertEqual(result.usage.total_tokens, 10)
        self.assertEqual(responses.kwargs["model"], configuration.model)
        self.assertFalse(responses.kwargs["store"])
        self.assertNotIn("temperature", responses.kwargs)
        self.assertEqual(responses.kwargs["safety_identifier"], "safe")
        self.assertEqual(responses.kwargs["reasoning"]["effort"], configuration.reasoning_effort)
        self.assertEqual(responses.kwargs["text"]["verbosity"], configuration.response_verbosity)

    @override_settings(TASKER_AI_OPENAI_API_KEY="test-key")
    def test_structured_output_schema_is_forwarded_to_responses_api(self):
        configuration = AIConfiguration.objects.get(provider="OPENAI")
        response = SimpleNamespace(output_text='{"value":"ok"}', id="resp", usage=None)

        class Responses:
            def __init__(self):
                self.kwargs = None

            def create(self, **kwargs):
                self.kwargs = kwargs
                return response

        responses = Responses()
        provider = OpenAIProvider(configuration=configuration, client=SimpleNamespace(responses=responses))
        schema = {
            "type": "object", "additionalProperties": False,
            "properties": {"value": {"type": "string"}}, "required": ["value"],
        }
        provider.generate(
            instructions="System", input_items=[], safety_identifier="safe", output_schema=schema
        )
        output_format = responses.kwargs["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertEqual(output_format["schema"], schema)


class StreamingTransportTests(TestCase):
    def test_sse_transport_emits_delta_and_content_free_completion_metadata(self):
        result = AIResult(
            text="Secret response",
            conversation_id=2,
            message_id=3,
            usage_log_id=4,
            usage=TokenUsage(input_tokens=1, output_tokens=1),
        )
        events = list(
            server_sent_event_stream(
                [AIStreamChunk(delta="Hello"), AIStreamChunk(delta="", is_final=True, result=result)]
            )
        )
        self.assertIn('event: delta', events[0])
        self.assertIn('"text": "Hello"', events[0])
        self.assertIn('event: complete', events[1])
        self.assertNotIn("Secret response", events[1])


class AgentFoundationTests(TestCase):
    def test_registry_is_explicit_and_rejects_duplicate_agents(self):
        registry = AgentRegistry()
        definition = AgentDefinition(
            key="future-planner",
            name="Future planner",
            prompt_key="future-planner-v1",
            allowed_tools=("read_tasks",),
            requires_confirmation_for=("create_tasks",),
        )
        registry.register(definition)
        self.assertEqual(registry.get("future-planner"), definition)
        with self.assertRaises(ValueError):
            registry.register(definition)

    def test_environment_settings_hide_api_key_from_repr(self):
        environment = AIEnvironmentSettings("super-secret", "", "", 50000)
        self.assertNotIn("super-secret", repr(environment))
