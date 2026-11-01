"""Inventory capabilities and explicit company scope govern procurement reviews."""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from accounting.models import Company
from core.permissions import allowed
from .replenishment_models import ReplenishmentPlan


def current_actor(user):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None:
        raise PermissionDenied
    return actor


def operational_admin(user):
    return bool(user.is_active and (user.is_superuser or user.groups.filter(name="Admin").exists()))


def source_read_capable(user):
    return (allowed(user, "inventory.view_replenishmentplan", staff=False)
        and allowed(user, "inventory.view_product", staff=True))


def company_ids(user, *, manage=False):
    from tasker.models import OrganizationAccess, TeamMembership
    if operational_admin(user):
        return Company.objects.filter(is_active=True).values_list("pk", flat=True)
    grants = OrganizationAccess.objects.filter(user=user, is_active=True, is_deleted=False,
        company__is_active=True)
    if manage:
        return Company.objects.filter(is_active=True, pk__in=grants.filter(can_manage=True).values(
            "company_id")).values_list("pk", flat=True)
    memberships = TeamMembership.objects.filter(user=user, is_active=True, is_deleted=False,
        team__is_deleted=False, team__company__is_active=True).values("team__company_id")
    return Company.objects.filter(is_active=True).filter(Q(pk__in=memberships) | Q(pk__in=grants.filter(
        Q(can_manage=True) | Q(can_view_all=True)).values("company_id"))).values_list("pk", flat=True)


def visible_companies_for(user, *, manage=False):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return Company.objects.none()
    if not source_read_capable(actor):
        return Company.objects.none()
    if manage and not allowed(actor, "inventory.manage_replenishment", staff=False):
        return Company.objects.none()
    return Company.objects.filter(pk__in=company_ids(actor, manage=manage))


def visible_plans_for(user):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return ReplenishmentPlan.objects.none()
    if not source_read_capable(actor):
        return ReplenishmentPlan.objects.none()
    return ReplenishmentPlan.objects.filter(company_id__in=company_ids(actor))


def can_read_plan(user, plan):
    return visible_plans_for(user).filter(pk=plan.pk).exists()


def can_manage_company(user, company):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return False
    company_id = getattr(company, "pk", company)
    return bool(company_id and source_read_capable(actor)
        and allowed(actor, "inventory.manage_replenishment", staff=False)
        and Company.objects.filter(pk=company_id, is_active=True).exists()
        and company_ids(actor, manage=True).filter(pk=company_id).exists())


def can_manage_plan(user, plan):
    return can_read_plan(user, plan) and can_manage_company(user, plan.company_id)


def can_change_plan(user, plan):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return False
    return bool(can_read_plan(actor, plan)
        and allowed(actor, "inventory.change_replenishmentplan", staff=False)
        and (plan.assigned_to_id == actor.pk or can_manage_company(actor, plan.company_id)))


def eligible_assignees(user, company_id):
    from tasker.models import OrganizationAccess, TeamMembership
    if not can_manage_company(user, company_id):
        return get_user_model().objects.none()
    members = TeamMembership.objects.filter(team__company_id=company_id, team__is_deleted=False,
        team__company__is_active=True, is_active=True, is_deleted=False).values("user_id")
    grants = OrganizationAccess.objects.filter(company_id=company_id, company__is_active=True,
        is_active=True, is_deleted=False).filter(Q(can_manage=True) | Q(can_view_all=True)).values("user_id")
    users = get_user_model().objects.filter(is_active=True).filter(Q(pk__in=members) | Q(pk__in=grants)
        | Q(is_superuser=True) | Q(groups__name="Admin")).distinct()
    eligible = [actor.pk for actor in users.iterator() if source_read_capable(actor)
        and allowed(actor, "inventory.change_replenishmentplan", staff=False)]
    return get_user_model().objects.filter(pk__in=eligible).order_by("first_name", "username", "pk")
