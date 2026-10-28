"""Explicit arrival declarations; no login, timer, payroll or presence inference."""
import uuid
from datetime import datetime, timezone as datetime_timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Prefetch, Q
from django.utils import timezone

from accounting.models import Company
from tasker.arrival_models import ArrivalCorrection, ArrivalDeclaration
from tasker.models import Team, TeamMembership
from tasker.organization import arrival_can_read, company_read_ids, managed_teams_for


def _actor(user, *, lock=False):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    queryset = get_user_model().objects
    if lock:
        queryset = queryset.select_for_update(no_key=True)
    actor = queryset.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def current_business_zone(user):
    """Use configured local planning policy, with the explicit Harare fallback."""
    name = "Africa/Harare"
    try:
        policy_model = apps.get_model("tasker", "WorkPolicy")
    except LookupError:
        policy_model = None
    if policy_model is not None:
        policy = policy_model.objects.filter(owner_id=user.pk).first()
        if policy and policy.timezone_name:
            name = policy.timezone_name
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValidationError("Your work timezone needs a valid configuration before registering arrival.") from None


def today_arrival(user):
    actor = _actor(user)
    local_date = timezone.now().astimezone(current_business_zone(actor)).date()
    return with_history(ArrivalDeclaration.objects.filter(user=actor, local_date=local_date)).first()


def with_history(queryset):
    return queryset.select_related("user").prefetch_related("teams", Prefetch(
        "corrections", queryset=ArrivalCorrection.objects.select_related("actor").order_by("-created_at", "-pk"),
        to_attr="correction_history"))


def arrival_teams_for(user):
    actor = _actor(user)
    return Team.objects.filter(company__is_active=True).filter(
        Q(pk__in=managed_teams_for(actor).values("pk")) | Q(company_id__in=company_read_ids(actor))).distinct()


def visible_arrivals_for(user, *, team=None, local_date=None):
    """Recorded team AND current employee membership are required for team reads."""
    actor = _actor(user)
    queryset = ArrivalDeclaration.objects.all()
    if team is not None:
        team_id = getattr(team, "pk", team)
        if not arrival_teams_for(actor).filter(pk=team_id).exists():
            raise PermissionDenied
        queryset = queryset.filter(teams__pk=team_id, teams__memberships__user_id=F("user_id"),
            teams__memberships__is_active=True, teams__memberships__is_deleted=False, user__is_active=True)
    else:
        queryset = queryset.filter(user=actor)
    if local_date is not None:
        queryset = queryset.filter(local_date=local_date)
    return with_history(queryset.distinct())


def can_read_arrival(user, arrival):
    actor = _actor(user)
    if actor.pk == arrival.user_id:
        return True
    return any(arrival_can_read(actor, arrival.user_id, team.pk) for team in arrival.teams.all())


@transaction.atomic
def register_arrival(*, user):
    actor = _actor(user, lock=True)
    now = timezone.now()
    zone = current_business_zone(actor)
    local_date = now.astimezone(zone).date()
    existing = ArrivalDeclaration.objects.filter(user=actor, local_date=local_date).first()
    if existing:
        return existing, False
    membership_query = TeamMembership.objects.filter(user=actor, is_active=True,
        team__is_deleted=False, team__company__is_active=True)
    company_ids = list(membership_query.values_list("team__company_id", flat=True).distinct())
    list(Company.objects.select_for_update().filter(pk__in=company_ids).order_by("pk"))
    team_ids = list(membership_query.filter(team__company_id__in=company_ids).values_list("team_id", flat=True))
    list(Team.objects.select_for_update().filter(pk__in=team_ids).order_by("pk"))
    team_ids = list(membership_query.filter(team_id__in=team_ids).values_list("team_id", flat=True))
    record = ArrivalDeclaration.objects.create(user=actor, local_date=local_date,
        timezone_name=zone.key, registered_at=now)
    record.teams.set(team_ids)
    return record, True


def declared_datetime(arrival, declared_time):
    zone = ZoneInfo(arrival.timezone_name)
    naive = datetime.combine(arrival.local_date, declared_time)
    value = naive.replace(tzinfo=zone)
    if value.astimezone(datetime_timezone.utc).astimezone(zone).replace(tzinfo=None) != naive:
        raise ValidationError("That local time does not exist in the recorded timezone.")
    return value


@transaction.atomic
def correct_arrival(*, user, arrival_id, effective_at, reason, client_key, expected_revision):
    actor = _actor(user, lock=True)
    arrival = ArrivalDeclaration.objects.select_for_update().filter(pk=arrival_id, user=actor).first()
    if arrival is None:
        raise PermissionDenied
    try:
        key = uuid.UUID(str(client_key))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError("Reload to obtain a correction identifier.") from None
    reason = reason.strip() if isinstance(reason, str) else ""
    existing = arrival.corrections.filter(client_key=key).first()
    if existing:
        if existing.effective_at == effective_at and existing.reason == reason:
            return existing
        raise ValidationError("This correction identifier was already used. Reload before adding another correction.")
    if type(expected_revision) is not int or expected_revision != arrival.revision:
        raise ValidationError("This declaration was corrected in another window. Review its latest history before correcting it.")
    correction = ArrivalCorrection(arrival=arrival, actor=actor, client_key=key,
        effective_at=effective_at, reason=reason, created_at=timezone.now())
    correction.save()
    return correction
