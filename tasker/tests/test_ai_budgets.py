"""Money limits use local provider fakes and disposable PostgreSQL only."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections, connection
from django.test import TestCase, TransactionTestCase, override_settings, skipUnlessDBFeature
from django.utils import timezone

from accounting.models import Company
from tasker.ai.budgets import (
    AIBudgetAccountingError, AIBudgetLimitError, AIBudgetUnavailableError,
    MoneyBudget, budget_status, company_budget_status, configuration_fingerprint, week_bounds,
)
from tasker.ai.exceptions import AIConfigurationError, AIProviderError
from tasker.ai.privacy import redact_conversation
from tasker.ai.service import AIService
from tasker.ai.types import ProviderResult, ProviderStreamEvent, TokenUsage
from tasker.ai_budget_models import (
    AIBudgetEnrollment, AIBudgetPolicy, AICompanyBudget, AIPriceCard,
    AISpendAttempt, AISpendReservation,
)
from tasker.models import AIConfiguration, AIMessage, AIUsageLog, PromptTemplate
from tasker.organization_models import OrganizationAccess


class MoneyFixture:
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="money-employee")
        self.user.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="use_ai"))
        self.company = Company.objects.create(name="Money fixture company")
        self.policy, _ = AIBudgetPolicy.objects.update_or_create(key=1, defaults={
            "is_enabled": True, "currency": "USD", "timezone_name": "Africa/Harare",
            "week_start_day": 0, "reset_hour": 0, "reset_minute": 0,
        })
        self.company_budget = AICompanyBudget.objects.create(company=self.company, is_enabled=True, weekly_limit=Decimal("5"), charge_requester=True)
        self.enrollment = AIBudgetEnrollment.objects.create(user=self.user, company=self.company, is_active=True)
        self.configuration, _ = AIConfiguration.objects.get_or_create(provider="OPENAI", is_active=True)
        self.configuration.is_enabled = True
        self.configuration.model = "fixture-text-model-20261028"
        self.configuration.reasoning_effort = "none"
        self.configuration.temperature = None
        self.configuration.max_output_tokens = 1000
        self.configuration.max_retries = 1
        self.configuration.daily_token_limit = 1000000
        self.configuration.daily_request_limit = 1000
        self.configuration.requests_per_minute = 1000
        self.configuration.store_provider_responses = False
        self.configuration.save()
        self.card = self.price_card()
        self.money = MoneyBudget()
        PromptTemplate.objects.create(key="money-fixture", name="Budget fixture", version=1,
            system_template="Help with this request.", user_template="{input}", variables=["input"], is_active=True)

    def price_card(self, *, version=1, ordinary="100", cached="50", output="200", write=None):
        return AIPriceCard.objects.create(
            provider="OPENAI", model=self.configuration.model, service_tier="default", version=version,
            is_active=True, is_verified=True, verified_at=timezone.now()-timedelta(days=1),
            valid_until=timezone.now()+timedelta(days=30), input_usd_per_million=Decimal(ordinary),
            cached_input_usd_per_million=Decimal(cached), output_usd_per_million=Decimal(output),
            cache_write_usd_per_million=Decimal(write) if write is not None else None,
            max_input_tokens=20000, max_output_tokens=2000,
            capability_profile={"model": self.configuration.model, "revision": "offline-fixture-v1",
                "structured_outputs": True, "temperature": False,
                "reasoning_efforts": ["none"], "verbosity_levels": ["low", "medium", "high"],
                "max_output_tokens": 2000},
        )

    def replace_price(self, **kwargs):
        self.card.is_active = False
        self.card.save(update_fields=["is_active"])
        self.card = self.price_card(version=self.card.version+1, **kwargs)

    def admission(self, *, user=None, input_bound=1000, output_bound=1000):
        user = user or self.user
        log = AIUsageLog.objects.create(user=user, configuration=self.configuration, operation="money-fixture")
        reservation = self.money.admit(user=user, configuration=self.configuration, usage_log=log,
            input_bound=input_bound, output_bound=output_bound, prompt_fingerprint="a"*64)
        return log, reservation

    def begin(self, log):
        return self.money.begin_attempt(user=self.user, configuration=self.configuration, usage_log=log)

    def settle(self, log, *, usage=None, metadata=None):
        return self.money.settle(user=self.user, usage_log=log, usage=usage, metadata=metadata)

    def provider_metadata(self, **overrides):
        return {"model": self.configuration.model, "service_tier": "default",
                "usage_available": True, "usage_validated": True, **overrides}

    def service(self, provider):
        return AIService(provider_factory=lambda configuration: provider, sleep=lambda delay: None, random_source=lambda: 0)

    def generate(self, service):
        return service.generate_text(user=self.user, operation="money-fixture", prompt_key="money-fixture", variables={"input": "Hello"})


@override_settings(TASKER_AI_BUDGET_ENFORCED=True)
class AIMoneyBudgetTests(MoneyFixture, TestCase):
    def test_configuration_fingerprint_survives_decimal_database_roundtrip(self):
        self.configuration.retry_base_seconds = Decimal("1.0")
        self.configuration.save()
        fresh = AIConfiguration.objects.get(pk=self.configuration.pk)
        self.assertEqual(configuration_fingerprint(self.configuration), configuration_fingerprint(fresh))

    def test_confirmed_company_ceiling_cannot_be_increased(self):
        self.company_budget.weekly_limit = Decimal("5.000000000000000001")
        with self.assertRaises(ValidationError):
            self.company_budget.save()

    def test_calendar_reset_is_monday_midnight_in_harare(self):
        monday = datetime(2026, 10, 25, 22, tzinfo=datetime_timezone.utc)
        start, end = week_bounds(self.policy, monday)
        self.assertEqual(start, monday)
        self.assertEqual(end, monday+timedelta(days=7))
        previous, _ = week_bounds(self.policy, monday-timedelta(microseconds=1))
        self.assertEqual(previous, monday-timedelta(days=7))

    def test_reservation_cannot_understate_the_configured_output_limit(self):
        with self.assertRaises(AIBudgetUnavailableError):
            self.admission(output_bound=1)
        self.assertFalse(AISpendReservation.objects.exists())

    def test_admission_reserves_before_dispatch_and_rejects_another_full_hold(self):
        log, reservation = self.admission()
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))
        self.assertEqual(reservation.attempts.get().status, AISpendAttempt.Status.RESERVED)
        with self.assertRaises(AIBudgetLimitError):
            self.admission()
        self.assertEqual(AISpendReservation.objects.count(), 1)

    def test_exact_half_dollar_boundary_is_allowed_but_not_exceeded(self):
        self.configuration.max_output_tokens = 2000
        self.configuration.save()
        log, reservation = self.admission(output_bound=2000)
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.5"))
        with self.assertRaises(AIBudgetLimitError):
            self.admission(input_bound=1, output_bound=2000)

    def test_independent_company_ceiling_blocks_before_user_limit(self):
        self.company_budget.weekly_limit = Decimal("0.25")
        self.company_budget.save()
        with self.assertRaises(AIBudgetLimitError):
            self.admission()
        self.assertFalse(AISpendReservation.objects.exists())

    def test_employee_cap_is_shared_across_billing_companies(self):
        self.admission()
        other = Company.objects.create(name="Second billing company")
        AICompanyBudget.objects.create(company=other, is_enabled=True, weekly_limit=Decimal("5"), charge_requester=True)
        self.enrollment.company = other
        self.enrollment.save()
        with self.assertRaises(AIBudgetLimitError):
            self.admission()
        self.assertEqual(budget_status(self.user)["reserved"], "0.300000000000000000")

    def test_cached_input_and_reasoning_are_not_double_counted(self):
        log, reservation = self.admission()
        self.begin(log)
        result = self.settle(log, usage=TokenUsage(12, 8, cached_input_tokens=4, reasoning_tokens=2), metadata=self.provider_metadata())
        attempt = reservation.attempts.get()
        self.assertTrue(result["safe"])
        self.assertEqual(attempt.charged_amount, Decimal("0.0026"))
        self.assertEqual(attempt.held_amount, 0)

    def test_explicit_cache_writes_use_their_own_price_once(self):
        self.replace_price(write="250")
        log, reservation = self.admission()
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.45"))
        self.begin(log)
        self.settle(log, usage=TokenUsage(12, 8, cached_input_tokens=4, reasoning_tokens=2, cache_write_tokens=2), metadata=self.provider_metadata())
        self.assertEqual(reservation.attempts.get().charged_amount, Decimal("0.0029"))

    def test_failure_before_dispatch_releases_the_hold(self):
        log, reservation = self.admission()
        self.settle(log)
        attempt = reservation.attempts.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.RELEASED)
        self.assertEqual(attempt.held_amount, 0)

    def test_unknown_dispatch_is_not_free_and_cannot_be_soft_deleted_away(self):
        log, reservation = self.admission()
        self.begin(log)
        self.settle(log)
        log.delete(user=self.user)
        self.assertEqual(reservation.attempts.get().status, AISpendAttempt.Status.UNKNOWN)
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))
        with self.assertRaises(AIBudgetLimitError):
            self.admission()

    def test_retry_needs_another_reservation_and_keeps_unknown_first_charge(self):
        self.replace_price(ordinary="50", cached="25", output="100")
        log, reservation = self.admission()
        self.begin(log)
        self.begin(log)
        self.settle(log, usage=TokenUsage(12, 8, cached_input_tokens=4), metadata=self.provider_metadata())
        first, final = list(reservation.attempts.order_by("number"))
        self.assertEqual(first.status, AISpendAttempt.Status.UNKNOWN)
        self.assertEqual(first.held_amount, Decimal("0.15"))
        self.assertEqual(final.charged_amount, Decimal("0.0013"))
        self.assertEqual(final.held_amount, 0)

    def test_retry_cannot_reuse_first_attempt_money(self):
        log, reservation = self.admission()
        self.begin(log)
        with self.assertRaises(AIBudgetLimitError):
            self.begin(log)
        self.assertEqual(reservation.attempts.count(), 1)
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))

    def test_missing_response_usage_keeps_hold_and_returns_unsafe(self):
        log, reservation = self.admission()
        self.begin(log)
        result = self.settle(log, usage=TokenUsage(), metadata=self.provider_metadata(usage_available=False))
        self.assertFalse(result["safe"])
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))

    def test_unpriced_cache_writes_require_review(self):
        log, reservation = self.admission()
        self.begin(log)
        self.settle(log, usage=TokenUsage(12, 8, cache_write_tokens=2), metadata=self.provider_metadata())
        reservation.refresh_from_db()
        self.assertTrue(reservation.requires_review)
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))

    def test_unexpected_model_blocks_future_spending_and_preserves_hold(self):
        log, reservation = self.admission()
        self.begin(log)
        self.settle(log, usage=TokenUsage(12, 8), metadata=self.provider_metadata(model="unexpected-model"))
        reservation.refresh_from_db()
        self.assertTrue(reservation.requires_review)
        self.assertEqual(reservation.attempts.get().held_amount, Decimal("0.3"))
        with self.assertRaises(AIBudgetUnavailableError):
            self.admission(input_bound=1)

    def test_out_of_bounds_report_records_truth_and_blocks_future_use(self):
        log, reservation = self.admission()
        self.begin(log)
        result = self.settle(log, usage=TokenUsage(10000, 1000), metadata=self.provider_metadata())
        reservation.refresh_from_db()
        self.assertFalse(result["safe"])
        self.assertTrue(reservation.requires_review)
        self.assertEqual(reservation.attempts.get().charged_amount, Decimal("1.2"))

    def test_settlement_is_idempotent(self):
        log, reservation = self.admission()
        self.begin(log)
        self.settle(log, usage=TokenUsage(12, 8), metadata=self.provider_metadata())
        self.settle(log, usage=TokenUsage(1000, 1000), metadata=self.provider_metadata())
        self.assertEqual(reservation.attempts.get().charged_amount, Decimal("0.0028"))

    def test_company_and_enrollment_must_be_explicit(self):
        self.enrollment.is_active = False
        self.enrollment.save()
        with self.assertRaises(AIBudgetUnavailableError):
            self.admission()
        self.assertFalse(budget_status(self.user)["configured"])

    def test_unconfigured_calendar_and_expired_price_fail_closed(self):
        self.policy.is_enabled = False
        self.policy.save()
        with self.assertRaises(AIBudgetUnavailableError):
            self.admission()
        self.policy.is_enabled = True
        self.policy.save()
        with patch("tasker.ai.budgets.timezone.now", return_value=timezone.now()+timedelta(days=31)):
            with self.assertRaises(AIBudgetUnavailableError):
                self.admission()

    def test_price_and_used_calendar_cannot_be_rewritten(self):
        self.card.input_usd_per_million = Decimal("1")
        with self.assertRaises(ValidationError):
            self.card.save()
        self.admission()
        self.policy.week_start_day = 1
        with self.assertRaises(ValidationError):
            self.policy.save()
        self.policy.week_start_day = 0
        self.policy.employee_weekly_limit = Decimal("0.51")
        with self.assertRaises(ValidationError):
            self.policy.save()

    def test_admitted_price_and_configuration_snapshots_cannot_be_rewritten(self):
        log, reservation = self.admission()
        reservation.configuration_snapshot["model"] = "different-model"
        with self.assertRaises(ValidationError):
            reservation.save()
        reservation.refresh_from_db()
        reservation.price_snapshot["input"] = "0"
        with self.assertRaises(ValidationError):
            reservation.save()
        reservation.refresh_from_db()
        self.assertEqual(Decimal(reservation.policy_snapshot["company_weekly_limit"]), Decimal("5"))
        self.begin(log)
        self.assertTrue(self.settle(log, usage=TokenUsage(12, 8), metadata=self.provider_metadata())["safe"])

    def test_configuration_change_before_dispatch_is_rejected(self):
        log, reservation = self.admission()
        self.configuration.max_retries = 2
        self.configuration.save()
        with self.assertRaises(AIBudgetUnavailableError):
            self.begin(log)
        self.assertIsNone(reservation.attempts.get().started_at)

    def test_new_week_has_its_own_allowance_without_mutating_old_evidence(self):
        now = timezone.now()
        with patch("tasker.ai.budgets.timezone.now", return_value=now):
            first_log, first = self.admission()
        with patch("tasker.ai.budgets.timezone.now", return_value=now+timedelta(days=7)):
            next_log, second = self.admission()
        self.assertNotEqual(first.period_start, second.period_start)
        self.assertEqual(first.attempts.get().held_amount, Decimal("0.3"))

    def test_retry_cannot_charge_an_old_week_after_reset(self):
        now = timezone.now()
        with patch("tasker.ai.budgets.timezone.now", return_value=now):
            log, reservation = self.admission()
            self.begin(log)
        with patch("tasker.ai.budgets.timezone.now", return_value=now+timedelta(days=7)):
            with self.assertRaises(AIBudgetUnavailableError):
                self.begin(log)
        self.assertEqual(reservation.attempts.count(), 1)

    def test_company_costs_require_a_current_company_grant_and_contain_no_prompts(self):
        self.admission()
        outsider = get_user_model().objects.create_user(username="money-manager")
        outsider.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="manage_ai_configuration"))
        with self.assertRaises(PermissionDenied):
            company_budget_status(outsider, self.company)
        OrganizationAccess.objects.create(user=outsider, company=self.company, can_manage=True, is_active=True)
        report = company_budget_status(outsider, self.company)
        self.assertEqual(report["reserved"], "0.300000000000000000")
        self.assertNotIn("prompt", str(report))
        self.assertNotIn("message", str(report))

    def test_service_records_actual_money_through_its_single_boundary(self):
        outer = self
        class Provider:
            def generate(self, **kwargs):
                return ProviderResult(text="Done", usage=TokenUsage(12, 8, cached_input_tokens=4), metadata=outer.provider_metadata())
        result = self.generate(self.service(Provider()))
        attempt = AISpendAttempt.objects.get(reservation__usage_log_id=result.usage_log_id)
        self.assertEqual(attempt.charged_amount, Decimal("0.0026"))
        self.assertTrue(AIUsageLog.objects.get(pk=result.usage_log_id).request_metadata["effective_prompt_fingerprint"])

    def test_service_commits_unknown_cost_before_reporting_failure(self):
        outer = self
        class Provider:
            def generate(self, **kwargs):
                return ProviderResult(text="Not safe to apply", usage=TokenUsage(), metadata=outer.provider_metadata(usage_available=False))
        with self.assertRaises(AIBudgetAccountingError):
            self.generate(self.service(Provider()))
        attempt = AISpendAttempt.objects.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.UNKNOWN)
        self.assertGreater(attempt.held_amount, 0)
        self.assertEqual(AIUsageLog.objects.latest("pk").status, AIUsageLog.Status.FAILED)

    def test_streaming_uses_the_same_money_settlement(self):
        self.configuration.allow_streaming = True
        self.configuration.save()
        outer = self
        class Provider:
            def stream(self, **kwargs):
                yield ProviderStreamEvent(delta="Done")
                yield ProviderStreamEvent(result=ProviderResult(
                    text="Done", usage=TokenUsage(12, 8), metadata=outer.provider_metadata()))
        chunks = list(self.service(Provider()).stream_text(user=self.user, operation="money-stream",
            prompt_key="money-fixture", variables={"input": "Hello"}))
        self.assertTrue(chunks[-1].is_final)
        attempt = AISpendAttempt.objects.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.KNOWN)
        self.assertEqual(attempt.charged_amount, Decimal("0.0028"))

    def test_service_retry_reserves_again_and_keeps_the_unknown_first_charge(self):
        self.replace_price(ordinary="50", cached="25", output="100")
        outer = self
        class Provider:
            calls = 0
            def generate(self, **kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise AIProviderError("Temporary offline fixture failure.", transient=True)
                return ProviderResult(text="Done", usage=TokenUsage(12, 8), metadata=outer.provider_metadata())
        provider = Provider()
        self.generate(self.service(provider))
        first, final = list(AISpendAttempt.objects.order_by("number"))
        self.assertEqual(provider.calls, 2)
        self.assertEqual(first.status, AISpendAttempt.Status.UNKNOWN)
        self.assertGreater(first.held_amount, 0)
        self.assertEqual(final.status, AISpendAttempt.Status.KNOWN)
        self.assertEqual(final.charged_amount, Decimal("0.0014"))
        self.assertEqual(final.held_amount, 0)

    def test_redaction_during_provider_io_keeps_measured_charge_without_restoring_content(self):
        outer = self
        class Provider:
            def generate(self, **kwargs):
                log = AIUsageLog.objects.latest("pk")
                redact_conversation(user=outer.user, conversation_id=log.conversation_id)
                return ProviderResult(text="Must remain erased", response_id="offline-response",
                    usage=TokenUsage(12, 8), metadata=outer.provider_metadata())
        result = self.generate(self.service(Provider()))
        self.assertEqual(result.text, "")
        message = AIMessage.all_objects.get(pk=result.message_id)
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.content, "")
        self.assertEqual(message.provider_response_id, "")
        self.assertEqual(message.message_metadata, {})
        self.assertEqual(AISpendAttempt.objects.get().charged_amount, Decimal("0.0028"))
        self.assertEqual(AISpendAttempt.objects.get().held_amount, 0)
        self.assertEqual(AIUsageLog.objects.get(pk=result.usage_log_id).status, AIUsageLog.Status.SUCCEEDED)

    def test_redaction_during_failed_provider_io_preserves_unknown_charge(self):
        outer = self
        class Provider:
            def generate(self, **kwargs):
                log = AIUsageLog.objects.latest("pk")
                redact_conversation(user=outer.user, conversation_id=log.conversation_id)
                raise AIProviderError("Offline fixture failed after erasure.")
        with self.assertRaises(AIProviderError):
            self.generate(self.service(Provider()))
        message = AIMessage.all_objects.get(role=AIMessage.Role.ASSISTANT)
        self.assertTrue(message.is_deleted)
        self.assertEqual(message.content, "")
        self.assertEqual(message.error_code, "")
        self.assertEqual(AISpendAttempt.objects.get().status, AISpendAttempt.Status.UNKNOWN)
        self.assertGreater(AISpendAttempt.objects.get().held_amount, 0)

    def test_provider_construction_failure_refunds_money_before_dispatch(self):
        def factory(configuration):
            raise AIConfigurationError("Offline fixture failure")
        with self.assertRaises(AIConfigurationError):
            self.generate(AIService(provider_factory=factory))
        attempt = AISpendAttempt.objects.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.RELEASED)
        self.assertEqual(attempt.held_amount, 0)

    def test_disabled_budget_never_constructs_a_provider(self):
        self.policy.is_enabled = False
        self.policy.save()
        calls = []
        with self.assertRaises(AIBudgetUnavailableError):
            self.generate(AIService(provider_factory=lambda configuration: calls.append(True)))
        self.assertEqual(calls, [])
        self.assertFalse(AISpendReservation.objects.exists())

    def test_failed_money_attempt_rolls_back_token_attempt(self):
        calls = []
        def factory(configuration):
            configuration.max_retries = 2
            configuration.save()
            class Provider:
                def generate(self, **kwargs):
                    calls.append(True)
            return Provider()
        with self.assertRaises(AIBudgetUnavailableError):
            self.generate(AIService(provider_factory=factory))
        self.assertEqual(calls, [])
        self.assertEqual(AIUsageLog.objects.latest("pk").quota_provider_attempts, 0)
        self.assertEqual(AISpendAttempt.objects.get().held_amount, 0)


@override_settings(TASKER_AI_BUDGET_ENFORCED=True)
class AIMoneyConcurrencyTests(MoneyFixture, TransactionTestCase):
    def test_dispatch_guard_and_provider_execute_outside_atomic_transactions(self):
        outer = self
        seen = []
        def guard():
            attempt = AISpendAttempt.objects.get()
            seen.append(("guard", connection.in_atomic_block, attempt.status))
        class Provider:
            def generate(self, **kwargs):
                seen.append(("provider", connection.in_atomic_block, AISpendAttempt.objects.get().status))
                return ProviderResult(text="Done", usage=TokenUsage(12, 8), metadata=outer.provider_metadata())
        self.service(Provider()).generate_text(user=self.user, operation="money-guard",
            prompt_key="money-fixture", variables={"input": "Hello"}, before_provider_attempt=guard)
        self.assertEqual(seen, [("guard", False, AISpendAttempt.Status.RESERVED),
                                ("provider", False, AISpendAttempt.Status.DISPATCHED)])

    def test_cancelled_first_dispatch_releases_the_undispatched_hold(self):
        calls = []
        def guard():
            self.assertFalse(connection.in_atomic_block)
            raise AIConfigurationError("Request cancelled by offline fixture.")
        class Provider:
            def generate(self, **kwargs):
                calls.append(True)
        with self.assertRaises(AIConfigurationError):
            self.service(Provider()).generate_text(user=self.user, operation="money-guard",
                prompt_key="money-fixture", variables={"input": "Hello"}, before_provider_attempt=guard)
        self.assertEqual(calls, [])
        attempt = AISpendAttempt.objects.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.RELEASED)
        self.assertEqual(attempt.held_amount, 0)
        self.assertEqual(AIUsageLog.objects.latest("pk").quota_provider_attempts, 0)

    def test_guard_rechecks_after_retry_sleep_and_retains_first_unknown_charge(self):
        self.replace_price(ordinary="50", cached="25", output="100")
        events = []
        def guard():
            self.assertFalse(connection.in_atomic_block)
            events.append("guard")
            if events.count("guard") == 2:
                raise AIConfigurationError("Worker lease no longer belongs to this request.")
        class Provider:
            def generate(self, **kwargs):
                events.append("provider")
                raise AIProviderError("Temporary offline fixture failure.", transient=True)
        service = AIService(provider_factory=lambda config: Provider(),
            sleep=lambda delay: events.append("sleep"), random_source=lambda: 0)
        with self.assertRaises(AIConfigurationError):
            service.generate_text(user=self.user, operation="money-guard",
                prompt_key="money-fixture", variables={"input": "Hello"}, before_provider_attempt=guard)
        self.assertEqual(events, ["guard", "provider", "sleep", "guard"])
        attempt = AISpendAttempt.objects.get()
        self.assertEqual(attempt.status, AISpendAttempt.Status.UNKNOWN)
        self.assertGreater(attempt.held_amount, 0)
        self.assertEqual(AIUsageLog.objects.latest("pk").quota_provider_attempts, 1)

    def test_streaming_dispatch_also_honors_the_guard_before_io(self):
        self.configuration.allow_streaming = True
        self.configuration.save()
        calls = []
        def guard():
            self.assertFalse(connection.in_atomic_block)
            raise AIConfigurationError("Streaming cancelled by offline fixture.")
        class Provider:
            def stream(self, **kwargs):
                calls.append(True)
                yield ProviderStreamEvent(delta="Should not be emitted")
        with self.assertRaises(AIConfigurationError):
            list(self.service(Provider()).stream_text(user=self.user, operation="money-guard-stream",
                prompt_key="money-fixture", variables={"input": "Hello"}, before_provider_attempt=guard))
        self.assertEqual(calls, [])
        self.assertEqual(AISpendAttempt.objects.get().status, AISpendAttempt.Status.RELEASED)

    @skipUnlessDBFeature("has_select_for_update")
    def test_one_employee_cannot_reserve_the_same_personal_headroom_twice(self):
        barrier = Barrier(2)
        config_id = self.configuration.pk
        actor_id = self.user.pk
        def reserve(_):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=actor_id)
                config = AIConfiguration.objects.get(pk=config_id)
                log = AIUsageLog.objects.create(user=actor, configuration=config, operation="money-user-race")
                barrier.wait(timeout=10)
                try:
                    MoneyBudget().admit(user=actor, configuration=config, usage_log=log,
                        input_bound=1000, output_bound=1000, prompt_fingerprint="c"*64)
                    return "admitted"
                except AIBudgetLimitError:
                    return "limited"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, (0, 1)))
        self.assertCountEqual(outcomes, ["admitted", "limited"])
        self.assertEqual(AISpendAttempt.objects.get().held_amount, Decimal("0.3"))

    @skipUnlessDBFeature("has_select_for_update")
    def test_two_employees_cannot_reserve_the_same_company_headroom(self):
        other = get_user_model().objects.create_user(username="money-concurrent")
        other.user_permissions.add(Permission.objects.get(content_type__app_label="tasker", codename="use_ai"))
        AIBudgetEnrollment.objects.create(user=other, company=self.company, is_active=True)
        self.company_budget.weekly_limit = Decimal("0.45")
        self.company_budget.save()
        barrier = Barrier(2)
        config_id = self.configuration.pk
        def reserve(user_id):
            close_old_connections()
            try:
                actor = get_user_model().objects.get(pk=user_id)
                config = AIConfiguration.objects.get(pk=config_id)
                log = AIUsageLog.objects.create(user=actor, configuration=config, operation="money-race")
                barrier.wait(timeout=10)
                try:
                    MoneyBudget().admit(user=actor, configuration=config, usage_log=log, input_bound=1000, output_bound=1000, prompt_fingerprint="b"*64)
                    return "admitted"
                except AIBudgetLimitError:
                    return "limited"
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(reserve, (self.user.pk, other.pk)))
        self.assertCountEqual(outcomes, ["admitted", "limited"])
        self.assertEqual(AISpendAttempt.objects.get().held_amount, Decimal("0.3"))
