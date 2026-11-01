"""Idempotent source references through the existing outbox and live permissions."""
from copy import copy
from datetime import date
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Case, CharField, DateField, DateTimeField, Exists, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Cast, Concat
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from tasker.business_adapters import collections as adapter
from tasker.business_models import BusinessLinkEvent, BusinessRecordLink, BusinessSyncCursor


ACTIONABLE = ("OPEN", "IN_PROGRESS")


def enabled(source_type=None):
    from operations.registry import SOURCES
    if type(source_type) is str and source_type in SOURCES:
        flag = 'TASKER_'+source_type.removesuffix('_work').upper()+'_ADAPTER_ENABLED'
        return bool(getattr(settings,flag,False))
    if source_type == "replenishment_plan":
        return bool(getattr(settings, "TASKER_REPLENISHMENT_ADAPTER_ENABLED", False))
    collections = bool(getattr(settings, "TASKER_COLLECTIONS_ADAPTER_ENABLED", False))
    return collections if source_type is not None else collections or enabled("replenishment_plan") or any(enabled(k) for k in SOURCES)


def _adapter(kind):
    if type(kind) is not str:
        raise ValidationError("Choose a registered business source.")
    from tasker.business_adapters import operations
    if kind in operations.SOURCE_TYPES:
        return operations
    if kind == "replenishment_plan":
        from tasker.business_adapters import replenishment
        return replenishment
    adapter.source_type(kind)
    return adapter


def _source_app(kind):
    from operations.registry import SOURCES
    return 'operations' if kind in SOURCES else "inventory" if kind == "replenishment_plan" else "credit_control"


def _source_kinds():
    from operations.registry import SOURCES
    return ("collection_task", "promise_to_pay", "replenishment_plan", *SOURCES)


def _actor(user):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def _collection_live_annotations():
    # OuterRef is deliberately local: only the registered source table is used.
    tasks = adapter.SOURCE_TYPES["collection_task"].objects.filter(pk=OuterRef("source_record_id"))
    promises = adapter.SOURCE_TYPES["promise_to_pay"].objects.filter(pk=OuterRef("source_record_id"))
    rank = Case(When(priority="URGENT", then=0), When(priority="HIGH", then=1),
        When(priority="MEDIUM", then=2), default=3, output_field=IntegerField())
    task_rank = tasks.annotate(_rank=rank).values("_rank")[:1]
    promise_rank = promises.annotate(_rank=Case(When(status="BROKEN", then=1), default=2,
        output_field=IntegerField())).values("_rank")[:1]
    return {"_business_due": Case(When(source_type="collection_task", then=Subquery(tasks.values("due_date")[:1])),
                default=Subquery(promises.values("promised_date")[:1]), output_field=DateField()),
        "_business_rank": Case(When(source_type="collection_task", then=Subquery(task_rank)),
                default=Subquery(promise_rank), output_field=IntegerField()),
        "_business_source_version": Case(When(source_type="collection_task", then=Cast(Subquery(tasks.values("revision")[:1]), CharField())),
                default=Cast(Subquery(promises.values("revision")[:1]), CharField()), output_field=CharField())}


def _collection_notification_annotations():
    tasks = adapter.SOURCE_TYPES["collection_task"].objects.filter(pk=OuterRef("source_record_id"))
    promises = adapter.SOURCE_TYPES["promise_to_pay"].objects.filter(pk=OuterRef("source_record_id"))
    task_title = Case(When(task_type="FOLLOW_UP_DEBTOR", then=Value("Debtor follow-up")),
        When(task_type="FOLLOW_UP_CREDITOR", then=Value("Creditor follow-up")),
        When(task_type="CHECK_PROMISE", then=Value("Check payment promise")),
        When(task_type="ESCALATION_REVIEW", then=Value("Review collection escalation")),
        When(task_type="SEND_REMINDER", then=Value("Review collection reminder")),
        default=Value("Collection follow-up"), output_field=CharField())
    task_priority = Case(When(priority="URGENT", then=Value("CRITICAL")), default=F("priority"), output_field=CharField())
    promise_priority = Case(When(status="BROKEN", then=Value("HIGH")), default=Value("MEDIUM"), output_field=CharField())
    result = {}
    for label, field, output_field in (("status", "status", CharField()), ("owner", "assigned_to_id", IntegerField()),
            ("company", "company_id", IntegerField()), ("updated", "updated_at", DateTimeField())):
        result["_business_"+label] = Case(When(source_type="collection_task", then=Subquery(tasks.values(field)[:1])),
            default=Subquery(promises.values(field)[:1]), output_field=output_field)
    result["_business_title"] = Case(When(source_type="collection_task", then=Subquery(tasks.annotate(
        _title=task_title).values("_title")[:1])), default=Value("Review payment promise"), output_field=CharField())
    result["_business_priority"] = Case(When(source_type="collection_task", then=Subquery(tasks.annotate(
        _priority=task_priority).values("_priority")[:1])), default=Subquery(promises.annotate(
        _priority=promise_priority).values("_priority")[:1]), output_field=CharField())
    result["_business_state"] = Case(When(source_type="collection_task", _business_status="IN_PROGRESS", then=Value("IN_PROGRESS")),
        default=Value("OPEN"), output_field=CharField())
    return result


