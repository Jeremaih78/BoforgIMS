from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied

from core.permissions import allowed
from inventory.replenishment_policies import company_ids, current_actor
from .models import Responsibility
from .registry import SOURCES, spec_for


def capable(user, kind, *, work=False):
    spec = spec_for(kind)
    return allowed(user, 'operations.view_responsibility') and allowed(user, spec.permission) and (
        not work or allowed(user, spec.work_permission))


def visible(user, kind=None):
    from django.db.models import Q
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return Responsibility.objects.none()
    kinds = [k for k in SOURCES if (kind is None or kind == k) and capable(actor,k)]
    rows = Responsibility.objects.filter(kind__in=kinds, company_id__in=company_ids(actor))
    # Native company changes invalidate the old explicit binding immediately.
    return rows.filter((~Q(kind='bill_work') | Q(bill__company_id=models_f('company_id'))) &
        (~Q(kind='period_work') | Q(period__company_id=models_f('company_id'))))


def models_f(field):
    from django.db.models import F
    return F(field)


def can_manage(user, kind, company_id, team_id=None):
    from tasker.organization import can_manage_team
    from tasker.models import Team
    scope=(company_ids(user,manage=True).filter(pk=company_id).exists() or (
        team_id and Team.objects.filter(pk=team_id,company_id=company_id,is_deleted=False).exists()
        and can_manage_team(user,team_id)))
    return bool(capable(user,kind) and allowed(user,'operations.manage_responsibilities') and scope)


def setup_companies(user):
    from accounting.models import Company
    from django.db.models import Q
    from tasker.organization import managed_teams_for
    return Company.objects.filter(is_active=True).filter(Q(pk__in=company_ids(user,manage=True)) |
        Q(pk__in=managed_teams_for(user).values('company_id'))).order_by('name')


def can_setup_company(user,kind,company_id):
    return bool(capable(user,kind) and allowed(user,'operations.manage_responsibilities')
        and setup_companies(user).filter(pk=company_id).exists())


def eligible(user, kind, company_id, team_id=None):
    from tasker.models import TeamMembership
    ids = []
    for actor in get_user_model().objects.filter(is_active=True).iterator():
        if not capable(actor,kind,work=True) or not company_ids(actor).filter(pk=company_id).exists():
            continue
        if team_id and not TeamMembership.objects.filter(user=actor,team_id=team_id,is_active=True,
                is_deleted=False,team__is_deleted=False).exists():
            continue
        ids.append(actor.pk)
    return get_user_model().objects.filter(pk__in=ids).order_by('username')
