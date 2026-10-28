"""Leased PostgreSQL jobs with transactional domain effects and late fencing.

Claiming never holds the outbox row while a handler acquires Task/scope locks.
All handler DB changes and the final lease fence commit in one transaction.
External adapters must provide their own idempotency/ambiguous-send ledger.
"""
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timedelta
import hashlib
import importlib
import json
import re
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import CharField, Count, F, Min, OuterRef, Q, Subquery
from django.db.models.functions import Cast
from django.db.models.fields.json import KeyTextTransform
from django.utils import timezone

from tasker.job_models import JobWorkerHeartbeat, OutboxJob


HANDLERS = {
    "coordination.dispatch": ("tasker.services.coordination", "dispatch_event"),
    "notification.deliver": ("tasker.services.notifications", "deliver_notification"),
    "notification.digest": ("tasker.services.notifications", "deliver_digest"),
    "reports.build": ("tasker.services.reporting", "handle_build_report"),
    "reports.deliver": ("tasker.services.reporting", "handle_report_delivery"),
    "operator.refresh": (None, "_operator_refresh"),
}
TERMINAL = (OutboxJob.State.SUCCEEDED, OutboxJob.State.FAILED, OutboxJob.State.CANCELLED)
_current_lease = ContextVar("tasker_job_lease", default=None)
SAFE_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
SAFE_WORKER = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


class JobConflict(ValueError):
    pass


class LostJobLease(ValueError):
    pass


class JobSuperseded(LostJobLease):
    pass


class JobDeferred(Exception):
    """Return this to commit held-ledger changes; raise to roll them back."""
    def __init__(self, *, run_after, reason="deferred"):
        self.run_after = _aware(run_after)
        self.reason = _code(reason, "deferred")
        super().__init__(self.reason)


class JobCancelled(Exception):
    def __init__(self, reason="obsolete"):
        self.reason = _code(reason, "obsolete")
        super().__init__(self.reason)


class JobPermanentError(Exception):
    def __init__(self, code="permanent_failure"):
        self.code = _code(code, "permanent_failure")
        super().__init__(self.code)


class JobRetry:
    """Return after a known failed delivery to commit its ledger and spend an attempt."""
    def __init__(self, code="handler_error", *, run_after=None):
        self.code = _code(code)
        self.run_after = _aware(run_after) if run_after is not None else None


@dataclass(frozen=True)
class JobLease:
    job_id: uuid.UUID
    token: uuid.UUID
    payload_digest: str
    worker_id: str


def _code(value, default="handler_error"):
    return value if isinstance(value, str) and SAFE_CODE.fullmatch(value) else default


def _aware(value):
    if not isinstance(value, datetime) or timezone.is_naive(value):
        raise ValidationError("A timezone-aware job time is required.")
    return value


def _payload(value):
    if not isinstance(value, dict) or len(value) > 24:
        raise ValidationError("Job payloads must be small record-reference objects.")
    forbidden = {"api_key", "password", "secret", "token", "email", "body", "notes", "content", "prompt", "message", "reason"}
    def visit(item, depth=0):
        if depth > 3:
            raise ValidationError("Job payload nesting is limited.")
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str) or len(key) > 64 or any(term in key.lower() for term in forbidden):
                    raise ValidationError("Job payloads must contain references, not private content.")
                visit(child, depth+1)
        elif isinstance(item, list):
            if len(item) > 100:
                raise ValidationError("Job payload lists are limited.")
            for child in item: visit(child, depth+1)
        elif isinstance(item, str):
            if len(item) > 200 or "\x00" in item:
                raise ValidationError("Job reference strings are limited.")
        elif item is not None and type(item) not in (bool, int):
            raise ValidationError("Job payload values must be record references.")
    visit(value)
    try:
        encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError):
        raise ValidationError("Use a valid JSON record-reference payload.") from None
    if len(encoded.encode("utf-8")) > 4096:
        raise ValidationError("Job payloads are limited to 4 KiB.")
    return value, hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@transaction.atomic