def _annotations(*, notification=False):
    fields = {"due": DateField(), "rank": IntegerField(), "source_version": CharField()}
    if notification:
        fields = {"company":IntegerField(), "owner":IntegerField(), "updated":DateTimeField(),
            "title":CharField(), "priority":CharField(), "status":CharField(), "state":CharField()}
    if enabled("collection_task"):
        result = _collection_notification_annotations() if notification else _collection_live_annotations()
    else:
        result = {"_business_"+key:Value(None, output_field=field) for key,field in fields.items()}
    if enabled("replenishment_plan"):
        module = _adapter("replenishment_plan")
        sources = module.SOURCE_TYPES["replenishment_plan"].objects.filter(pk=OuterRef("source_record_id"))
        expressions = module.sql_fields()
        for key, field in fields.items():
            subquery = Subquery(sources.annotate(_replenishment_value=expressions[key]).values("_replenishment_value")[:1])
            result["_business_"+key] = Case(When(source_type="replenishment_plan", then=subquery),
                default=result["_business_"+key], output_field=field)
    from operations.registry import SOURCES
    for kind in SOURCES:
        if not enabled(kind):
            continue
        module = _adapter(kind)
        sources = module.with_state(module.SOURCE_TYPES[kind].objects.filter(
            pk=OuterRef('source_record_id'),kind=kind),kind)
        expressions = module.sql_fields(kind)
        for key,field in fields.items():
            subquery = Subquery(sources.annotate(_value=expressions[key]).values('_value')[:1])
            result['_business_'+key] = Case(When(source_type=kind,then=subquery),
                default=result['_business_'+key],output_field=field)
    return result


def _live_annotations():
    return _annotations()


def _notification_annotations():
    return _annotations(notification=True)


def visible_business_links_for(user, *, state="all", source_type=None, mine=False):
    """Source ACLs, live owner/state filters and current dates precede pagination."""
    actor = _actor(user)
    queryset = BusinessRecordLink.objects.all()
    if not enabled():
        return queryset.none()
    if state not in ("all", "active", "closed") or type(mine) is not bool:
        raise ValidationError("Choose a supported business action filter.")
    if source_type:
        _adapter(source_type).source_type(source_type)
        queryset = queryset.filter(source_type=source_type)
    condition = Q(pk__in=[])
    for kind in _source_kinds():
        if not enabled(kind):
            continue
        module = _adapter(kind)
        readable = module.readable_sources(actor, kind)
        active = module.actionable_sources(actor, kind)
        if mine:
            readable, active = readable.filter(assigned_to=actor), active.filter(assigned_to=actor)
        if state == "active":
            sources = active
        elif state == "closed":
            sources = readable.exclude(pk__in=active.values("pk"))
        else:
            sources = readable
        condition |= Q(source_app=_source_app(kind), source_type=kind, source_record_id__in=sources.values("pk"))
    return queryset.filter(condition).annotate(**_live_annotations()).select_related("owner", "company").order_by(
        "_business_rank", F("_business_due").asc(nulls_last=True), "created_at", "pk")


def business_links_for_owner(user):
    return visible_business_links_for(user, mine=True)


