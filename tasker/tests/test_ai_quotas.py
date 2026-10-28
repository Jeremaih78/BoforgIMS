"""Durable quota admission and settlement; providers are always local fakes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.cache import cache
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from tasker.ai.exceptions import AIConfigurationError, AIProviderError, AIPromptError, AIRateLimitError, AIStreamInterruptedError
from tasker.ai.rate_limits import AIRateLimiter
from tasker.ai.service import AIService
from tasker.ai.types import ProviderResult, ProviderStreamEvent, TokenUsage
from tasker.models import AIConfiguration, AIUsageLog, PromptTemplate


class QuotaProvider:
    def __init__(self, *, failures=0, missing_usage=False, interrupted=False):
        self.calls = 0
        self.failures = failures
        self.missing_usage = missing_usage
        self.interrupted = interrupted

    def generate(self, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            raise AIProviderError("Unknown billed usage", transient=True)
        return ProviderResult(text="Done", usage=TokenUsage() if self.missing_usage else TokenUsage(12, 8))

    def stream(self, **kwargs):
        self.calls += 1
        yield ProviderStreamEvent(delta="Partial")
        if self.interrupted:
            raise AIStreamInterruptedError(provider_code="lost_connection")
        yield ProviderStreamEvent(result=ProviderResult(text="Done", usage=TokenUsage(12, 8)))


class QuotaFixture:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="quota-user")
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="use_ai"))
        self.configuration, _ = AIConfiguration.objects.get_or_create(provider="OPENAI", is_active=True)
        self.configuration.is_enabled = True
        self.configuration.daily_request_limit = 100
        self.configuration.requests_per_minute = 100
        self.configuration.daily_token_limit = 100000
        self.configuration.max_output_tokens = 64
        self.configuration.max_retries = 0
        self.configuration.save()
        PromptTemplate.objects.get_or_create(
            key="foundation-general", is_active=True,
            defaults={"name": "Quota fixture", "version": 1, "system_template": "Help with this request.", "user_template": "{input}", "variables": ["input"]},
        )

    def service(self, provider):
        return AIService(provider_factory=lambda configuration: provider, sleep=lambda delay: None, random_source=lambda: 0)

    def generate(self, service):
        return service.generate_text(user=self.user, operation="quota-test", prompt_key="foundation-general", variables={"input": "Hello"})

    def stream(self, service):
        return service.stream_text(user=self.user, operation="quota-stream", prompt_key="foundation-general", variables={"input": "Hello"})


class AIQuotaServiceTests(QuotaFixture, TestCase):
    def test_limit_one_allows_first_request_and_rejects_second(self):
        self.configuration.daily_request_limit = 1
        self.configuration.save()
        provider = QuotaProvider()
        self.generate(self.service(provider))
        with self.assertRaises(AIRateLimitError):
            self.generate(self.service(provider))
        self.assertEqual(provider.calls, 1)

    def test_success_releases_unused_reservation_and_keeps_actual_usage(self):
        result = self.generate(self.service(QuotaProvider()))
        log = AIUsageLog.objects.get(pk=result.usage_log_id)
        self.assertEqual(log.total_tokens, 20)
        self.assertEqual(log.quota_charged_tokens, 20)
        self.assertEqual(log.quota_reserved_tokens, 0)
        self.assertIsNotNone(log.quota_settled_at)

    def test_minute_admission_is_durable_after_cache_clear(self):
        self.configuration.requests_per_minute = 1
        self.configuration.save()
        provider = QuotaProvider()
        self.generate(self.service(provider))
        cache.clear()
        with self.assertRaises(AIRateLimitError):
            self.generate(self.service(provider))
        self.assertEqual(provider.calls, 1)

    def test_token_budget_is_reserved_before_provider_call(self):
        self.configuration.daily_token_limit = 1
        self.configuration.save()
        provider = QuotaProvider()
        with self.assertRaises(AIRateLimitError):
            self.generate(self.service(provider))
        self.assertEqual(provider.calls, 0)

    def test_soft_deleted_usage_cannot_refund_request_allowance(self):
        self.configuration.daily_request_limit = 1
        self.configuration.save()
        result = self.generate(self.service(QuotaProvider()))
        AIUsageLog.objects.get(pk=result.usage_log_id).delete(user=self.user)
        with self.assertRaises(AIRateLimitError):
            self.generate(self.service(QuotaProvider()))

    def test_missing_usage_is_charged_conservatively(self):
        result = self.generate(self.service(QuotaProvider(missing_usage=True)))
        log = AIUsageLog.objects.get(pk=result.usage_log_id)
        self.assertEqual(log.total_tokens, 0)
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget)
        self.assertGreater(log.quota_charged_tokens, self.configuration.max_output_tokens)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_retry_retains_unknown_attempt_charge_plus_final_usage(self):
        self.configuration.max_retries = 1
        self.configuration.save()
        provider = QuotaProvider(failures=1)
        result = self.generate(self.service(provider))
        log = AIUsageLog.objects.get(pk=result.usage_log_id)
        self.assertEqual(provider.calls, 2)
        self.assertEqual(log.quota_provider_attempts, 2)
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget + 20)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_retry_cannot_spend_without_another_token_admission(self):
        self.configuration.max_retries = 1
        self.configuration.save()
        provider = QuotaProvider(failures=1)

        def provider_factory(configuration):
            # Leave exactly enough for the admitted attempt, with no token
            # headroom for a retry. No real provider is involved.
            configuration.daily_token_limit = AIUsageLog.objects.latest("pk").quota_attempt_token_budget
            configuration.save()
            return provider

        with self.assertRaises(AIRateLimitError):
            self.generate(AIService(provider_factory=provider_factory, sleep=lambda delay: None))
        log = AIUsageLog.objects.latest("pk")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(log.quota_provider_attempts, 1)
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget)
        self.assertEqual(log.quota_reserved_tokens, 0)
        self.assertIsNotNone(log.quota_settled_at)

    def test_local_preparation_failure_does_not_consume_request_allowance(self):
        self.configuration.daily_request_limit = 1
        self.configuration.save()
        provider = QuotaProvider()
        with self.assertRaises(AIPromptError):
            self.service(provider).generate_text(user=self.user, operation="missing-input", prompt_key="foundation-general", variables={})
        self.generate(self.service(provider))
        self.assertEqual(provider.calls, 1)

    def test_repeated_settlement_cannot_refund_twice(self):
        limiter = AIRateLimiter()
        log = AIUsageLog.objects.create(user=self.user, operation="settle-twice")
        limiter.check(user=self.user, configuration=self.configuration, usage_log=log, token_budget=100)
        limiter.begin_attempt(user=self.user, configuration=self.configuration, usage_log=log)
        limiter.settle(user=self.user, usage_log=log, usage=TokenUsage(12, 8))
        limiter.settle(user=self.user, usage_log=log, usage=TokenUsage(1, 1))
        log.refresh_from_db()
        self.assertEqual(log.quota_charged_tokens, 20)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_provider_factory_failure_releases_unspent_tokens(self):
        def unavailable(configuration):
            raise AIConfigurationError("Unavailable local provider")
        with self.assertRaises(AIConfigurationError):
            self.generate(AIService(provider_factory=unavailable))
        log = AIUsageLog.objects.latest("pk")
        self.assertEqual(log.quota_provider_attempts, 0)
        self.assertEqual(log.quota_charged_tokens, 0)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_failed_attempt_keeps_charge_and_releases_reservation(self):
        with self.assertRaises(AIProviderError):
            self.generate(self.service(QuotaProvider(failures=1)))
        log = AIUsageLog.objects.latest("pk")
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget)
        self.assertEqual(log.quota_reserved_tokens, 0)
        self.assertIsNotNone(log.quota_settled_at)

    def test_stream_success_settles_actual_usage(self):
        chunks = list(self.stream(self.service(QuotaProvider())))
        log = AIUsageLog.objects.get(pk=chunks[-1].result.usage_log_id)
        self.assertEqual(log.quota_charged_tokens, 20)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_stream_interruption_keeps_unknown_usage_charge(self):
        with self.assertRaises(AIStreamInterruptedError):
            list(self.stream(self.service(QuotaProvider(interrupted=True))))
        log = AIUsageLog.objects.latest("pk")
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget)
        self.assertEqual(log.quota_reserved_tokens, 0)

    def test_stream_consumer_disconnect_settles_without_releasing_possible_spend(self):
        stream = self.stream(self.service(QuotaProvider()))
        next(stream)
        stream.close()
        log = AIUsageLog.objects.latest("pk")
        self.assertEqual(log.status, AIUsageLog.Status.INTERRUPTED)
        self.assertEqual(log.quota_charged_tokens, log.quota_attempt_token_budget)
        self.assertEqual(log.quota_reserved_tokens, 0)


class AIQuotaConcurrencyTests(QuotaFixture, TransactionTestCase):
    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_request_admission_allows_only_one(self):
        self.configuration.daily_request_limit = 1
        self.configuration.save()
        barrier = Barrier(2)
        user_pk = self.user.pk

        def request():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_pk)
                barrier.wait(timeout=10)
                try:
                    self.service(QuotaProvider()).generate_text(user=user, operation="concurrent-quota", prompt_key="foundation-general", variables={"input": "Hello"})
                    return "admitted"
                except AIRateLimitError:
                    return "limited"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: request(), range(2)))
        self.assertCountEqual(outcomes, ["admitted", "limited"])

    @skipUnlessDBFeature("has_select_for_update")
    def test_concurrent_token_reservations_cannot_overbook(self):
        self.configuration.daily_token_limit = 100
        self.configuration.save()
        barrier = Barrier(2)
        user_pk, config_pk = self.user.pk, self.configuration.pk

        def reserve():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=user_pk)
                configuration = AIConfiguration.objects.get(pk=config_pk)
                log = AIUsageLog.objects.create(user=user, operation="token-reservation")
                barrier.wait(timeout=10)
                try:
                    AIRateLimiter().check(user=user, configuration=configuration, usage_log=log, token_budget=60)
                    return "admitted"
                except AIRateLimitError:
                    return "limited"
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as workers:
            outcomes = list(workers.map(lambda _: reserve(), range(2)))
        self.assertCountEqual(outcomes, ["admitted", "limited"])

    def test_provider_io_runs_outside_quota_transaction(self):
        class OutsideTransaction(QuotaProvider):
            def generate(provider, **kwargs):
                self.assertFalse(connection.in_atomic_block)
                return super().generate(**kwargs)
        self.generate(self.service(OutsideTransaction()))