def enqueue_job(*, kind, dedupe_key, payload, run_after=None, owner=None):
    if kind not in HANDLERS:
        raise ValidationError("This job kind is not registered.")
    if kind == "operator.refresh":
        raise ValidationError("Use enqueue_operator_refresh for coalesced operator work.")
    if not isinstance(dedupe_key, str) or not dedupe_key or len(dedupe_key) > 200 or "\x00" in dedupe_key:
        raise ValidationError("Supply a stable job key of at most 200 characters.")
    payload, digest = _payload(payload)
    due = _aware(run_after or timezone.now())
    owner_id = getattr(owner, "pk", owner)
    job, created = OutboxJob.objects.get_or_create(dedupe_key=dedupe_key, defaults={
        "kind": kind, "owner_id": owner_id, "payload": payload, "payload_digest": digest, "run_after": due})
    if not created and (job.kind != kind or job.owner_id != owner_id or job.payload_digest != digest):
        raise JobConflict("This job key already identifies different work.")
    return job


@transaction.atomic
def enqueue_operator_refresh(*, owner_id, revision):
    """One reusable job per owner; a newer event supersedes an in-flight read."""
    try:
        owner_id = int(owner_id)
        revision = str(uuid.UUID(str(revision)))
    except (TypeError, ValueError):
        raise ValidationError("Supply a current operator owner and revision.") from None
    # A reconciliation read can race a later source mutation. Serialize with
    # source invalidation before taking the job lock and prefer its canonical
    # revision so an older sweep cannot replace more recent pending work.
    from tasker.operator_models import OperatorState
    state = OperatorState.objects.select_for_update().filter(owner_id=owner_id).only("revision").first()
    if state is not None:
        revision = str(state.revision)
    payload, digest = _payload({"owner_id": owner_id, "revision": revision})
    job, created = OutboxJob.objects.get_or_create(dedupe_key=f"operator:{owner_id}", defaults={
        "kind": "operator.refresh", "owner_id": owner_id, "payload": payload,
        "payload_digest": digest, "run_after": timezone.now()})
    if created:
        return job
    job = OutboxJob.objects.select_for_update().get(pk=job.pk)
    if job.kind != "operator.refresh" or job.owner_id != owner_id:
        raise JobConflict("This operator job key identifies different work.")
    if job.payload_digest == digest:
        return job
    job.payload, job.payload_digest, job.updated_at = payload, digest, timezone.now()
    # The replacement revision owns a fresh failure budget, including when
    # the preceding revision was already using its final leased attempt.
    job.attempts = 0
    if job.state != OutboxJob.State.RUNNING:
        job.state, job.run_after, job.attempts = OutboxJob.State.QUEUED, timezone.now(), 0
        job.finished_at, job.lease_token, job.lease_expires_at = None, None, None
        job.locked_by, job.last_error_code, job.result = "", "", {}
    job.save()
    return job


def current_lease():
    return _current_lease.get()


def assert_current_job_lease():
    lease = current_lease()
    if lease is None or not OutboxJob.objects.filter(pk=lease.job_id, state=OutboxJob.State.RUNNING,
            lease_token=lease.token, lease_expires_at__gt=timezone.now(), payload_digest=lease.payload_digest).exists():
        if lease is not None:
            current = OutboxJob.objects.filter(pk=lease.job_id, state=OutboxJob.State.RUNNING,
                lease_token=lease.token, lease_expires_at__gt=timezone.now()).values("payload_digest").first()
            if current and current["payload_digest"] != lease.payload_digest:
                raise JobSuperseded("A newer operator revision superseded this read.")
        raise LostJobLease("The job lease is no longer current.")
    return lease


def heartbeat(*, worker_id="local", role="worker", last_job_id=None, completed=0):
    if not isinstance(worker_id, str) or not SAFE_WORKER.fullmatch(worker_id):
        raise ValidationError("Use a short worker name containing letters, digits, dots, colons or hyphens.")
    if role not in ("worker", "scheduler"):
        raise ValidationError("Choose a worker or scheduler heartbeat.")
    record, _ = JobWorkerHeartbeat.objects.get_or_create(name=worker_id, defaults={"role": role})
    JobWorkerHeartbeat.objects.filter(pk=record.pk).update(role=role, seen_at=timezone.now(),
        last_job_id=last_job_id, completed=F("completed")+completed)


