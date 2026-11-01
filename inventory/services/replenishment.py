"""Procurement review authority, never stock movement or purchase acceptance."""
from datetime import date
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import Company
from inventory.models import Product
from inventory.replenishment_models import ReplenishmentEvent, ReplenishmentPlan
from inventory.replenishment_policies import (can_change_plan, can_manage_company, can_manage_plan,
    current_actor, eligible_assignees, source_read_capable, visible_plans_for)


class SourceConflict(ValidationError):
    pass


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def product_stock_version(product):
    raw = f"{product.quantity}|{product.reserved}|{product.reorder_level}|{int(product.is_active)}|{int(product.track_inventory)}"
    return hashlib.sha256(raw.encode()).hexdigest()


def stock_facts(product):
    """Evaluate raw stock without hiding inconsistent records behind clamping."""
    quantity, reserved, reorder = product.quantity, product.reserved, product.reorder_level
    valid = (all(type(value) is int for value in (quantity, reserved, reorder))
        and quantity >= 0 and 0 <= reserved <= quantity and reorder >= 0)
    available = quantity - reserved if valid else None
    if not product.is_active or not product.track_inventory:
        state = "INACTIVE"
    elif not valid:
        state = "INVALID"
    else:
        state = "LOW" if available <= reorder else "CLEAR"
    return {"need_state": state, "available": available, "reorder_level": reorder,
        "stock_version": product_stock_version(product)}


def effective_plan_state(plan):
    """Current source facts for GETs. Never updates observations or ownership."""
    facts = stock_facts(plan.product)
    review = plan.review_state if plan.observed_need_state == "LOW" else "NEEDS_REVIEW"
    facts.update(review_state=review, status="UNAVAILABLE", reason_code="")
    if not plan.is_enabled:
        facts["reason_code"] = "responsibility_paused"
    elif not plan.company.is_active:
        facts["reason_code"] = "company_inactive"
    elif facts["need_state"] == "INACTIVE":
        facts["reason_code"] = "product_not_stock_tracked"
    elif facts["need_state"] == "INVALID":
        facts["reason_code"] = "stock_records_need_review"
    elif facts["need_state"] == "CLEAR":
        facts.update(status="COMPLETED", reason_code="need_cleared")
    elif not plan.assigned_to_id or not plan.assigned_to.is_active:
        facts.update(status="UNASSIGNED", reason_code="responsible_owner_needed")
    else:
        facts["status"] = "OPEN" if review == "NEEDS_REVIEW" else "IN_PROGRESS"
    return facts


def get_plan(*, user, pk):
    item = visible_plans_for(user).select_related("product", "company", "assigned_to").filter(pk=pk).first()
    if item is None:
        raise PermissionDenied
    return item


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ValidationError("Choose a valid product, company or employee.")
    return value


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid source revision and action key are required.") from None


def _work_values(next_review_on, notes):
    if next_review_on in (None, ""):
        day = None
    elif type(next_review_on) is date:
        day = next_review_on
    elif type(next_review_on) is str:
        try:
            day = date.fromisoformat(next_review_on)
            if day.isoformat() != next_review_on:
                raise ValueError
        except ValueError:
            raise ValidationError("Choose an exact next-review date.") from None
    else:
        raise ValidationError("Choose an exact next-review date.")
    if day and not 1900 <= day.year <= 2200:
        raise ValidationError("Choose a next-review date from 1900 to 2200.")
    if type(notes) is not str or len(notes) > 10000:
        raise ValidationError("Standing procurement notes must be text of at most 10,000 characters.")
    return day, notes


def _locked_actor(user, *, additional_user_ids=()):
    actor = current_actor(user)
    # Configuration can assign another active employee. Lock all participating
    # user rows before Product, in one global order, so deactivation cannot
    # slip past eligibility and reciprocal delegations cannot deadlock.
    ids = {actor.pk, *additional_user_ids}
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk__in=ids).order_by("pk"))
    return current_actor(actor)


