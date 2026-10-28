"""One local-day convention for personal planning and the Today dashboard."""
from zoneinfo import ZoneInfo
from django.utils import timezone
from tasker.models import WorkPolicy


def work_zone(user):
    policy=WorkPolicy.objects.filter(owner=user).first()
    return ZoneInfo(policy.timezone_name if policy else "Africa/Harare")


def work_date(user, *, at=None):
    return (at or timezone.now()).astimezone(work_zone(user)).date()