def _apply_values(link, facts):
    for key in ("owner_id", "company_id", "status", "title", "priority", "source_status", "source_version", "reason_code"):
        setattr(link, key, facts[key])
    link.due_date = date.fromisoformat(facts["due_date"]) if facts["due_date"] else None
    link.source_updated_at = parse_datetime(facts["source_updated_at"]) if facts["source_updated_at"] else None
    link.source_digest = adapter.digest(facts)
    link._state.fields_cache.pop("owner", None)
    link._state.fields_cache.pop("company", None)
    return link


def present_business_link(*, user, link):
    """Read current source facts without writing a projection from a GET request."""
    actor = _actor(user)
    if not isinstance(link, BusinessRecordLink) or not enabled(link.source_type) or link.source_app != _source_app(link.source_type):
        raise PermissionDenied
    module = _adapter(link.source_type)
    sources = module.readable_sources(actor, link.source_type).select_related("company", "assigned_to")
    if link.source_type == "replenishment_plan":
        sources = sources.select_related("product")
    source = sources.filter(pk=link.source_record_id).first()
    if source is None:
        raise PermissionDenied
    presented = copy(link)
    presented._state = copy(link._state)
    presented._state.fields_cache = link._state.fields_cache.copy()
    _apply_values(presented, module.snapshot(link.source_type, link.source_record_id, instance=source))
    presented._state.fields_cache = {"owner": source.assigned_to, "company": source.company}
    presented.source_stale = presented.source_digest != link.source_digest
    presented.source_url = module.source_url(user=actor, kind=link.source_type, pk=link.source_record_id)
    return presented


def get_business_link(*, user, pk):
    link = visible_business_links_for(user).filter(pk=pk).first()
    if link is None:
        raise PermissionDenied
    return present_business_link(user=user, link=link)


def business_source_url(*, user, link):
    actor = _actor(user)
    if not enabled(link.source_type) or link.source_app != _source_app(link.source_type):
        raise PermissionDenied
    return _adapter(link.source_type).source_url(user=actor, kind=link.source_type, pk=link.source_record_id)


def business_actions_for(user, *, limit=8, due_by=None):
    if type(limit) is not int or not 1 <= limit <= 50:
        raise ValidationError("Choose a business action limit between one and fifty.")
    rows = visible_business_links_for(user, mine=True, state="active")
    if due_by is not None:
        if type(due_by) is not date:
            raise ValidationError('Choose a calendar date for operational guidance.')
        rows=rows.filter(Q(_business_due__lte=due_by)|Q(_business_due__isnull=True))
    total, items = rows.count(), []
    for link in rows[:limit]:
        try:
            presented = present_business_link(user=user, link=link)
        except PermissionDenied:
            continue  # A source grant may have been removed after the page read.
        if presented.status in ACTIONABLE and presented.owner_id == user.pk:
            items.append(presented)
    return {"items": items, "total": total, "omitted": max(0, total-len(items))}


def business_notification_links_for(user):
    # Counts and notification pagination use the same current metadata check as
    # final dispatch. Ordinary source saves revise their UUID; legacy bulk
    # updates can bypass that, so UUID equality alone is insufficient.
    rows = visible_business_links_for(user, mine=True, state="active")
    if not enabled():
        return rows
    return rows.annotate(**_notification_annotations()).filter(
        Q(company_id=F("_business_company")) | Q(company_id__isnull=True, _business_company__isnull=True),
        Q(due_date=F("_business_due")) | Q(due_date__isnull=True, _business_due__isnull=True),
        owner=user, owner_id=F("_business_owner"), source_version=F("_business_source_version"),
        source_status=F("_business_status"), status=F("_business_state"), priority=F("_business_priority"),
        source_updated_at=F("_business_updated"), title=F("_business_title"), reason_code__in=("","quality_acceptance_needed"))


def business_notification_available(*, user, link, source_revision):
    if not enabled() or not isinstance(link, BusinessRecordLink):
        return False
    try:
        stored = business_notification_links_for(user).filter(pk=link.pk).first()
        if stored is None or str(stored.revision) != str(source_revision):
            return False
        current = present_business_link(user=user, link=stored)
        return current.status in ACTIONABLE and current.owner_id == user.pk and not current.source_stale
    except (PermissionDenied, ValidationError):
        return False