@transaction.atomic
def claim_next_job(*, worker_id="local", lease_seconds=120, now=None):
    if connection.vendor != "postgresql" or not connection.features.has_select_for_update_skip_locked:
        raise ValidationError("Tasker jobs require PostgreSQL with skip-locked row support.")
    now = _aware(now or timezone.now())
    if not isinstance(worker_id, str) or not SAFE_WORKER.fullmatch(worker_id):
        raise ValidationError("Choose a valid worker name.")
    if type(lease_seconds) is not int or not 10 <= lease_seconds <= 3600:
        raise ValidationError("Choose a job lease from 10 to 3,600 seconds.")
    # Expired leases can be reclaimed even after a worker exits mid-request.
    for _ in range(25):
        job = OutboxJob.objects.select_for_update(skip_locked=True).filter(
            Q(state=OutboxJob.State.QUEUED, run_after__lte=now)
            | Q(state=OutboxJob.State.RUNNING, lease_expires_at__lte=now)
        ).order_by("run_after", "created_at", "pk").first()
        if job is None:
            return None
        if job.attempts >= job.max_attempts:
            expired = job.state == OutboxJob.State.RUNNING
            job.state, job.finished_at, job.updated_at = OutboxJob.State.FAILED, now, now
            job.last_error_code = "lease_exhausted" if expired else "attempts_exhausted"
            job.lease_token, job.lease_expires_at, job.locked_by = None, None, ""
            job.save()
            continue
        job.state, job.attempts = OutboxJob.State.RUNNING, job.attempts+1
        job.lease_token, job.lease_expires_at = uuid.uuid4(), now+timedelta(seconds=lease_seconds)
        job.locked_by, job.started_at, job.finished_at = worker_id, now, None
        job.updated_at, job.last_error_code = now, ""
        job.save()
        return job
    return None


def _fence(job, values, *, allow_superseded=False):
    now = timezone.now()
    query = OutboxJob.objects.filter(pk=job.pk, state=OutboxJob.State.RUNNING,
        lease_token=job.lease_token, lease_expires_at__gt=now)
    if not allow_superseded:
        query = query.filter(payload_digest=job.payload_digest)
    updated = query.update(updated_at=now, **values)
    if not updated:
        current = OutboxJob.objects.filter(pk=job.pk).values("lease_token", "payload_digest").first()
        if current and current["lease_token"] == job.lease_token and current["payload_digest"] != job.payload_digest:
            raise JobSuperseded("A newer operator revision superseded this read.")
        raise LostJobLease("The job lease expired or another worker reclaimed it.")


def _terminal_values(state, *, result=None, code=""):
    return {"state": state, "result": result or {}, "last_error_code": code, "finished_at": timezone.now(),
        "lease_token": None, "lease_expires_at": None, "locked_by": ""}


def _result_summary(result):
    """Keep diagnostics below 4 KiB even if a handler returns an oversized map."""
    safe = {}
    if not isinstance(result, dict):
        return safe
    for key, value in result.items():
        if not isinstance(key, str) or not SAFE_CODE.fullmatch(key):
            continue
        if (value is None or type(value) is bool or type(value) is int and -(2**63) <= value < 2**63
                or isinstance(value, str) and SAFE_CODE.fullmatch(value)):
            safe[key] = value
            if len(safe) == 24:
                break
    return safe


def _deferred_values(job, deferred):
    return {"state": OutboxJob.State.QUEUED, "run_after": max(deferred.run_after, timezone.now()+timedelta(seconds=1)),
        "attempts": max(0, job.attempts-1), "deferrals": F("deferrals")+1, "last_error_code": deferred.reason,
        "lease_token": None, "lease_expires_at": None, "locked_by": "", "finished_at": None}


def _retry_values(job, *, code="handler_error", run_after=None):
    if job.attempts >= job.max_attempts:
        return _terminal_values(OutboxJob.State.FAILED, code=code), "failed"
    delay = min(3600, 30*(2**(job.attempts-1)))
    return {"state": OutboxJob.State.QUEUED,
        "run_after": max(run_after, timezone.now()+timedelta(seconds=1)) if run_after is not None else timezone.now()+timedelta(seconds=delay),
        "last_error_code": code, "lease_token": None, "lease_expires_at": None, "locked_by": ""}, "retry"


def _handler_for(kind):
    module, name = HANDLERS[kind]
    return globals()[name] if module is None else getattr(importlib.import_module(module), name)


