"""Canonical collections scope, shared by IMS and Tasker's source adapters.

Tasker capabilities never substitute for Credit Control permission. Company
membership/grants must be current; null-company legacy work stays owner-only.
"""
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied
from django.db.models import Q

from core.permissions import allowed
from .models import CollectionTask, PromiseToPay


def current_actor(user):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None:
        raise PermissionDenied
    return actor


def operational_admin(user):
    return bool(user.is_active and (user.is_superuser or user.groups.filter(name="Admin").exists()))


def source_company_ids(user, *, manage=False):
    from accounting.models import Company
    from tasker.models import OrganizationAccess, TeamMembership
    if operational_admin(user):
        return Company.objects.filter(is_active=True).values("pk")
    grants = OrganizationAccess.objects.filter(user=user, is_active=True, is_deleted=False, company__is_active=True)
    if manage:
        grants = grants.filter(can_manage=True)
        return grants.values("company_id")
    membership = TeamMembership.objects.filter(user=user, is_active=True, is_deleted=False,
        team__is_deleted=False, team__company__is_active=True).values("team__company_id")
    return Company.objects.filter(is_active=True).filter(
        Q(pk__in=membership) | Q(pk__in=grants.filter(Q(can_view_all=True) | Q(can_manage=True)).values("company_id"))).values("pk")


def _visible(user, model, permission):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return model.objects.none()
    if not allowed(actor, permission, staff=False):
        return model.objects.none()
    if operational_admin(actor):
        return model.objects.filter(Q(company__isnull=True) | Q(company__is_active=True))
    return model.objects.filter(Q(company_id__in=source_company_ids(actor)) | Q(company__isnull=True, assigned_to=actor))


def visible_collection_tasks_for(user):
    return _visible(user, CollectionTask, "credit_control.view_collectiontask")


def visible_promises_for(user):
    return _visible(user, PromiseToPay, "credit_control.view_promisetopay")


def can_read_collection_task(user, item):
    return visible_collection_tasks_for(user).filter(pk=item.pk).exists()


def can_read_promise(user, item):
    return visible_promises_for(user).filter(pk=item.pk).exists()


def source_manager(user, company_id):
    actor = current_actor(user)
    if operational_admin(actor):
        return True
    if company_id is None:
        return False
    if not source_company_ids(actor).filter(pk=company_id).exists():
        return False
    return (source_company_ids(actor, manage=True).filter(company_id=company_id).exists()
        or actor.has_perm("credit_control.credit_control_finance_manager"))


def can_change_source(user, item):
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return False
    model_name = "collectiontask" if isinstance(item, CollectionTask) else "promisetopay"
    readable = can_read_collection_task(actor, item) if model_name == "collectiontask" else can_read_promise(actor, item)
    return bool(readable and allowed(actor, "credit_control.change_"+model_name, staff=False)
        and (item.assigned_to_id == actor.pk or source_manager(actor, item.company_id)))


def source_assignees(user, company_id, *, permission, additional_permissions=()):
    """Existing active company membership qualifies scope, not financial skill."""
    from tasker.models import OrganizationAccess, TeamMembership
    actor = current_actor(user)
    if not source_manager(actor, company_id):
        return get_user_model().objects.none()
    users = get_user_model().objects.filter(is_active=True)
    if company_id is not None:
        member_ids = TeamMembership.objects.filter(team__company_id=company_id, team__is_deleted=False,
            team__company__is_active=True, is_active=True, is_deleted=False).values("user_id")
        grants = OrganizationAccess.objects.filter(company_id=company_id, company__is_active=True,
            is_active=True, is_deleted=False).filter(Q(can_manage=True) | Q(can_view_all=True)).values("user_id")
        users = users.filter(Q(pk__in=member_ids) | Q(pk__in=grants) | Q(is_superuser=True) | Q(groups__name="Admin")).distinct()
    candidates = []
    for user in users.order_by("pk").iterator():
        if all(allowed(user, item, staff=False) for item in (permission,*additional_permissions)):
            candidates.append(user.pk)
    return get_user_model().objects.filter(pk__in=candidates).order_by("first_name", "username", "pk")


def promise_assignees(user, promise, *, company_id):
    """A propagated check must remain readable to its responsible employee."""
    linked = promise.pk and CollectionTask.objects.filter(promise_to_pay=promise,task_type="CHECK_PROMISE").exists()
    return source_assignees(user,company_id,permission="credit_control.view_promisetopay",
        additional_permissions=("credit_control.view_collectiontask",) if linked else ())


def coherent_promise_check(promise, task):
    return (task.task_type == "CHECK_PROMISE" and task.promise_to_pay_id == promise.pk
        and task.company_id == promise.company_id and task.customer_id == promise.customer_id
        and task.invoice_id == promise.invoice_id and task.supplier_id is None and task.bill_id is None)


def can_assign_promise(user, promise):
    if not can_change_source(user,promise) or not source_manager(user,promise.company_id):
        return False
    return all(coherent_promise_check(promise,task) and can_change_source(user,task)
        for task in CollectionTask.objects.filter(promise_to_pay=promise,task_type="CHECK_PROMISE"))


def eligible_collection_owner(user, company_id):
    """Live read authority for a proposed, explicitly assigned source check."""
    try:
        actor = current_actor(user)
    except PermissionDenied:
        return False
    return bool(allowed(actor,"credit_control.view_collectiontask",staff=False) and (
        company_id is None or source_company_ids(actor).filter(pk=company_id).exists()))