def _receipt(actor, key, digest, *, product_id, action):
    event = ReplenishmentEvent.objects.filter(actor=actor, client_key=key).select_related("plan").first()
    if event and (event.request_digest != digest or event.plan.product_id != product_id or event.action != action):
        raise SourceConflict("This action key belongs to another procurement change.")
    return event


def _observe(plan, product, *, actor=None, include_receipt=True):
    """Observe a need transition under Product -> Plan -> Company locks."""
    facts = stock_facts(product)
    state = facts["need_state"] if plan.is_enabled else "INACTIVE"
    now = timezone.now()
    previous_revision, previous_state = plan.revision, plan.observed_need_state
    if state == previous_state:
        ReplenishmentPlan.objects.filter(pk=plan.pk).update(last_observed_at=now)
        plan.last_observed_at = now
        return False
    if state == "LOW":
        plan.review_state, plan.next_review_on, plan.cycle = "NEEDS_REVIEW", None, uuid.uuid4()
    plan.observed_need_state, plan.last_observed_at = state, now
    plan._replenishment_syncing = True
    try:
        plan.save(update_fields=("observed_need_state", "last_observed_at", "review_state", "next_review_on", "cycle"))
    finally:
        del plan._replenishment_syncing
    if include_receipt:
        ReplenishmentEvent.objects.create(plan=plan, actor=None, action="need_observed",
            previous_revision=previous_revision, result_revision=plan.revision, cycle=plan.cycle,
            details={"need_from": previous_state, "need_to": state})
    return True


@transaction.atomic
def synchronize_plan(pk):
    """Bounded worker/source repair entry point; no stock or finance writes."""
    pk = _identifier(pk)
    seed = ReplenishmentPlan.objects.filter(pk=pk).only("product_id").first()
    if seed is None:
        return None
    product = Product.objects.select_for_update().filter(pk=seed.product_id).first()
    if product is None:
        return None
    plan = ReplenishmentPlan.objects.select_for_update().filter(pk=pk, product=product).first()
    if plan is None:
        return None
    company = Company.objects.select_for_update().get(pk=plan.company_id)
    plan.product, plan.company = product, company
    _observe(plan, product)
    return plan


@transaction.atomic
def configure_plan(*, user, product_id, company_id, assigned_to_id, is_enabled,
        expected_revision, client_key, next_review_on=None, notes=""):
    product_id, company_id = _identifier(product_id), _identifier(company_id)
    if assigned_to_id is not None:
        assigned_to_id = _identifier(assigned_to_id)
    actor = _locked_actor(user, additional_user_ids=() if assigned_to_id is None else (assigned_to_id,))
    if type(is_enabled) is not bool:
        raise ValidationError("Choose whether this responsibility is enabled.")
    key = _uuid(client_key)
    expected = "new" if expected_revision == "new" else str(_uuid(expected_revision))
    day, notes = _work_values(next_review_on, notes)
    digest = _digest({"action":"configure", "product":product_id, "company":company_id,
        "owner":assigned_to_id, "enabled":is_enabled, "revision":expected,
        "day":day.isoformat() if day else None, "notes":notes})
    product = Product.objects.select_for_update().filter(pk=product_id).first()
    if product is None:
        raise PermissionDenied
    plan = ReplenishmentPlan.objects.select_for_update().filter(product=product).first()
    companies = {company_id} | ({plan.company_id} if plan else set())
    locked = {item.pk:item for item in Company.objects.select_for_update().filter(pk__in=companies).order_by("pk")}
    actor = current_actor(actor)
    if not can_manage_company(actor, company_id) or (plan and not can_manage_plan(actor, plan)):
        raise PermissionDenied
    if company_id not in locked:
        raise PermissionDenied
    receipt = _receipt(actor, key, digest, product_id=product_id, action="configure")
    if receipt:
        return get_plan(user=actor, pk=receipt.plan_id)
    if (plan is None and expected != "new") or (plan is not None and str(plan.revision) != expected):
        raise SourceConflict("This procurement mapping changed. Review the current source before saving.")
    owner = None
    if assigned_to_id is not None:
        owner = eligible_assignees(actor, company_id).filter(pk=assigned_to_id).first()
        if owner is None:
            raise ValidationError("Choose an active employee with inventory review access in this company.")
    before_revision = plan.revision if plan else None
    before = {"owner":plan.assigned_to_id if plan else None, "company":plan.company_id if plan else None,
        "enabled":plan.is_enabled if plan else False}
    plan = plan or ReplenishmentPlan(product=product, created_by=actor)
    plan.company, plan.assigned_to, plan.is_enabled = locked[company_id], owner, is_enabled
    plan.next_review_on, plan.notes = day, notes
    plan._allow_company_move = True
    plan.save()
    _observe(plan, product)
    # An explicit configured review date belongs to this newly reviewed need,
    # not to the previous cycle that the observation may have cleared.
    if plan.next_review_on != day:
        plan.next_review_on = day
        plan.save(update_fields=("next_review_on",))
    ReplenishmentEvent.objects.create(plan=plan, actor=actor, client_key=key, action="configure",
        request_digest=digest, previous_revision=before_revision, result_revision=plan.revision,
        cycle=plan.cycle, details={"owner_from":before["owner"], "owner_to":plan.assigned_to_id,
            "company_from":before["company"], "company_to":plan.company_id,
            "enabled_from":before["enabled"], "enabled_to":plan.is_enabled})
    return plan


