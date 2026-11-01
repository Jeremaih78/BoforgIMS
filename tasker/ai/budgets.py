"""Atomic, exact weekly money reservations for the single AIService boundary.

Only text-token/default-tier prices are supported. Unknown attribution, prices,
currency, model capabilities or usage do not acquire an imaginary zero price.
The shared policy row serializes admission across users and companies; no lock
is held across provider I/O. All values retained here are content-free evidence.
"""

from datetime import datetime, time, timedelta, timezone as datetime_timezone
from decimal import Decimal, localcontext
import hashlib
import json
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from tasker.ai_budget_models import (
    AIBudgetEnrollment, AIBudgetPolicy, AICompanyBudget, AIPriceCard,
    AISpendAttempt, AISpendReservation, EMPLOYEE_MAXIMUM, COMPANY_MAXIMUM,
)

from .capabilities import preflight_configuration, require_preflight
from .exceptions import AIConfigurationError, AIProviderError, AIRateLimitError


ZERO = Decimal("0")
UNIT = Decimal("0.000000000000000001")
CONFIGURATION_FIELDS = (
    "provider", "model", "reasoning_effort", "response_verbosity", "temperature",
    "max_output_tokens", "timeout_seconds", "max_retries", "retry_base_seconds",
    "requests_per_minute", "daily_request_limit", "daily_token_limit",
    "allow_streaming", "store_provider_responses", "is_enabled", "is_active",
)


class AIBudgetUnavailableError(AIConfigurationError):
    code = "ai_budget_unavailable"
    user_message = "AI spending needs an approved budget, billing company and current model price before it can run."


class AIBudgetLimitError(AIRateLimitError):
    code = "ai_budget_exceeded"
    user_message = "The remaining weekly AI allowance cannot cover this request. Core Tasker work is still available."


class AIBudgetAccountingError(AIProviderError):
    code = "ai_cost_unverified"
    user_message = "The AI charge could not be verified. Its reserved allowance remains held for review."


def enforced():
    # Only explicit local test settings bypass this. Production settings do not
    # offer an environment switch that silently restores unbudgeted AI calls.
    return bool(getattr(settings, "TASKER_AI_BUDGET_ENFORCED", True))


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def request_fingerprint(*, instructions, input_items, output_schema):
    return _fingerprint({"instructions": instructions, "input": input_items, "schema": output_schema or {}})


def configuration_fingerprint(configuration):
    return _fingerprint(_configuration(configuration))


def _decimal_string(value):
    amount = Decimal(str(value))
    return format(amount.normalize(), "f") if amount else "0"


def _configuration(configuration):
    result = {field: getattr(configuration, field) for field in CONFIGURATION_FIELDS}
    result["id"] = configuration.pk
    # DecimalField adds its configured scale on a database round-trip. A
    # semantically identical 1.0 / 1.00 must retain one configuration identity.
    result["temperature"] = _decimal_string(result["temperature"]) if result["temperature"] is not None else None
    result["retry_base_seconds"] = _decimal_string(result["retry_base_seconds"])
    result["system_override_fingerprint"] = _fingerprint(configuration.system_prompt_override)
    return result


def _policy_snapshot(policy, *, budget, enrollment):
    return {"currency": policy.currency, "timezone_name": policy.timezone_name,
            "week_start_day": policy.week_start_day, "reset_hour": policy.reset_hour,
            "reset_minute": policy.reset_minute, "employee_weekly_limit": str(policy.employee_weekly_limit),
            "company_budget_id": budget.pk, "company_weekly_limit": str(budget.weekly_limit),
            "charge_requester": budget.charge_requester, "enrollment_id": enrollment.pk,
            "billing_company_id": enrollment.company_id}


def _price_snapshot(card):
    return {"id": card.pk, "provider": card.provider, "model": card.model,
            "service_tier": card.service_tier, "version": card.version,
            "verified_at": card.verified_at.isoformat(), "valid_until": card.valid_until.isoformat(),
            "input": str(card.input_usd_per_million), "cached": str(card.cached_input_usd_per_million),
            "write": str(card.cache_write_usd_per_million) if card.cache_write_usd_per_million is not None else None,
            "output": str(card.output_usd_per_million), "capability_profile": card.capability_profile}


