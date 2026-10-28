from __future__ import annotations

import json
from datetime import datetime, time, timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from tasker.models import AIUsageLog

from .exceptions import AIConfigurationError, AIRateLimitError


class AIRateLimiter:
    """Durable per-user admission, serialized by a short user-row lock.

    One admitted logical request consumes one minute/day request allowance.
    Each provider attempt consumes token capacity, including retries. Before
    I/O the reserved attempt budget becomes a conservative charge; only a
    measured final response refunds the difference. Unknown billed usage is
    never silently treated as free. Tokens are attributed to admission day.
    No transaction here spans provider calls, streaming or retry sleeps.
    """

    TOKEN_FRAMING_ALLOWANCE = 1024

    @classmethod
    def estimate_token_budget(cls, *, instructions, input_items, output_schema, configuration):
        # For this text-only provider, UTF-8 bytes conservatively bound content
        # tokens. Include schema/roles/structure and a separate framing margin;
        # this deliberately avoids the optimistic characters/4 heuristic.
        serialized = json.dumps(
            {"instructions": instructions, "input": input_items, "schema": output_schema or {}},
            ensure_ascii=False, separators=(",", ":"),
        )
        return len(serialized.encode("utf-8")) + cls.TOKEN_FRAMING_ALLOWANCE + configuration.max_output_tokens

    @transaction.atomic
    def check(self, *, user, configuration, usage_log=None, token_budget=0):
        self._lock_user(user)
        now = timezone.now()
        start, end = self._day_bounds(now)
        logs = AIUsageLog.all_objects.filter(user=user, quota_reserved_at__gte=start, quota_reserved_at__lt=end)
        retry_after = max(1, int((end - now).total_seconds()))
        if logs.count() >= configuration.daily_request_limit:
            raise AIRateLimitError("Daily AI request limit reached.", retry_after=retry_after)
        minute_start = now.replace(second=0, microsecond=0)
        if AIUsageLog.all_objects.filter(user=user, quota_reserved_at__gte=minute_start).count() >= configuration.requests_per_minute:
            raise AIRateLimitError("Per-minute AI request limit reached.", retry_after=max(1, 60 - now.second))
        self._require_tokens(logs, token_budget, configuration, retry_after)
        if usage_log is None:
            # Preserve check-only callers through the same durable admission.
            usage_log = AIUsageLog.objects.create(user=user, configuration=configuration, operation="quota-check")
        log = AIUsageLog.all_objects.select_for_update().get(pk=usage_log.pk, user=user)
        if log.quota_reserved_at is not None:
            raise AIConfigurationError("This request already has a quota reservation.")
        log.quota_reserved_at = now
        log.quota_reserved_tokens = token_budget
        log.quota_attempt_token_budget = token_budget
        log.quota_settled_at = now if not token_budget else None
        log.save(update_fields=["quota_reserved_at", "quota_reserved_tokens", "quota_attempt_token_budget", "quota_settled_at", "updated_at"])
        return log

    @transaction.atomic
    def begin_attempt(self, *, user, configuration, usage_log):
        self._lock_user(user)
        log = AIUsageLog.all_objects.select_for_update().get(pk=usage_log.pk, user=user)
        if log.quota_reserved_at is None or log.quota_settled_at is not None:
            raise AIConfigurationError("The AI quota reservation is unavailable.")
        budget = log.quota_attempt_token_budget
        if log.quota_reserved_tokens:
            log.quota_reserved_tokens = 0
        else:
            start, end = self._day_bounds(log.quota_reserved_at)
            logs = AIUsageLog.all_objects.filter(user=user, quota_reserved_at__gte=start, quota_reserved_at__lt=end)
            self._require_tokens(logs, budget, configuration, max(1, int((end - timezone.now()).total_seconds())))
        log.quota_charged_tokens += budget
        log.quota_provider_attempts += 1
        log.quota_usage_estimated = True
        log.save(update_fields=["quota_reserved_tokens", "quota_charged_tokens", "quota_provider_attempts", "quota_usage_estimated", "updated_at"])
        return log

    @transaction.atomic
    def settle(self, *, user, usage_log, usage=None):
        self._lock_user(user)
        log = AIUsageLog.all_objects.select_for_update().get(pk=usage_log.pk, user=user)
        if log.quota_reserved_at is None or log.quota_settled_at is not None:
            return log
        measured = self._measured_tokens(usage)
        if measured is not None and log.quota_provider_attempts:
            log.quota_charged_tokens = max(0, log.quota_charged_tokens - log.quota_attempt_token_budget) + measured
            log.quota_usage_estimated = log.quota_provider_attempts > 1
        log.quota_reserved_tokens = 0
        log.quota_settled_at = timezone.now()
        log.save(update_fields=["quota_reserved_tokens", "quota_charged_tokens", "quota_usage_estimated", "quota_settled_at", "updated_at"])
        return log

    @staticmethod
    def _measured_tokens(usage):
        if usage is None:
            return None
        values = (getattr(usage, "input_tokens", None), getattr(usage, "output_tokens", None))
        if any(type(value) is not int or value < 0 for value in values) or sum(values) == 0:
            # Zero is the adapter's missing-usage default, not proof of no bill.
            return None
        return sum(values)

    @staticmethod
    def _require_tokens(logs, token_budget, configuration, retry_after):
        if type(token_budget) is not int or token_budget < 0:
            raise AIConfigurationError("The AI token reservation is invalid.")
        totals = logs.aggregate(charged=Sum("quota_charged_tokens"), reserved=Sum("quota_reserved_tokens"))
        used = (totals["charged"] or 0) + (totals["reserved"] or 0)
        if used + token_budget > configuration.daily_token_limit or (not token_budget and used >= configuration.daily_token_limit):
            raise AIRateLimitError("Daily AI token limit reached.", retry_after=retry_after)

    @staticmethod
    def _lock_user(user):
        # A stable row exists even when no request has been recorded yet.
        get_user_model()._default_manager.select_for_update().get(pk=user.pk)

    @staticmethod
    def _day_bounds(moment=None):
        today = timezone.localdate(moment)
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(today, time.min), zone)
        end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), zone)
        return start, end