def _operator_refresh(*, payload, job=None):
    from tasker.operator_models import OperatorState
    from tasker.services.operator import build_operator
    user = get_user_model().objects.filter(pk=payload.get("owner_id"), is_active=True).first()
    state = OperatorState.objects.filter(owner=user).first() if user else None
    if user is None or not user.has_perm("tasker.view_task") or state is None:
        return JobCancelled("owner_unavailable")
    if str(state.revision) != payload.get("revision"):
        return JobCancelled("obsolete_revision")
    result = build_operator(user=user)
    return {"reviewed": result["counts"]["reviewed"], "stale": bool(result["freshness"].get("stale"))}


def run_claimed_job(job):
    """Run one claimed row; a stale worker can never commit domain DB effects."""
    if not isinstance(job, OutboxJob) or job.state != OutboxJob.State.RUNNING or not job.lease_token:
        raise JobConflict("Claim a job before running it.")
    lease = JobLease(job.pk, job.lease_token, job.payload_digest, job.locked_by)
    context_token = _current_lease.set(lease)
    outcome = "lost_lease"
    try:
        try:
            with transaction.atomic():
                assert_current_job_lease()
                if _payload(job.payload)[1] != job.payload_digest:
                    raise JobPermanentError("payload_integrity_changed")
                result = _handler_for(job.kind)(payload=job.payload, job=job)
                if isinstance(result, JobDeferred):
                    _fence(job, _deferred_values(job, result))
                    outcome = "deferred"
                elif isinstance(result, JobRetry):
                    values, outcome = _retry_values(job, code=result.code, run_after=result.run_after)
                    _fence(job, values)
                elif isinstance(result, JobCancelled):
                    _fence(job, _terminal_values(OutboxJob.State.CANCELLED, code=result.reason))
                    outcome = "cancelled"
                else:
                    # Result summaries are deliberately constrained to counts,
                    # booleans and generic codes, never copied exception text.
                    _fence(job, _terminal_values(OutboxJob.State.SUCCEEDED, result=_result_summary(result)))
                    outcome = "succeeded"
        except JobSuperseded:
            with transaction.atomic():
                _fence(job, {"state": OutboxJob.State.QUEUED, "run_after": timezone.now(), "attempts": 0,
                    "lease_token": None, "lease_expires_at": None, "locked_by": "", "last_error_code": "superseded"}, allow_superseded=True)
            outcome = "superseded"
        except JobDeferred as deferred:
            with transaction.atomic(): _fence(job, _deferred_values(job, deferred))
            outcome = "deferred"
        except JobCancelled as cancelled:
            with transaction.atomic(): _fence(job, _terminal_values(OutboxJob.State.CANCELLED, code=cancelled.reason))
            outcome = "cancelled"
        except LostJobLease:
            outcome = "lost_lease"
        except Exception as error:
            permanent = isinstance(error, (JobPermanentError, ValidationError, PermissionDenied, KeyError))
            code = error.code if isinstance(error, JobPermanentError) else "invalid_job" if isinstance(error, (ValidationError, KeyError)) else "access_denied" if isinstance(error, PermissionDenied) else "handler_error"
            with transaction.atomic():
                if permanent or job.attempts >= job.max_attempts:
                    _fence(job, _terminal_values(OutboxJob.State.FAILED, code=code))
                    outcome = "failed"
                else:
                    values, outcome = _retry_values(job, code=code)
                    _fence(job, values)
    except LostJobLease:
        outcome = "lost_lease"
    finally:
        _current_lease.reset(context_token)
    return {"job_id": str(job.pk), "outcome": outcome}


def run_worker_once(*, worker_id="local", limit=1, lease_seconds=120):
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose from 1 to 1,000 jobs per pass.")
    heartbeat(worker_id=worker_id)
    results = []
    for _ in range(limit):
        job = claim_next_job(worker_id=worker_id, lease_seconds=lease_seconds)
        if job is None: break
        results.append(run_claimed_job(job))
        heartbeat(worker_id=worker_id, last_job_id=job.pk, completed=int(results[-1]["outcome"] in ("succeeded", "cancelled")))
    return results