def _configured_policy(policy):
    if (policy is None or not policy.is_enabled or policy.currency != "USD"
            or not policy.timezone_name or type(policy.week_start_day) is not int
            or not 0 <= policy.week_start_day <= 6 or type(policy.reset_hour) is not int
            or not 0 <= policy.reset_hour <= 23 or type(policy.reset_minute) is not int
            or not 0 <= policy.reset_minute <= 59 or not ZERO <= policy.employee_weekly_limit <= EMPLOYEE_MAXIMUM):
        raise AIBudgetUnavailableError()


def week_bounds(policy, now=None):
    """One explicit calendar, shared by global employee and company ceilings."""
    _configured_policy(policy)
    now = now or timezone.now()
    if timezone.is_naive(now):
        raise AIBudgetUnavailableError()
    try:
        zone = ZoneInfo(policy.timezone_name)
        local = now.astimezone(zone)
        day = local.date() - timedelta(days=(local.weekday() - policy.week_start_day) % 7)
        boundary = datetime.combine(day, time(policy.reset_hour, policy.reset_minute), zone)
        if local < boundary:
            boundary = datetime.combine(day - timedelta(days=7), time(policy.reset_hour, policy.reset_minute), zone)
        end = datetime.combine(boundary.date() + timedelta(days=7), time(policy.reset_hour, policy.reset_minute), zone)
        for point in (boundary, end):
            if point.replace(fold=0).utcoffset() != point.replace(fold=1).utcoffset():
                raise AIBudgetUnavailableError()
            if point.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != point.replace(tzinfo=None):
                raise AIBudgetUnavailableError()
        return boundary.astimezone(datetime_timezone.utc), end.astimezone(datetime_timezone.utc)
    except (ValueError, KeyError) as exc:
        raise AIBudgetUnavailableError() from exc


