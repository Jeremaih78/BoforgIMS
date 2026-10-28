"""Bounded live prerequisite readiness and serialized, scoped DAG mutations."""
from dataclasses import dataclass
import hashlib
import json
import uuid

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.db.models import F, Q

from tasker.dependency_models import DependencyGraphState, TaskDependency
from tasker.models import Task, TaskActivity
from tasker.organization import require_task_access
from tasker.policies import visible_tasks_for


MAX_NODES = 1000
MAX_EDGES = 2000
MAX_DEPTH = 128


class DependencyConflict(ValueError):
    pass


@dataclass(frozen=True)
class PrerequisiteSummary:
    dependency_id: int
    task_id: int | None
    title: str
    url: str
    completed: bool
    accessible: bool = True


@dataclass(frozen=True)
class DependencyReadiness:
    ready: bool
    code: str
    reason: str
    version: str
    prerequisites: tuple = ()
    count: int = 0


def _actor(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def _scope(task):
    if task.company_id:
        return f"company:{task.company_id}:team:{task.team_id or 0}"
    return f"personal:{task.owner_id}"


def _same_scope(left, right):
    return (left.company_id == right.company_id and left.team_id == right.team_id
            and (bool(left.company_id) or left.owner_id == right.owner_id))


def _identifier(value):
    value = getattr(value, "pk", value)
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).isdecimal() or len(str(value)) > 18 or int(value) < 1:
        raise ValidationError("Choose a valid task or prerequisite link.")
    return int(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _graph_revision(scope_key):
    state = DependencyGraphState.objects.filter(pk=scope_key).values_list("revision", flat=True).first()
    return str(state) if state else "empty"


def _lock_graph(scope_key):
    """Call only after endpoint Task locks; never lock more Tasks afterwards."""
    if connection.vendor != "postgresql":
        raise RuntimeError("Dependency mutation requires PostgreSQL serialization.")
    lock = int.from_bytes(hashlib.sha256(("tasker.dependencies:" + scope_key).encode()).digest()[:8], "big", signed=True)
    with connection.cursor() as cursor:
        cursor.execute("SELECT pg_advisory_xact_lock(%s)", [lock])


def _bump(scope_key):
    DependencyGraphState.objects.update_or_create(scope_key=scope_key, defaults={"revision": uuid.uuid4()})


def _chain(start_ids):
    """Batch each frontier; graph size and depth are explicit safety bounds."""
    visited, edges, frontier = set(start_ids), [], set(start_ids)
    for _ in range(MAX_DEPTH):
        if not frontier:
            return edges, visited, False
        found = list(TaskDependency.objects.filter(task_id__in=frontier).order_by("pk").values("pk", "task_id", "prerequisite_id")[:MAX_EDGES + 1])
        edges.extend(found)
        if len(edges) > MAX_EDGES:
            return edges[:MAX_EDGES], visited, True
        following = {item["prerequisite_id"] for item in found} - visited
        visited.update(following)
        if len(visited) > MAX_NODES:
            return edges, visited, True
        frontier = following
    return edges, visited, bool(frontier)


def _state(task):
    return [task.pk, task.status, task.is_deleted, task.archived, task.owner_id, task.company_id, task.team_id,
            task.completed_at.isoformat() if task.completed_at else None]


def _has_cycle(edges):
    adjacency, incoming = {}, {}
    for edge in edges:
        left, right = edge["task_id"], edge["prerequisite_id"]
        adjacency.setdefault(left, []).append(right)
        incoming.setdefault(left, 0)
        incoming[right] = incoming.get(right, 0) + 1
    queue = [node for node, count in incoming.items() if count == 0]
    removed = 0
    while queue:
        node = queue.pop(); removed += 1
        for target in adjacency.get(node, ()):
            incoming[target] -= 1
            if incoming[target] == 0:
                queue.append(target)
    return removed != len(incoming)


def readiness_for_tasks(*, tasks, user):
    """Shared bounded selector. No notes, private captures or inaccessible titles."""
    actor = _actor(user)
    ids = list(dict.fromkeys(_identifier(task) for task in tasks))
    if len(ids) > 100:
        raise ValidationError("Check at most 100 tasks at once.")
    if not ids:
        return {}
    roots = {task.pk: task for task in Task.all_objects.filter(pk__in=ids).only("pk", "owner_id", "company_id", "team_id")}
    scope_keys = {_scope(task) for task in roots.values()}
    before = dict(DependencyGraphState.objects.filter(pk__in=scope_keys).values_list("scope_key", "revision"))
    edges, reached, limited = _chain(ids)
    records = {task.pk: task for task in Task.all_objects.filter(pk__in=reached).only(
        "pk", "slug", "title", "status", "is_deleted", "archived", "owner_id", "company_id", "team_id", "completed_at")}
    visible = set(visible_tasks_for(actor).filter(pk__in=reached).values_list("pk", flat=True))
    revisions = dict(DependencyGraphState.objects.filter(pk__in=scope_keys).values_list("scope_key", "revision"))
    changed_scopes = {key for key in scope_keys if before.get(key) != revisions.get(key)}
    # Structured requests remain a readiness constraint even if a stale/manual
    # status edit clears BLOCKED. Receipt alone cannot make waiting work ready.
    from django.conf import settings
    coordination = {}
    if getattr(settings, "TASKER_COORDINATION_ENABLED", False):
        from tasker.coordination_models import CoordinationRequest
        for request_id, task_id, revision in CoordinationRequest.objects.filter(
            task_id__in=ids, status__in=("OPEN", "ACKNOWLEDGED"), blocks_task=True,
            task__owner_id=F("source_owner_id"), task__is_deleted=False, task__archived=False,
            task__status__in=(Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED),
        ).filter(Q(task__company__isnull=True, source_company__isnull=True) | Q(task__company_id=F("source_company_id"))).filter(
            Q(task__team__isnull=True, source_team__isnull=True) | Q(task__team_id=F("source_team_id"))
        ).values_list("pk", "task_id", "revision"):
            coordination.setdefault(task_id, []).append((request_id, str(revision)))
    outgoing = {}
    for edge in edges:
        outgoing.setdefault(edge["task_id"], []).append(edge)
    result = {}
    for task_id in ids:
        task = records.get(task_id)
        if task is None or task_id not in visible:
            result[task_id] = DependencyReadiness(False, "unavailable", "This task is no longer available in your current scope.", _digest([task_id, "unavailable"]))
            continue
        seen, queue, relevant = {task_id}, [task_id], []
        # READ COMMITTED frontiers and state queries must not combine old
        # transitive edges with newer states. Mutation writers bump this
        # per-scope revision atomically; reject a moving graph without taking
        # additional Task locks or reversing Focus/scheduling lock order.
        moved = _scope(task) in changed_scopes or task_id not in roots or _scope(task) != _scope(roots[task_id])
        code = "graph_limit" if limited else "graph_changed" if moved else "ready"
        cycle = False
        while queue:
            current = queue.pop()
            for edge in outgoing.get(current, ()):
                relevant.append(edge)
                prerequisite_id = edge["prerequisite_id"]
                prerequisite = records.get(prerequisite_id)
                origin = records.get(edge["task_id"])
                if prerequisite_id == task_id:
                    cycle = True
                if prerequisite is None or origin is None or prerequisite.is_deleted or prerequisite_id not in visible or not _same_scope(origin, prerequisite):
                    code = "unavailable"
                elif (prerequisite.archived or prerequisite.status != Task.Status.COMPLETED) and code == "ready":
                    code = "waiting"
                if prerequisite_id not in seen:
                    seen.add(prerequisite_id)
                    queue.append(prerequisite_id)
        if cycle or _has_cycle(relevant):
            code = "cycle"
        if code == "ready" and coordination.get(task_id):
            code = "coordination"
        reason = {"ready": "All prerequisites are complete." if outgoing.get(task_id) else "No prerequisites recorded.", "waiting": "Waiting for unfinished prerequisites in this dependency chain.",
                  "unavailable": "A prerequisite is unavailable in your current scope. Ask an authorized manager to review the link.",
                  "graph_limit": "This dependency chain needs review before work can start.",
                  "graph_changed": "Prerequisite links changed while checking readiness. Reload and review them before starting work.",
                  "cycle": "This dependency chain needs repair before work can start.",
                  "coordination": "Resolve the active coordination request before starting this task."}[code]
        states = [_state(records[value]) if value in records else [value, "missing"] for value in sorted(seen)]
        version = _digest([_scope(task), str(revisions.get(_scope(task), "empty")), sorted((edge["pk"], edge["task_id"], edge["prerequisite_id"]) for edge in relevant), states, sorted(visible & seen), limited, moved])
        if coordination.get(task_id):
            version = _digest([version, sorted(coordination[task_id])])
        summaries = []
        for edge in outgoing.get(task_id, ()):
            pre = records.get(edge["prerequisite_id"])
            accessible = pre is not None and pre.pk in visible and not pre.is_deleted and _same_scope(task, pre)
            summaries.append(PrerequisiteSummary(edge["pk"], pre.pk, pre.title, pre.get_absolute_url(), pre.status == Task.Status.COMPLETED and not pre.archived)
                             if accessible else PrerequisiteSummary(edge["pk"], None, "Unavailable prerequisite", "", False, False))
        result[task_id] = DependencyReadiness(code == "ready", code, reason, version, tuple(summaries), len(outgoing.get(task_id, ())))
    return result


def readiness_for(*, task, user):
    task_id = _identifier(task)
    result = readiness_for_tasks(tasks=[task_id], user=user)[task_id]
    if result.code == "unavailable" and not visible_tasks_for(user).filter(pk=task_id).exists():
        raise PermissionDenied
    return result


def dependency_version(*, task, user):
    return readiness_for(task=task, user=user).version


def assert_task_ready(*, task, user, expected_version=None):
    result = readiness_for(task=task, user=user)
    if expected_version is not None and expected_version != result.version:
        raise DependencyConflict("Prerequisites changed. Review the current plan before accepting it.")
    if not result.ready:
        raise ValueError(result.reason)
    return result


def dependency_candidates(*, task, user):
    tasks = visible_tasks_for(user).exclude(pk=task.pk).filter(company_id=task.company_id, team_id=task.team_id)
    if not task.company_id:
        tasks = tasks.filter(owner_id=task.owner_id)
    return tasks.order_by("title", "pk")[:100]


def validate_dependency_scope(*, candidate, previous=None):
    """Called under the changed Task lock; edge writers lock both endpoints."""
    if previous is None or (previous.owner_id, previous.company_id, previous.team_id) == (candidate.owner_id, candidate.company_id, candidate.team_id):
        return
    edges = TaskDependency.objects.filter(Q(task_id=candidate.pk) | Q(prerequisite_id=candidate.pk))
    other_ids = set()
    for row in edges.values("task_id", "prerequisite_id")[:MAX_EDGES + 1]:
        other_ids.update((row["task_id"], row["prerequisite_id"]))
    other_ids.discard(candidate.pk)
    if len(other_ids) > MAX_NODES:
        raise ValidationError("Review prerequisite links before changing this task's scope.")
    others = Task.all_objects.filter(pk__in=other_ids)
    if any(not _same_scope(candidate, other) for other in others):
        raise ValidationError("Remove or move prerequisite links before changing this task's owner or company/team scope.")


@transaction.atomic
def add_dependency(*, task, prerequisite, user, expected_version):
    user = _actor(user)
    task_id, prerequisite_id = _identifier(task), _identifier(prerequisite)
    if task_id == prerequisite_id:
        raise ValidationError("A task cannot require itself.")
    tasks = {item.pk: item for item in Task.all_objects.select_for_update().filter(pk__in=sorted((task_id, prerequisite_id))).order_by("pk")}
    if len(tasks) != 2:
        raise PermissionDenied
    dependent, required = tasks[task_id], tasks[prerequisite_id]
    if not _same_scope(dependent, required):
        if not visible_tasks_for(user).filter(pk=required.pk).exists():
            raise PermissionDenied
        raise ValidationError("Prerequisites must share the task's company/team scope, or its personal owner.")
    require_task_access(user, dependent, capability="tasker.view_task")
    require_task_access(user, required, change=False, capability="tasker.view_task")
    _lock_graph(_scope(dependent))
    existing = TaskDependency.objects.filter(task=dependent, prerequisite=required).first()
    if existing:
        return existing
    current = readiness_for(task=dependent, user=user)
    if not isinstance(expected_version, str) or expected_version != current.version:
        raise DependencyConflict("Prerequisites changed. Reload before adding this link.")
    edges, reached, limited = _chain([prerequisite_id])
    if limited:
        raise ValidationError("This dependency chain is too large to change safely. Split or review it first.")
    if _has_cycle(edges):
        raise ValidationError("Repair the existing dependency cycle before adding links.")
    if task_id in reached:
        raise ValidationError("That prerequisite would create a dependency cycle.")
    edge = TaskDependency.objects.create(task=dependent, prerequisite=required, created_by=user, updated_by=user)
    _bump(_scope(dependent))
    TaskActivity.objects.create(task=dependent, event_type=TaskActivity.EventType.UPDATED,
        description="Prerequisite added", changes={"dependency_action": "added", "dependency_id": edge.pk}, created_by=user, updated_by=user)
    return edge


@transaction.atomic
def remove_dependency(*, task, dependency_id, user, expected_version):
    user = _actor(user)
    task_id, edge_id = _identifier(task), _identifier(dependency_id)
    # Existing prerequisite may now be private or deleted; removing a link only
    # changes the authorized dependent and never discloses that record.
    dependent = Task.all_objects.select_for_update().filter(pk=task_id).first()
    if dependent is None:
        raise PermissionDenied
    require_task_access(user, dependent, capability="tasker.view_task")
    _lock_graph(_scope(dependent))
    edge = TaskDependency.all_objects.filter(pk=edge_id, task=dependent).first()
    if edge is None:
        raise PermissionDenied
    if edge.is_deleted:
        return edge
    current = readiness_for(task=dependent, user=user)
    if not isinstance(expected_version, str) or expected_version != current.version:
        raise DependencyConflict("Prerequisites changed. Reload before removing this link.")
    edge.delete(user=user)
    _bump(_scope(dependent))
    TaskActivity.objects.create(task=dependent, event_type=TaskActivity.EventType.UPDATED,
        description="Prerequisite removed", changes={"dependency_action": "removed", "dependency_id": edge.pk}, created_by=user, updated_by=user)
    return edge
