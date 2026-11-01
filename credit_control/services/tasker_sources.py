"""Source-authoritative collection follow-up actions; never payment posting."""
from datetime import date
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import Company
from credit_control.models import CollectionTask, PromiseToPay, SourceActionEvent
from credit_control.source_policies import (can_change_source, current_actor, source_assignees,
    coherent_promise_check, promise_assignees, source_manager, visible_collection_tasks_for, visible_promises_for)


class SourceConflict(ValidationError):
    pass


def get_collection_task(*, user, pk):
    item = visible_collection_tasks_for(user).select_related("assigned_to", "company", "promise_to_pay").filter(pk=pk).first()
    if item is None:
        raise PermissionDenied
    return item


def get_promise(*, user, pk):
    item = visible_promises_for(user).select_related("assigned_to", "company").filter(pk=pk).first()
    if item is None:
        raise PermissionDenied
    return item


def _digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()


def _uuid(value):
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("A valid source action key and current revision are required.") from None


def _identifier(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ValidationError("Choose a valid source or employee.")
    return value


def _values(values):
    if type(values) is not dict or set(values) - {"assigned_to_id", "due_date", "priority"}:
        raise ValidationError("Only responsibility, due date and priority can be changed here.")
    result = dict(values)
    if "assigned_to_id" in result and result["assigned_to_id"] is not None:
        result["assigned_to_id"] = _identifier(result["assigned_to_id"])
    if "priority" in result and result["priority"] not in CollectionTask.Priority.values:
        raise ValidationError("Choose a supported collection priority.")
    if "due_date" in result:
        day = result["due_date"]
        if type(day) is not str:
            raise ValidationError("Choose an exact calendar due date.")
        try:
            parsed = date.fromisoformat(day)
            if parsed.isoformat() != day or not 1900 <= parsed.year <= 2200:
                raise ValueError
        except ValueError:
            raise ValidationError("Choose an exact due date from 1900 to 2200.") from None
    return result


def _lock_company(item):
    if item.company_id:
        Company.objects.select_for_update().get(pk=item.company_id)


def _assignee(actor, item, value, *, permission):
    if value is None:
        return None
    user = source_assignees(actor, item.company_id, permission=permission).filter(pk=value).first()
    if user is None:
        raise ValidationError("Choose an active, source-authorized employee in this company.")
    return user


def _receipt(actor, *, key, digest, source_type, source_id):
    event = SourceActionEvent.objects.filter(actor=actor, client_key=key).first()
    if event and (event.request_digest != digest or event.source_type != source_type or event.source_id != source_id):
        raise SourceConflict("This action key belongs to a different source change.")
    return event


@transaction.atomic
def change_collection_task(*, user, pk, expected_revision, client_key, action, values=None):
    """Complete a follow-up, not an invoice or payment. Replays never rewrite it."""
    actor = current_actor(user)
    pk, key, revision = _identifier(pk), _uuid(client_key), _uuid(expected_revision)
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    values = _values({} if values is None else values)
    if action not in ("start", "done", "reopen", "cancel", "metadata") or (action != "metadata" and values):
        raise ValidationError("Choose a supported collection workflow action.")
    digest = _digest({"source": pk, "action": action, "values": values, "revision": str(revision)})
    # Source row precedes company/receipt locks throughout these write paths.
    item = CollectionTask.objects.select_for_update().filter(pk=pk).first()
    if item is None:
        raise PermissionDenied
    _lock_company(item)
    actor = current_actor(actor)
    if not can_change_source(actor, item):
        raise PermissionDenied
    event = _receipt(actor, key=key, digest=digest, source_type="collection_task", source_id=pk)
    if event:
        return item
    if item.revision != revision:
        raise SourceConflict("This source task changed. Review its current state before acting.")
    before = {"status": item.status, "owner": item.assigned_to_id, "priority": item.priority, "due": item.due_date.isoformat()}
    transitions = {"start": ("PENDING",), "done": ("PENDING", "IN_PROGRESS"),
        "reopen": ("DONE", "CANCELLED"), "cancel": ("PENDING", "IN_PROGRESS")}
    if action in transitions:
        if item.status not in transitions[action]:
            raise SourceConflict("That action does not match the source task's current state.")
        item.status = {"start": "IN_PROGRESS", "done": "DONE", "reopen": "PENDING", "cancel": "CANCELLED"}[action]
        item.completed_at = timezone.now() if action == "done" else None
    else:
        if not values:
            raise ValidationError("Choose at least one work field to update.")
        if item.status not in ("PENDING", "IN_PROGRESS"):
            raise SourceConflict("Reopen the source task before editing its work fields.")
        if "assigned_to_id" in values and values["assigned_to_id"] != item.assigned_to_id:
            if item.promise_to_pay_id:
                raise ValidationError("Change this check's responsible employee through its payment promise.")
            if not source_manager(actor, item.company_id):
                raise PermissionDenied
            item.assigned_to = _assignee(actor, item, values["assigned_to_id"], permission="credit_control.view_collectiontask")
        if "due_date" in values:
            item.due_date = date.fromisoformat(values["due_date"])
        if "priority" in values:
            item.priority = values["priority"]
    item.save()
    after = {"status": item.status, "owner": item.assigned_to_id, "priority": item.priority, "due": item.due_date.isoformat()}
    details = {}
    for name in before:
        if before[name] != after[name]:
            details[name+"_from"], details[name+"_to"] = before[name], after[name]
    SourceActionEvent.objects.create(source_type="collection_task", source_id=pk, actor=actor,
        client_key=key, action=action, request_digest=digest, previous_revision=revision,
        result_revision=item.revision, details=details)
    return item


@transaction.atomic
def assign_promise(*, user, pk, assigned_to_id, expected_revision, client_key):
    actor = current_actor(user)
    pk, key, revision = _identifier(pk), _uuid(client_key), _uuid(expected_revision)
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    if assigned_to_id is not None:
        assigned_to_id = _identifier(assigned_to_id)
    item = PromiseToPay.objects.select_for_update().filter(pk=pk).first()
    if item is None:
        raise PermissionDenied
    checks = list(CollectionTask.objects.select_for_update().filter(promise_to_pay=item,
        task_type="CHECK_PROMISE").order_by("pk"))
    _lock_company(item)
    actor = current_actor(actor)
    if not can_change_source(actor, item) or not source_manager(actor, item.company_id):
        raise PermissionDenied
    digest = _digest({"source":pk,"action":"assign","owner":assigned_to_id,"revision":str(revision)})
    event = _receipt(actor,key=key,digest=digest,source_type="promise_to_pay",source_id=pk)
    if event:
        return item
    if item.revision != revision:
        raise SourceConflict("The payment promise changed. Review its current source record.")
    if any(not coherent_promise_check(item, task) for task in checks):
        raise SourceConflict("A linked check has inconsistent source references. A source administrator must review it.")
    if any(not can_change_source(actor, task) for task in checks):
        raise PermissionDenied
    previous_owner = item.assigned_to_id
    item.assigned_to = None
    if assigned_to_id is not None:
        item.assigned_to = promise_assignees(actor,item,company_id=item.company_id).filter(pk=assigned_to_id).first()
        if item.assigned_to is None:
            raise ValidationError("Choose an active employee authorized to read this promise and its linked checks.")
    item.save(update_fields=["assigned_to"])
    # Known promise lineage can share its source-assigned responsibility. Do not
    # infer a legacy task relationship from customer/invoice/date similarities.
    for task in checks:
        task.assigned_to = item.assigned_to
        task.save(update_fields=["assigned_to"])
    SourceActionEvent.objects.create(source_type="promise_to_pay",source_id=pk,actor=actor,client_key=key,
        action="assign",request_digest=digest,previous_revision=revision,result_revision=item.revision,
        details={"owner_from":previous_owner,"owner_to":item.assigned_to_id})
    return item