@transaction.atomic
def retry_failed(*, user, job_id):
    actor = get_user_model().objects.filter(pk=getattr(user, "pk", None), is_active=True).first()
    if actor is None or not actor.is_superuser:
        raise PermissionDenied
    job = OutboxJob.objects.select_for_update().filter(pk=job_id).first()
    if job is None or job.state != OutboxJob.State.FAILED:
        raise JobConflict("Only a failed job can be retried here.")
    job.state, job.attempts, job.run_after = OutboxJob.State.QUEUED, 0, timezone.now()
    job.finished_at, job.last_error_code, job.result, job.updated_at = None, "", {}, timezone.now()
    job.save()
    return job


def scheduler_tick(*, scheduler_id="local-scheduler", limit=100, prune=False, retention_days=None):
    """One bounded repair pass; a supervised CLI supplies the actual schedule."""
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose a sweep limit from 1 to 1,000.")
    heartbeat(worker_id=scheduler_id, role="scheduler")
    result = {}
    for module_name, function_name, label in (
        ("tasker.services.coordination", "reconcile_coordination", "coordination"),
        ("tasker.services.notifications", "reconcile_notifications", "notifications"),
        ("tasker.services.reporting", "reconcile_reports", "reports"),
    ):
        function = getattr(importlib.import_module(module_name), function_name)
        result[label] = function(limit=limit)
    # Existing owner states only. The same constant-key operator job is reused.
    from tasker.operator_models import OperatorState
    operator_jobs = OutboxJob.objects.filter(kind="operator.refresh", owner_id=OuterRef("owner_id")).annotate(
        reference_revision=KeyTextTransform("revision", "payload"))
    states = OperatorState.objects.annotate(
        queued_revision=Subquery(operator_jobs.values("reference_revision")[:1], output_field=CharField()),
        current_revision=Cast("revision", output_field=CharField()),
    ).filter(Q(queued_revision__isnull=True) | ~Q(queued_revision=F("current_revision")))
    for state in states.order_by("owner_id")[:limit]:
        enqueue_operator_refresh(owner_id=state.owner_id, revision=state.revision)
    if prune:
        result["pruned"] = prune_terminal_jobs(retention_days=retention_days, limit=limit)
    return result


@transaction.atomic
def prune_terminal_jobs(*, retention_days=None, limit=100):
    days = retention_days if retention_days is not None else getattr(settings, "TASKER_JOB_RETENTION_DAYS", 30)
    if type(days) is not int or not 1 <= days <= 3650 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose a retention period from 1 to 3,650 days and a bounded limit.")
    # Keep dedupe tombstones indefinitely: deleting a completed outbox row could
    # allow an old source event to create the action twice. Prune payload/result
    # detail only; failed/pending work and delivery ledgers are never purged.
    rows = list(OutboxJob.objects.select_for_update(skip_locked=True).filter(
        state__in=(OutboxJob.State.SUCCEEDED, OutboxJob.State.CANCELLED),
        finished_at__lt=timezone.now()-timedelta(days=days),
    ).exclude(payload={}, result={}).exclude(kind="operator.refresh", result={}).order_by("finished_at", "pk")[:limit])
    for job in rows:
        # Keep the digest/kind/key and references necessary for dedupe. Operator
        # refresh rows are reusable, so pruning them only clears result detail.
        job.result, job.updated_at = {}, timezone.now()
        if job.kind != "operator.refresh": job.payload = {}
        job.save(update_fields=("payload", "result", "updated_at"))
    return len(rows)


def jobs_health(*, stale_seconds=300):
    if type(stale_seconds) is not int or not 30 <= stale_seconds <= 86400:
        raise ValidationError("Choose a health window from 30 to 86,400 seconds.")
    now = timezone.now()
    counts = {row["state"]: row["count"] for row in OutboxJob.objects.values("state").annotate(count=Count("pk"))}
    queued = OutboxJob.objects.filter(state=OutboxJob.State.QUEUED).aggregate(oldest=Min("created_at"))["oldest"]
    expired = OutboxJob.objects.filter(state=OutboxJob.State.RUNNING, lease_expires_at__lte=now).count()
    recent = JobWorkerHeartbeat.objects.filter(role="worker", seen_at__gte=now-timedelta(seconds=stale_seconds)).exists()
    return {"healthy": not counts.get("FAILED", 0) and not expired and (not counts.get("QUEUED", 0) or recent),
        "counts": counts, "expired_leases": expired, "worker_recent": recent,
        "oldest_queued_seconds": max(0, int((now-queued).total_seconds())) if queued else None}