def enrolled_company(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        return None
    enrollment = AIBudgetEnrollment.objects.select_related("company").filter(user=user, is_active=True, company__is_active=True).first()
    return enrollment.company if enrollment else None


def _totals(reservations):
    totals = AISpendAttempt.objects.filter(reservation__in=reservations).aggregate(spent=Sum("charged_amount"), reserved=Sum("held_amount"))
    return totals["spent"] or ZERO, totals["reserved"] or ZERO


def _amount(value):
    return format(value, "f")


def budget_status(user):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        raise PermissionDenied
    result = {"configured": False, "reason": "budget_unconfigured", "currency": None,
              "limit": None, "spent": None, "reserved": None, "remaining": None,
              "unknown_holds": 0, "period_start": None, "period_end": None, "company_id": None}
    policy = AIBudgetPolicy.objects.filter(key=1).first()
    try:
        start, end = week_bounds(policy)
    except AIBudgetUnavailableError:
        return result
    reservations = AISpendReservation.objects.filter(user=user, period_start=start, period_end=end)
    spent, reserved = _totals(reservations)
    enrollment = AIBudgetEnrollment.objects.filter(user=user, is_active=True, company__is_active=True).first()
    company_budget = AICompanyBudget.objects.filter(company_id=enrollment.company_id, is_enabled=True, charge_requester=True).first() if enrollment else None
    result.update(currency=policy.currency, limit=_amount(policy.employee_weekly_limit), spent=_amount(spent),
                  reserved=_amount(reserved), remaining=_amount(max(ZERO, policy.employee_weekly_limit-spent-reserved)),
                  unknown_holds=AISpendAttempt.objects.filter(reservation__in=reservations, status=AISpendAttempt.Status.UNKNOWN).count(),
                  period_start=start.isoformat(), period_end=end.isoformat(), company_id=enrollment.company_id if enrollment else None)
    result["configured"] = (company_budget is not None and company_budget.weekly_limit is not None
                            and ZERO <= company_budget.weekly_limit <= COMPANY_MAXIMUM)
    result["reason"] = "" if result["configured"] else "billing_company_unconfigured"
    if (AISpendReservation.objects.filter(user=user, requires_review=True).exists()
            or (enrollment and AISpendReservation.objects.filter(company_id=enrollment.company_id, requires_review=True).exists())):
        result.update(configured=False, reason="charge_review_required")
    return result


def company_budget_status(user, company):
    if not getattr(user, "is_authenticated", False) or not user.is_active:
        raise PermissionDenied
    if not user.is_superuser:
        from tasker.organization_models import OrganizationAccess
        permitted = user.has_perm("tasker.manage_ai_configuration") and OrganizationAccess.objects.filter(
            user=user, company=company, is_active=True, can_manage=True, company__is_active=True).exists()
        if not permitted:
            raise PermissionDenied
    policy = AIBudgetPolicy.objects.filter(key=1).first()
    try:
        start, end = week_bounds(policy)
    except AIBudgetUnavailableError:
        return {"configured": False, "company_id": company.pk, "reason": "budget_unconfigured"}
    budget = AICompanyBudget.objects.filter(company=company).first()
    reservations = AISpendReservation.objects.filter(company=company, period_start=start, period_end=end)
    spent, reserved = _totals(reservations)
    limit = budget.weekly_limit if budget else None
    review = AISpendReservation.objects.filter(company=company, requires_review=True).exists()
    configured = bool(company.is_active and budget and budget.is_enabled and budget.charge_requester
                      and limit is not None and ZERO <= limit <= COMPANY_MAXIMUM and not review)
    return {"configured": configured, "reason": "" if configured else "charge_review_required" if review else "billing_company_unconfigured",
            "company_id": company.pk, "currency": policy.currency, "limit": _amount(limit) if limit is not None else None,
            "spent": _amount(spent), "reserved": _amount(reserved),
            "remaining": _amount(max(ZERO, limit-spent-reserved)) if limit is not None else None,
            "period_start": start.isoformat(), "period_end": end.isoformat(),
            "requests": reservations.count(), "unknown_holds": AISpendAttempt.objects.filter(reservation__in=reservations, status=AISpendAttempt.Status.UNKNOWN).count()}


class MoneyBudget:
    """Every method uses the same stable lock order as token governance."""

    @staticmethod
    def _lock_user(user, *, admission=False):
        actor = get_user_model()._default_manager.select_for_update().get(pk=user.pk)
        if admission and (not actor.is_active or not actor.has_perm("tasker.use_ai")):
            raise AIBudgetUnavailableError()
        return actor

    @staticmethod
    def _lock_policy():
        return AIBudgetPolicy.objects.select_for_update().filter(key=1).first()

    @staticmethod
    def _company_budget(user):
        enrollment = AIBudgetEnrollment.objects.select_for_update().select_related("company").filter(user=user, is_active=True, company__is_active=True).first()
        if enrollment is None:
            raise AIBudgetUnavailableError()
        budget = AICompanyBudget.objects.select_for_update().filter(company=enrollment.company).first()
        if (budget is None or not budget.is_enabled or budget.weekly_limit is None
                or not ZERO <= budget.weekly_limit <= COMPANY_MAXIMUM or not budget.charge_requester):
            raise AIBudgetUnavailableError()
        return enrollment, budget

    @staticmethod
    def _capacity(user, company, policy, budget, start, end, amount):
        if AISpendReservation.objects.filter(user=user, requires_review=True).exists() or AISpendReservation.objects.filter(company=company, requires_review=True).exists():
            raise AIBudgetUnavailableError()
        user_rows = AISpendReservation.objects.filter(user=user, period_start=start, period_end=end)
        company_rows = AISpendReservation.objects.filter(company=company, period_start=start, period_end=end)
        user_spent, user_held = _totals(user_rows)
        company_spent, company_held = _totals(company_rows)
        if user_spent+user_held+amount > policy.employee_weekly_limit or company_spent+company_held+amount > budget.weekly_limit:
            raise AIBudgetLimitError(retry_after=max(1, int((end-timezone.now()).total_seconds())))

    @staticmethod
    def _quote(price, input_bound, output_bound):
        with localcontext() as context:
            context.prec = 60
            input_rate = max(Decimal(price["input"]), Decimal(price["cached"]), Decimal(price["write"]) if price["write"] is not None else ZERO)
            return ((Decimal(input_bound)*input_rate + Decimal(output_bound)*Decimal(price["output"])) / Decimal(1_000_000)).quantize(UNIT)

    @transaction.atomic
    def admit(self, *, user, configuration, usage_log, input_bound, output_bound, prompt_fingerprint):
        if not enforced():
            return None
        self._lock_user(user, admission=True)
        policy = self._lock_policy()
        start, end = week_bounds(policy)
        enrollment, budget = self._company_budget(user)
        if (usage_log.user_id != user.pk or usage_log.configuration_id != configuration.pk
                or type(input_bound) is not int or type(output_bound) is not int or input_bound < 1
                or output_bound < 1 or output_bound != configuration.max_output_tokens):
            raise AIBudgetUnavailableError()
        card = AIPriceCard.objects.select_for_update().filter(provider=configuration.provider, model=configuration.model,
            service_tier="default", is_active=True, is_verified=True).first()
        now = timezone.now()
        if (card is None or card.verified_at is None or card.valid_until is None
                or card.verified_at > now or card.valid_until <= now
                or input_bound > card.max_input_tokens or output_bound > card.max_output_tokens
                or configuration.store_provider_responses):
            raise AIBudgetUnavailableError()
        require_preflight(preflight_configuration(configuration, profile=card.capability_profile, pricing_verified=True))
        price = _price_snapshot(card)
        quote = self._quote(price, input_bound, output_bound)
        self._capacity(user, enrollment.company, policy, budget, start, end, quote)
        snapshot = _configuration(configuration)
        reservation = AISpendReservation.objects.create(
            usage_log=usage_log, user=user, company=enrollment.company, policy=policy, price_card=card,
            admitted_at=now, period_start=start, period_end=end, configuration_snapshot=snapshot,
            price_snapshot=price, policy_snapshot=_policy_snapshot(policy, budget=budget, enrollment=enrollment), prompt_fingerprint=prompt_fingerprint,
            configuration_fingerprint=_fingerprint(snapshot), input_bound=input_bound, output_bound=output_bound,
            max_attempts=configuration.max_retries+1,
        )
        AISpendAttempt.objects.create(reservation=reservation, number=1, held_amount=quote)
        return reservation

    @transaction.atomic
    def begin_attempt(self, *, user, configuration, usage_log):
        if not enforced():
            return None
        self._lock_user(user, admission=True)
        policy = self._lock_policy()
        start, end = week_bounds(policy)
        enrollment, budget = self._company_budget(user)
        reservation = AISpendReservation.objects.select_for_update().filter(usage_log=usage_log, user=user).first()
        if (reservation is None or reservation.settled_at is not None or reservation.company_id != enrollment.company_id
                or reservation.period_start != start or reservation.period_end != end):
            raise AIBudgetUnavailableError()
        from tasker.models import AIConfiguration
        fresh = AIConfiguration.objects.filter(pk=configuration.pk, is_enabled=True, is_active=True).first()
        if fresh is None or _fingerprint(_configuration(configuration)) != reservation.configuration_fingerprint or _fingerprint(_configuration(fresh)) != reservation.configuration_fingerprint:
            raise AIBudgetUnavailableError()
        card = AIPriceCard.objects.select_for_update().get(pk=reservation.price_card_id)
        if not card.is_active or not card.is_verified or card.valid_until is None or card.valid_until <= timezone.now():
            raise AIBudgetUnavailableError()
        last = reservation.attempts.select_for_update().order_by("-number").first()
        if last is None:
            raise AIBudgetUnavailableError()
        if last.status == AISpendAttempt.Status.RESERVED:
            # Its admission hold already counts against both ceilings. Recheck
            # current limits/revocation without reserving the same money twice.
            self._capacity(user, enrollment.company, policy, budget, start, end, ZERO)
        else:
            if last.status != AISpendAttempt.Status.DISPATCHED or last.number >= reservation.max_attempts:
                raise AIBudgetUnavailableError()
            quote = self._quote(reservation.price_snapshot, reservation.input_bound, reservation.output_bound)
            self._capacity(user, enrollment.company, policy, budget, start, end, quote)
            last.status = AISpendAttempt.Status.UNKNOWN
            last.failure_code = "attempt_outcome_unknown"
            last.settled_at = timezone.now()
            last.save(update_fields=["status", "failure_code", "settled_at"])
            last = AISpendAttempt.objects.create(reservation=reservation, number=last.number+1, held_amount=quote)
        last.status = AISpendAttempt.Status.DISPATCHED
        last.started_at = timezone.now()
        last.save(update_fields=["status", "started_at"])
        return last

    @staticmethod
    def _usage_cost(reservation, usage, metadata):
        price = reservation.price_snapshot
        if not isinstance(metadata, dict) or metadata.get("model") != price["model"] or metadata.get("service_tier") != price["service_tier"]:
            return None, "model_or_tier_unverified", True, {}
        verified = metadata.get("usage_validated") is True or metadata.get("usage_verified") is True
        if metadata.get("usage_available") is not True or not verified:
            return None, "usage_unavailable", False, {}
        names = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "cache_write_tokens")
        values = {name: getattr(usage, name, 0 if name == "cache_write_tokens" else None) for name in names}
        if any(type(value) is not int or value < 0 for value in values.values()) or values["input_tokens"]+values["output_tokens"] == 0:
            return None, "usage_invalid", True, {}
        ordinary = values["input_tokens"]-values["cached_input_tokens"]-values["cache_write_tokens"]
        if ordinary < 0 or values["reasoning_tokens"] > values["output_tokens"] or (values["cache_write_tokens"] and price["write"] is None):
            return None, "usage_invalid", True, values
        with localcontext() as context:
            context.prec = 60
            amount = ((Decimal(ordinary)*Decimal(price["input"]) + Decimal(values["cached_input_tokens"])*Decimal(price["cached"])
                       + Decimal(values["cache_write_tokens"])*(Decimal(price["write"]) if price["write"] is not None else ZERO)
                       + Decimal(values["output_tokens"])*Decimal(price["output"])) / Decimal(1_000_000)).quantize(UNIT)
        outside = values["input_tokens"] > reservation.input_bound or values["output_tokens"] > reservation.output_bound
        return amount, "usage_out_of_bounds" if outside else "", outside, values

    @transaction.atomic
    def settle(self, *, user, usage_log, usage=None, metadata=None):
        if not enforced():
            return {"safe": True, "code": ""}
        self._lock_user(user)
        self._lock_policy()
        reservation = AISpendReservation.objects.select_for_update().filter(usage_log=usage_log, user=user).first()
        if reservation is None:
            return {"safe": True, "code": ""}
        if reservation.settled_at is not None:
            return {"safe": not bool(reservation.accounting_code), "code": reservation.accounting_code}
        attempt = reservation.attempts.select_for_update().order_by("-number").first()
        code, review = "", False
        if attempt and attempt.status == AISpendAttempt.Status.RESERVED:
            attempt.status = AISpendAttempt.Status.RELEASED
            attempt.held_amount = ZERO
        elif attempt and attempt.status == AISpendAttempt.Status.DISPATCHED:
            if usage is None:
                amount, code, review, values = None, "attempt_outcome_unknown", False, {}
            else:
                amount, code, review, values = self._usage_cost(reservation, usage, metadata)
            attempt.usage_snapshot = values
            attempt.failure_code = code
            if amount is None:
                attempt.status = AISpendAttempt.Status.UNKNOWN
            else:
                if amount > attempt.held_amount:
                    code, review = "charge_exceeded_reservation", True
                    attempt.failure_code = code
                attempt.status = AISpendAttempt.Status.KNOWN
                attempt.charged_amount = amount
                attempt.held_amount = ZERO
        if attempt:
            attempt.settled_at = timezone.now()
            attempt.save(update_fields=["status", "held_amount", "charged_amount", "usage_snapshot", "failure_code", "settled_at"])
        reservation.settled_at = timezone.now()
        reservation.requires_review = review
        reservation.accounting_code = code
        reservation.save(update_fields=["settled_at", "requires_review", "accounting_code"])
        return {"safe": not bool(code), "code": code}
