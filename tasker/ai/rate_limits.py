from __future__ import annotations

from datetime import datetime, time, timedelta

from django.core.cache import cache
from django.db.models import Sum
from django.utils import timezone

from tasker.models import AIUsageLog

from .exceptions import AIRateLimitError


class AIRateLimiter:
    """Per-minute cache throttle plus durable daily request/token quotas."""

    def check(self, *, user, configuration):
        self._check_minute(user=user, configuration=configuration)
        start, end = self._day_bounds()
        logs = AIUsageLog.objects.filter(
            user=user,
            started_at__gte=start,
            started_at__lt=end,
        ).exclude(status__in=(AIUsageLog.Status.DISABLED, AIUsageLog.Status.RATE_LIMITED))
        if logs.count() >= configuration.daily_request_limit:
            raise AIRateLimitError("Daily AI request limit reached.", retry_after=int((end - timezone.now()).total_seconds()))
        used_tokens = logs.aggregate(total=Sum("total_tokens"))["total"] or 0
        if used_tokens >= configuration.daily_token_limit:
            raise AIRateLimitError("Daily AI token limit reached.", retry_after=int((end - timezone.now()).total_seconds()))

    @staticmethod
    def _check_minute(*, user, configuration):
        minute = timezone.now().strftime("%Y%m%d%H%M")
        key = f"tasker:ai:rate:{user.pk}:{minute}"
        if cache.add(key, 1, timeout=75):
            count = 1
        else:
            try:
                count = cache.incr(key)
            except ValueError:
                cache.set(key, 1, timeout=75)
                count = 1
        if count > configuration.requests_per_minute:
            raise AIRateLimitError("Per-minute AI request limit reached.", retry_after=60)

    @staticmethod
    def _day_bounds():
        today = timezone.localdate()
        zone = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(today, time.min), zone)
        return start, start + timedelta(days=1)

