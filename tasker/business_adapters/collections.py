"""Credit Control metadata only: no contact, note, amount or payment copying."""
import hashlib
import json

from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Exists, OuterRef, Q
from django_hosts.resolvers import reverse as host_reverse

from credit_control.models import CollectionTask, PromiseToPay
from tasker.business_models import BusinessRecordLink


SOURCE_TYPES = {"collection_task": CollectionTask, "promise_to_pay": PromiseToPay}
ACTION_KEYS = {"collection_task": "follow_up", "promise_to_pay": "review_promise"}
ACTIVE_STATUSES = ("OPEN", "IN_PROGRESS")


def source_type(value):
    if not isinstance(value, str) or value not in SOURCE_TYPES:
        raise ValidationError("Choose a registered collections source.")
    return value


def source_id(value):
    if type(value) is not int or not 1 <= value <= 9223372036854775807:
        raise ValidationError("Choose a valid source reference.")
    return value


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def readable_sources(user, kind):
    from credit_control.source_policies import visible_collection_tasks_for, visible_promises_for
    return visible_collection_tasks_for(user) if source_type(kind) == "collection_task" else visible_promises_for(user)


def actionable_sources(user, kind):
    rows = readable_sources(user, kind)
    if kind == "collection_task":
        return rows.filter(status__in=("PENDING", "IN_PROGRESS")).exclude(
            task_type="CHECK_PROMISE", promise_to_pay__isnull=True)
    covered = CollectionTask.objects.filter(promise_to_pay_id=OuterRef("pk"), task_type="CHECK_PROMISE")
    return rows.filter(status__in=("OPEN", "BROKEN", "PARTIALLY_KEPT")).annotate(
        _tasker_covered=Exists(covered)).filter(_tasker_covered=False)


def load_source(kind, pk, *, lock=False):
    kind, pk = source_type(kind), source_id(pk)
    rows = SOURCE_TYPES[kind].objects
    if lock:
        rows = rows.select_for_update(of=("self",))
    return rows.select_related("company", "assigned_to").filter(pk=pk).first()


def snapshot(kind, pk, *, instance=None):
    """A generic observation; never declares money collected or a goal achieved."""
    kind, pk = source_type(kind), source_id(pk)
    record = instance if instance is not None else load_source(kind, pk)
    result = {"source_app": "credit_control", "source_type": kind, "source_record_id": pk,
        "action_key": ACTION_KEYS[kind], "title": "Collection follow-up" if kind == "collection_task" else "Review payment promise",
        "owner_id": None, "company_id": None, "status": "UNAVAILABLE", "due_date": None,
        "priority": "MEDIUM", "source_status": "", "source_version": "",
        "source_updated_at": None, "reason_code": "source_missing"}
    if record is None:
        return result
    result.update(owner_id=record.assigned_to_id, company_id=record.company_id,
        source_status=record.status, source_version=str(record.revision),
        due_date=str(record.due_date if kind == "collection_task" else record.promised_date),
        source_updated_at=record.updated_at.isoformat(), reason_code="")
    if kind == "collection_task":
        result["title"] = {"FOLLOW_UP_DEBTOR": "Debtor follow-up", "FOLLOW_UP_CREDITOR": "Creditor follow-up",
            "CHECK_PROMISE": "Check payment promise", "ESCALATION_REVIEW": "Review collection escalation",
            "SEND_REMINDER": "Review collection reminder"}.get(record.task_type, "Collection follow-up")
        result["priority"] = "CRITICAL" if record.priority == "URGENT" else record.priority
        result["status"] = {"PENDING": "OPEN", "IN_PROGRESS": "IN_PROGRESS", "DONE": "COMPLETED",
            "CANCELLED": "CANCELLED"}.get(record.status, "UNAVAILABLE")
        if record.task_type == "CHECK_PROMISE" and not record.promise_to_pay_id:
            result.update(status="UNAVAILABLE", reason_code="promise_reference_missing")
        elif record.task_type == "CHECK_PROMISE" and not PromiseToPay.objects.filter(pk=record.promise_to_pay_id).exists():
            result.update(status="UNAVAILABLE", reason_code="promise_reference_missing")
    else:
        result["priority"] = "HIGH" if record.status == "BROKEN" else "MEDIUM"
        result["status"] = {"OPEN": "OPEN", "BROKEN": "OPEN", "PARTIALLY_KEPT": "OPEN",
            "KEPT": "COMPLETED", "CANCELLED": "CANCELLED"}.get(record.status, "UNAVAILABLE")
        if CollectionTask.objects.filter(promise_to_pay_id=record.pk, task_type="CHECK_PROMISE").exists():
            result.update(status="CANCELLED", reason_code="covered_by_collection_task")
    if record.company_id and not record.company.is_active:
        result.update(status="UNAVAILABLE", reason_code="company_inactive")
    elif result["status"] in ACTIVE_STATUSES and (not record.assigned_to_id or not record.assigned_to.is_active):
        result.update(status="UNASSIGNED", reason_code="responsible_owner_needed")
    return result


def source_url(*, user, kind, pk):
    if not readable_sources(user, kind).filter(pk=source_id(pk)).exists():
        raise PermissionDenied
    route = "task_detail" if kind == "collection_task" else "promise_detail"
    return host_reverse("ims:credit_control:"+route, kwargs={"pk": pk}, host="ims")