@transaction.atomic
def change_plan(*, user, pk, expected_revision, expected_stock_version, client_key,
        review_state, next_review_on=None, notes=""):
    actor = _locked_actor(user)
    pk, revision, key = _identifier(pk), _uuid(expected_revision), _uuid(client_key)
    if review_state not in ReplenishmentPlan.ReviewState.values:
        raise ValidationError("Choose a supported replenishment review step.")
    if type(expected_stock_version) is not str or len(expected_stock_version) != 64:
        raise ValidationError("Review the current stock facts before saving this decision.")
    day, notes = _work_values(next_review_on, notes)
    digest = _digest({"action":"progress", "plan":pk, "revision":str(revision),
        "stock":expected_stock_version, "state":review_state,
        "day":day.isoformat() if day else None, "notes":notes})
    seed = ReplenishmentPlan.objects.filter(pk=pk).only("product_id").first()
    if seed is None:
        raise PermissionDenied
    product = Product.objects.select_for_update().get(pk=seed.product_id)
    plan = ReplenishmentPlan.objects.select_for_update().filter(pk=pk, product=product).first()
    if plan is None:
        raise PermissionDenied
    company = Company.objects.select_for_update().get(pk=plan.company_id)
    plan.product, plan.company = product, company
    actor = current_actor(actor)
    if not can_change_plan(actor, plan):
        raise PermissionDenied
    event = _receipt(actor, key, digest, product_id=product.pk, action="progress")
    if event:
        return plan
    if plan.revision != revision or product_stock_version(product) != expected_stock_version:
        raise SourceConflict("The procurement review or stock facts changed. Review them before saving.")
    facts = effective_plan_state(plan)
    if not plan.is_enabled or facts["need_state"] != "LOW":
        raise SourceConflict("This product has no active, valid low-stock review to progress.")
    before = facts["review_state"]
    _observe(plan, product)
    plan.review_state, plan.next_review_on, plan.notes = review_state, day, notes
    plan.save()
    ReplenishmentEvent.objects.create(plan=plan, actor=actor, client_key=key, action="progress",
        request_digest=digest, previous_revision=revision, result_revision=plan.revision,
        cycle=plan.cycle, details={"review_from":before, "review_to":review_state,
            "next_review_on":day.isoformat() if day else None})
    return plan
