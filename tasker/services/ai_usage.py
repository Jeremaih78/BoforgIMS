from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Avg, Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from tasker.models import AIConfiguration, AIUsageLog, PromptTemplate


@dataclass(frozen=True)
class AIUsageDashboardData:
    configuration: object
    active_prompt_count: int
    period_start: object
    total_requests: int
    successful_requests: int
    failed_requests: int
    active_users: int
    total_tokens: int
    average_latency_ms: int
    success_rate: int
    daily_usage: tuple
    model_usage: tuple
    operation_usage: tuple
    recent_logs: tuple


def build_ai_usage_dashboard(*, days=30):
    start = timezone.now() - timedelta(days=days)
    logs = AIUsageLog.objects.filter(started_at__gte=start)
    totals = logs.aggregate(
        total=Count("id"),
        successful=Count("id", filter=Q(status=AIUsageLog.Status.SUCCEEDED)),
        failed=Count("id", filter=Q(status__in=(AIUsageLog.Status.FAILED, AIUsageLog.Status.INTERRUPTED))),
        users=Count("user", distinct=True),
        tokens=Sum("total_tokens"),
        latency=Avg("latency_ms", filter=Q(latency_ms__isnull=False)),
    )
    total = totals["total"] or 0
    successful = totals["successful"] or 0
    daily = tuple(
        logs.annotate(day=TruncDate("started_at"))
        .values("day")
        .annotate(requests=Count("id"), tokens=Sum("total_tokens"))
        .order_by("day")
    )
    models = tuple(
        logs.values("model").annotate(requests=Count("id"), tokens=Sum("total_tokens")).order_by("-tokens")[:10]
    )
    operations = tuple(
        logs.values("operation").annotate(requests=Count("id"), tokens=Sum("total_tokens")).order_by("-requests")[:10]
    )
    recent = tuple(logs.select_related("user").order_by("-started_at")[:25])
    return AIUsageDashboardData(
        configuration=AIConfiguration.objects.filter(is_active=True).first(),
        active_prompt_count=PromptTemplate.objects.filter(is_active=True).count(),
        period_start=start,
        total_requests=total,
        successful_requests=successful,
        failed_requests=totals["failed"] or 0,
        active_users=totals["users"] or 0,
        total_tokens=totals["tokens"] or 0,
        average_latency_ms=round(totals["latency"] or 0),
        success_rate=round((successful / total) * 100) if total else 0,
        daily_usage=daily,
        model_usage=models,
        operation_usage=operations,
        recent_logs=recent,
    )