def enqueue_business_source(source_type, source_id):
    module = _adapter(source_type)
    kind, pk = module.source_type(source_type), module.source_id(source_id)
    if not enabled(kind):
        return None
    from tasker.services.jobs import enqueue_job
    facts = module.snapshot(kind, pk)
    target = adapter.digest(facts)
    link = BusinessRecordLink.objects.filter(source_app=_source_app(kind), source_type=kind,
        source_record_id=pk, action_key=module.ACTION_KEYS[kind]).only("source_digest", "revision").first()
    if link is not None and link.source_digest == target:
        from tasker.services.notifications import queue_business_due_update
        queue_business_due_update(link.pk)
        return None
    if facts["reason_code"] == "source_missing" and link is None:
        return None
    # Include the projection generation: after disabled rollback or deletion,
    # the same source content must not reuse an old terminal outbox tombstone.
    key = adapter.digest({"target": target, "projection": str(link.revision) if link else "new"})
    namespace = _source_app(kind)
    base = f"business:{namespace}:{kind}:{pk}:{key}"
    from tasker.job_models import OutboxJob
    # Preserve completed receipts. A job may have observed a different source
    # state, or have run while the adapter was disabled before its first link
    # existed. Its next repair derives one fresh, deduplicated successor. The
    # leaf predicate also handles identical timestamps and clock adjustments.
    successors = OutboxJob.objects.filter(dedupe_key=Concat(Value(base+":repair:"), Cast(OuterRef("pk"), CharField())))
    latest = OutboxJob.objects.filter(kind="business.reconcile").filter(
        Q(dedupe_key=base) | Q(dedupe_key__startswith=base+":repair:")).annotate(
        _has_repair=Exists(successors)).filter(_has_repair=False).order_by("-created_at", "-pk").first()
    if latest is not None:
        if latest.state == "SUCCEEDED":
            base += ":repair:"+str(latest.pk)
        elif latest.state in ("FAILED", "CANCELLED"):
            return latest  # Failure budgets and intentional cancellations stand.
        else:
            base = latest.dedupe_key
    return enqueue_job(kind="business.reconcile", dedupe_key=base,
        payload={"source_type": kind, "source_id": pk})


@transaction.atomic
def reconcile_business_source(*, source_type, source_id):
    """A worker/repair entry point, not a user-authored completion action."""
    module = _adapter(source_type)
    kind, pk = module.source_type(source_type), module.source_id(source_id)
    identity = {"source_app": _source_app(kind), "source_type": kind,
        "source_record_id": pk, "action_key": module.ACTION_KEYS[kind]}
    if not enabled(kind):
        # Hidden-adapter retirement only touches its projection. In particular,
        # a bounded multi-row retirement must not hold ProductA/Company while
        # waiting on ProductB, the reverse of a source writer's lock order.
        link = BusinessRecordLink.objects.select_for_update().filter(**identity).first()
        if link is None:
            return None
        source = None
        facts = {key:getattr(link,key) for key in ("source_app","source_type","source_record_id",
            "action_key","owner_id","company_id","title","priority","source_status","source_version")}
        facts.update(status="UNAVAILABLE",reason_code="adapter_disabled",
            due_date=link.due_date.isoformat() if link.due_date else None,
            source_updated_at=link.source_updated_at.isoformat() if link.source_updated_at else None)
    else:
        if kind == "replenishment_plan":
            from inventory.services.replenishment import synchronize_plan
            synchronize_plan(pk)
        source = module.load_source(kind, pk, lock=True)
        link = BusinessRecordLink.objects.select_for_update().filter(**identity).first()
        facts = module.snapshot(kind, pk, instance=source) if source is not None else module.snapshot(kind, pk)
    fingerprint = adapter.digest(facts)
    if link is not None and link.source_digest == fingerprint:
        BusinessRecordLink.objects.filter(pk=link.pk).update(last_reconciled_at=timezone.now())
        from .business_outcomes import retract_invalid_for
        retract_invalid_for(link)
        return link
    # A source row lock serializes the first create. Missing-source tombstones
    # are created only if an existing reference needs closure.
    if link is None and source is None:
        return None
    previous_revision = str(link.revision) if link is not None else "new"
    link = link or BusinessRecordLink(**identity)
    _apply_values(link, facts)
    link.revision, link.last_reconciled_at = uuid.uuid4(), timezone.now()
    link.save()
    # A real transition back to earlier facts is a new observation. Replays of
    # unchanged facts exit above, while disabled/re-enabled work receives a new
    # notification identity rather than reusing an old cancelled receipt.
    event_key = adapter.digest({"previous": previous_revision, "target": fingerprint})
    event, _ = BusinessLinkEvent.objects.get_or_create(link=link, event_key=event_key,
        defaults={"status": link.status, "owner_id_snapshot": link.owner_id,
            "source_version": link.source_version, "reason_code": link.reason_code, "revision": link.revision})
    from tasker.services.notifications import queue_business_link_update
    queue_business_link_update(link, event)
    from .business_outcomes import retract_invalid_for
    retract_invalid_for(link)
    return link


def handle_business_source(*, payload, job=None):
    if type(payload) is not dict or set(payload) != {"source_type", "source_id"}:
        raise ValidationError("A collections job contains only a registered source reference.")
    from tasker.services.jobs import assert_current_job_lease, fence_current_job_effects
    with transaction.atomic():
        assert_current_job_lease()
        link = reconcile_business_source(source_type=payload["source_type"], source_id=payload["source_id"])
        fence_current_job_effects()
    return {"reconciled": int(link is not None)}


@transaction.atomic
def _reconcile_collections(*, limit=100):
    """Fair bounded round-robin scans; event hooks are an optimization, not authority."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose a collections repair limit between one and one thousand.")
    cursor, _ = BusinessSyncCursor.objects.get_or_create(key="collections")
    cursor = BusinessSyncCursor.objects.select_for_update().get(pk=cursor.pk)
    queued, scanned, closed = 0, 0, 0
    if not enabled("collection_task"):
        rows = BusinessRecordLink.objects.filter(source_app="credit_control")
        if cursor.link_after:
            rows = rows.filter(pk__gt=cursor.link_after)
        rows = list(rows.order_by("pk")[:limit])
        if not rows:
            cursor.link_after = None
        for link in rows:
            reconcile_business_source(source_type=link.source_type, source_id=link.source_record_id)
            cursor.link_after = link.pk
            scanned += 1
            closed += 1
        cursor.save()
        return {"queued": 0, "scanned": scanned, "disabled": closed}
    remaining = limit
    for offset in range(3):
        phase = (cursor.phase+offset) % 3
        budget = min(remaining, (limit+2)//3)
        if budget < 1:
            break
        if phase < 2:
            kind = "collection_task" if phase == 0 else "promise_to_pay"
            field = "collection_after" if phase == 0 else "promise_after"
            values = list(adapter.SOURCE_TYPES[kind].objects.filter(pk__gt=getattr(cursor, field)).order_by("pk").values_list("pk", flat=True)[:budget])
            if not values:
                setattr(cursor, field, 0)
            for pk in values:
                job = enqueue_business_source(kind, pk)
                setattr(cursor, field, pk)
                queued += int(job is not None)
        else:
            rows = BusinessRecordLink.objects.filter(source_app="credit_control")
            if cursor.link_after:
                rows = rows.filter(pk__gt=cursor.link_after)
            values = list(rows.order_by("pk")[:budget])
            if not values:
                cursor.link_after = None
            for link in values:
                job = enqueue_business_source(link.source_type, link.source_record_id)
                cursor.link_after = link.pk
                queued += int(job is not None)
        scanned += len(values)
        remaining -= len(values)
    cursor.phase = (cursor.phase+1) % 3
    cursor.save()
    return {"queued": queued, "scanned": scanned, "disabled": 0}


@transaction.atomic
def _reconcile_replenishment(*, limit):
    module = _adapter("replenishment_plan")
    cursor, _ = BusinessSyncCursor.objects.get_or_create(key="replenishment")
    cursor = BusinessSyncCursor.objects.select_for_update().get(pk=cursor.pk)
    rows = BusinessRecordLink.objects.filter(source_app="inventory", source_type="replenishment_plan")
    queued = scanned = disabled = 0
    if not enabled("replenishment_plan"):
        if cursor.link_after:
            rows = rows.filter(pk__gt=cursor.link_after)
        values = list(rows.order_by("pk")[:limit])
        if not values:
            cursor.link_after = None
        for link in values:
            reconcile_business_source(source_type="replenishment_plan", source_id=link.source_record_id)
            cursor.link_after = link.pk
            scanned += 1
            disabled += 1
    elif cursor.phase % 2 == 0:
        # Each adapter has its own cursor; this numeric slot tracks Plan IDs.
        values = list(module.SOURCE_TYPES["replenishment_plan"].objects.filter(
            pk__gt=cursor.collection_after).order_by("pk").values_list("pk", flat=True)[:limit])
        if not values:
            cursor.collection_after = 0
        for pk in values:
            queued += int(enqueue_business_source("replenishment_plan", pk) is not None)
            cursor.collection_after = pk
            scanned += 1
    else:
        if cursor.link_after:
            rows = rows.filter(pk__gt=cursor.link_after)
        values = list(rows.order_by("pk")[:limit])
        if not values:
            cursor.link_after = None
        for link in values:
            queued += int(enqueue_business_source("replenishment_plan", link.source_record_id) is not None)
            cursor.link_after = link.pk
            scanned += 1
    cursor.phase = (cursor.phase+1) % 2
    cursor.save()
    return {"queued":queued,"scanned":scanned,"disabled":disabled}


@transaction.atomic
def reconcile_business_links(*, limit=100):
    """Share one bounded repair budget fairly across independently gated sources."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose a business repair limit between one and one thousand.")
    handlers = []
    if enabled("collection_task") or BusinessRecordLink.objects.filter(source_app="credit_control").exists():
        handlers.append(_reconcile_collections)
    if enabled("replenishment_plan") or BusinessRecordLink.objects.filter(source_type="replenishment_plan").exists():
        handlers.append(_reconcile_replenishment)
    from operations.registry import SOURCES
    from functools import partial
    for kind in SOURCES:
        if enabled(kind) or BusinessRecordLink.objects.filter(source_type=kind).exists():
            handlers.append(partial(_reconcile_operations,kind=kind))
    if len(handlers) == 1:
        return handlers[0](limit=limit)
    result = {"queued":0,"scanned":0,"disabled":0}
    if not handlers:
        return result
    cursor, _ = BusinessSyncCursor.objects.get_or_create(key="business_rotation")
    cursor = BusinessSyncCursor.objects.select_for_update().get(pk=cursor.pk)
    ordered = handlers[cursor.phase % len(handlers):]+handlers[:cursor.phase % len(handlers)]
    remaining = limit
    for index, function in enumerate(ordered):
        budget = (remaining+len(ordered)-index-1)//(len(ordered)-index)
        if not budget:
            break
        part = function(limit=budget)
        for key in result:
            result[key] += part[key]
        remaining -= budget
    cursor.phase = (cursor.phase+1) % len(handlers)
    cursor.save()
    return result


@transaction.atomic
def _reconcile_operations(*, kind, limit):
    from operations.models import Responsibility
    cursor,_ = BusinessSyncCursor.objects.get_or_create(key='ops_'+kind)
    cursor = BusinessSyncCursor.objects.select_for_update().get(pk=cursor.pk)
    count = queued = disabled = 0
    if enabled(kind) and cursor.phase % 2 == 0:
        values = list(Responsibility.objects.filter(kind=kind,pk__gt=cursor.collection_after)
            .order_by('pk').values_list('pk',flat=True)[:limit])
        if not values:
            cursor.collection_after = 0
        for pk in values:
            queued += int(enqueue_business_source(kind,pk) is not None)
            cursor.collection_after = pk
            count += 1
    else:
        rows = BusinessRecordLink.objects.filter(source_app='operations',source_type=kind)
        if cursor.link_after:
            rows = rows.filter(pk__gt=cursor.link_after)
        values = list(rows.order_by('pk')[:limit])
        if not values:
            cursor.link_after = None
        for link in values:
            if enabled(kind):
                queued += int(enqueue_business_source(kind,link.source_record_id) is not None)
            else:
                reconcile_business_source(source_type=kind,source_id=link.source_record_id)
                disabled += 1
            cursor.link_after = link.pk
            count += 1
    cursor.phase = (cursor.phase+1) % 2
    cursor.save()
    return {'scanned':count,'queued':queued,'disabled':disabled}
