"""Bounded current work facts. No model input can choose an actor or widen scope.

Task records occur once. All queues and links use those canonical IDs. Manager
inputs contain shared work facts, never captures, reflections or private text.
"""
from datetime import datetime, time, timedelta
import hashlib
import json
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Case, Count, IntegerField, Max, Q, When
from django.utils import timezone

from tasker.capture_models import CaptureItem
from tasker.goal_models import TaskContribution
from tasker.models import DailyPlan, Task, TaskActivity, TaskChecklistItem, TaskTimeEntry
from tasker.planning_models import ScheduleBlock, WorkPolicy
from tasker.services.coordination import scoped_tasks_for, visible_requests_for
from tasker.services.dependencies import readiness_for_tasks
from tasker.services.effort import effort_for_task
from tasker.services.goals import visible_goals_for, visible_initiatives_for
from tasker.services.operator import build_operator
from tasker.services.planner import plan_version
from tasker.services.reporting import report_teams_for
from tasker.services.workbench import work_version
from tasker.selectors import priority_rank_expression

from .exceptions import AIConfigurationError
from .workflow_schema import FEATURE_ACTIONS, normalize_feature


TASK_LIMIT = 75
GOAL_LIMIT = 12
COORDINATION_LIMIT = 20
ACTIVE = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _identifier(value):
    if value is not None and (type(value) is not int or not 1 <= value <= 9223372036854775807):
        raise ValidationError("Choose a valid current source.")
    return value


def task_children_proofs(tasks):
    """Personal child-state proofs, including soft deletes, without event bodies."""
    ids = [getattr(task, "pk", task) for task in tasks]
    states = {pk: {"checklist": [], "activity": [0, None], "blocks": []} for pk in ids}
    for row in TaskChecklistItem.all_objects.filter(task_id__in=ids).order_by("pk").values_list(
            "task_id", "pk", "title", "position", "is_completed", "updated_at", "is_deleted"):
        states[row[0]]["checklist"].append([str(value) for value in row[1:]])
    for row in TaskActivity.all_objects.filter(task_id__in=ids).values("task_id").annotate(total=Count("pk"), last=Max("pk")):
        states[row["task_id"]]["activity"] = [row["total"], row["last"]]
    for row in ScheduleBlock.all_objects.filter(task_id__in=ids).order_by("pk").values_list(
            "task_id", "pk", "revision", "state", "starts_at", "ends_at", "is_deleted"):
        states[row[0]]["blocks"].append([str(value) for value in row[1:]])
    return {str(pk): _digest(state) for pk, state in states.items()}


def task_children_proof(task):
    pk = getattr(task, "pk", task)
    return task_children_proofs([pk])[str(pk)]


def authorize_workflow_sources(*, user, context):
    """Narrow current read checks for retained context, without rebuilding facts.

    A historical version may differ from today's version, but every underlying
    source must still be readable. This performs no operator evaluation and
    never reads capture bodies, reflections or conversation history.
    """
    if not user or not user.is_authenticated or type(context) is not dict:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perms(("tasker.view_task", "tasker.use_ai")) or type(context.get("actor_id")) is not int or context.get("actor_id") != actor.pk:
        raise PermissionDenied
    source, proofs = context.get("source", {}), context.get("proofs", {})
    if type(source) is not dict or type(proofs) is not dict:
        raise PermissionDenied
    try:
        team_id = _identifier(source.get("team_id"))
        task_id = _identifier(source.get("task_id"))
        capture_id = _identifier(source.get("capture_id"))
        def proof_ids(key):
            values = proofs.get(key, {})
            if type(values) is not dict:
                raise PermissionDenied
            result = set()
            for value in values:
                if type(value) is not str or not value.isdecimal():
                    raise PermissionDenied
                result.add(_identifier(int(value)))
            return result
        tasks, goals, requests, captures = (proof_ids(key) for key in ("tasks", "goals", "coordination", "captures"))
    except (ValidationError, ValueError, TypeError, OverflowError):
        raise PermissionDenied from None
    scope = context.get("scope")
    if scope not in ("PERSONAL", "MANAGER") or (scope == "PERSONAL" and team_id is not None):
        raise PermissionDenied
    if scope == "MANAGER":
        if (team_id is None or not report_teams_for(actor).filter(pk=team_id).exists()
                or captures or proofs.get("plans") or context.get("capture") is not None or context.get("meeting_text")):
            raise PermissionDenied
    readable = scoped_tasks_for(actor)
    readable = readable.filter(owner=actor) if scope == "PERSONAL" else readable.filter(team_id=team_id)
    if task_id is not None and task_id not in tasks:
        raise PermissionDenied
    if readable.filter(pk__in=tasks).count() != len(tasks):
        raise PermissionDenied
    if captures != ({capture_id} if capture_id is not None else set()):
        raise PermissionDenied
    if captures and (scope != "PERSONAL" or CaptureItem.objects.filter(pk__in=captures, owner=actor,
            visibility=CaptureItem.Visibility.PRIVATE, triage_state=CaptureItem.TriageState.INBOX).count() != len(captures)):
        raise PermissionDenied
    if visible_goals_for(actor).filter(pk__in=goals).count() != len(goals):
        raise PermissionDenied
    if visible_requests_for(actor).filter(pk__in=requests).count() != len(requests):
        raise PermissionDenied
    return actor


def _capacity(value):
    keys = ("known", "timezone_name", "work_minutes", "excluded_minutes", "buffer_minutes",
            "usable_minutes", "committed_minutes", "available_minutes", "overload_minutes",
            "unknown_effort_count", "outside_hours_count", "availability_conflict_count", "overlap_conflict_count")
    result = {key: value.get(key) for key in keys}
    result["warnings"] = value.get("warnings", [])[:8]
    for key in ("work_intervals", "free_intervals", "booked_intervals"):
        intervals = value.get(key, [])
        result[key] = [[left.isoformat(), right.isoformat()] for left, right in intervals[:20]]
        result[key + "_omitted"] = max(0, len(intervals) - 20)
    return result


def _goals(actor, tasks, team):
    queryset = visible_goals_for(actor).filter(status="ACTIVE")
    if team:
        queryset = queryset.filter(company_id=team.company_id).filter(Q(team__isnull=True) | Q(team=team))
    total = queryset.count()
    # Linked direction is selected before unrelated direction, with an honest
    # omission count. Accepted outcome progress is not inferred from Tasks.
    links = TaskContribution.objects.filter(task_id__in=[task.pk for task in tasks],
        initiative__in=visible_initiatives_for(actor), initiative__goal__in=queryset).values_list("task_id", "initiative__goal_id")
    goal_links = dict(links)
    rows = list(queryset.annotate(_linked=Case(When(pk__in=goal_links.values(), then=0),
        default=1, output_field=IntegerField())).order_by("_linked", "pk")[:GOAL_LIMIT])
    records = [{"id": row.pk, "title": row.title, "reference": row.reference,
        "start_date": str(row.start_date) if row.start_date else None,
        "end_date": str(row.end_date) if row.end_date else None,
        "status": row.status, "outcome_progress": None,
        "progress_basis": "Accepted business evidence must be reviewed separately; task completion is not outcome evidence."} for row in rows]
    return records, goal_links, total


def _refresh(context):
    ids = {row["id"] for row in context["tasks"]}
    owner_ids = {row["owner_id"] for row in context["tasks"]}
    context["people"] = [row for row in context["people"] if row["id"] in owner_ids]
    context["proofs"]["people"] = {str(row["id"]): _digest(row) for row in context["people"]}
    for key, value in context["references"].items():
        context["references"][key] = ([pk for pk in value if pk in ids]
            if isinstance(value, list) else value if value in ids else None)
    for key in ("tasks", "dependencies", "efforts", "task_children"):
        context["proofs"][key] = {pk: value for pk, value in context["proofs"].get(key, {}).items() if int(pk) in ids}
    context["coordination"] = [row for row in context["coordination"] if row["task_id"] in ids]
    request_ids = {str(row["id"]) for row in context["coordination"]}
    context["proofs"]["coordination"] = {pk: value for pk, value in context["proofs"].get("coordination", {}).items() if pk in request_ids}
    context["proofs"]["goals"] = {str(row["id"]): _digest(row) for row in context["goals"]}
    goal_ids = {row["id"] for row in context["goals"]}
    for row in context["tasks"]:
        if row.get("goal_id") not in goal_ids:
            row["goal_id"] = None
    context["proofs"]["contributions"] = {str(row["id"]): row["goal_id"] for row in context["tasks"]}
    counts = context["counts"]
    counts.update(included_task_count=len(ids), omitted_task_count=max(0, counts["task_count"] - len(ids)),
                  included_goal_count=len(context["goals"]), omitted_goal_count=max(0, counts["goal_count"] - len(context["goals"])),
                  included_coordination_count=len(context["coordination"]),
                  omitted_coordination_count=max(0, counts["coordination_count"] - len(context["coordination"])))
    for field in ("planned", "priority"):
        included = len(context["references"][field + "_task_ids"])
        counts["included_" + field + "_count"] = included
        counts["omitted_" + field + "_count"] = max(0, counts[field + "_count"] - included)
    for field in ("critical", "active", "completed_today"):
        included = sum(bool(row[field]) for row in context["tasks"])
        counts["omitted_" + field + "_count"] = max(0, counts[field + "_count"] - included)


def _seal(context):
    # Ephemeral timestamps and the self-describing size envelope do not make a
    # source version stale. Actual source proofs and selected facts do.
    canonical = {key: value for key, value in context.items()
                 if key not in ("generated_at", "expires_at", "context_digest", "context_limits")}
    context["context_digest"] = _digest(canonical)
    limits = context["context_limits"]
    # Iterate to include the decimal digits of these counters themselves.
    for _ in range(4):
        encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        limits["characters"] = len(encoded)
        limits["utf8_bytes"] = len(encoded.encode("utf-8"))
        # One token per UTF-8 byte is a conservative content bound; monetary
        # admission separately accounts for the full rendered prompt envelope.
        limits["content_token_upper_bound"] = limits["utf8_bytes"]
    return len(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def _fit(context, maximum, protected):
    limits = context["context_limits"]
    for length in (1200, 400, 100, 0):
        limits["task_detail_character_limit"] = length
        for row in context["tasks"]:
            if "description" in row:
                row["description"] = row["description"][:length]
                row["definition_of_done"] = row["definition_of_done"][:length]
                row["omitted_description_characters"] = row["description_characters"] - len(row["description"])
                row["omitted_definition_of_done_characters"] = row["definition_of_done_characters"] - len(row["definition_of_done"])
        _refresh(context)
        if _seal(context) <= maximum:
            return context
    while True:
        removable = next((index for index in range(len(context["tasks"]) - 1, -1, -1)
                          if context["tasks"][index]["id"] not in protected), None)
        if removable is not None:
            context["tasks"].pop(removable)
        elif context["coordination"]:
            context["coordination"].pop()
        elif context["goals"]:
            context["goals"].pop()
        elif context.get("capture") and len(context["capture"]["content"]) > 256:
            capture = context["capture"]
            capture["content"] = capture["content"][:max(256, len(capture["content"]) // 2)]
            capture["omitted_content_characters"] = capture["content_characters"] - len(capture["content"])
        else:
            raise AIConfigurationError("The AI input budget cannot safely include the required source and current work facts.")
        _refresh(context)
        if _seal(context) <= maximum:
            return context


def build_workflow_context(*, user, feature, task_id=None, capture_id=None, team_id=None,
                           question="", max_characters=24000):
    """Read current scope and facts without creating plans or operator snapshots."""
    if not user or not user.is_authenticated:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perms(("tasker.view_task", "tasker.use_ai")):
        raise PermissionDenied
    feature = normalize_feature(feature)
    task_id, capture_id, team_id = map(_identifier, (task_id, capture_id, team_id))
    if type(max_characters) is not int or not 2048 <= max_characters <= 100000:
        raise AIConfigurationError("Choose a bounded AI context budget from 2,048 to 100,000 characters.")
    if type(question) is not str or "\x00" in question or len(question) > (16000 if feature == "MEETING" else 2000):
        raise ValidationError("Supply bounded text for this assistance request.")
    if feature == "MEETING" and not question.strip():
        raise ValidationError("Paste the meeting text you explicitly want summarized.")
    if capture_id is not None and feature != "CAPTURE":
        raise ValidationError("A capture can only be supplied to capture assistance.")
    if task_id is not None and feature in ("CAPTURE", "MEETING"):
        raise ValidationError("Select task assistance before supplying a task.")
    if feature == "CAPTURE" and capture_id is None:
        raise ValidationError("Select your own capture before requesting assistance.")
    team = report_teams_for(actor).filter(pk=team_id).first() if team_id else None
    if team_id and team is None:
        raise PermissionDenied
    if team and (capture_id is not None or feature in ("CAPTURE", "PLAN", "BREAKDOWN", "MEETING")):
        raise PermissionDenied
    scope = "MANAGER" if team else "PERSONAL"
    now = timezone.now()
    policy = WorkPolicy.objects.filter(owner=actor).first()
    zone_name = policy.timezone_name if policy else "Africa/Harare"
    zone = ZoneInfo(zone_name)
    today = now.astimezone(zone).date()
    local_start = timezone.make_aware(datetime.combine(today, time.min), zone)
    local_end = timezone.make_aware(datetime.combine(today + timedelta(days=1), time.min), zone)
    readable = scoped_tasks_for(actor).filter(archived=False)
    readable = readable.filter(team=team, company_id=team.company_id) if team else readable.filter(owner=actor)
    source_task = readable.filter(pk=task_id).first() if task_id else None
    if task_id and source_task is None:
        raise PermissionDenied
    capture = CaptureItem.objects.filter(pk=capture_id, owner=actor,
        visibility=CaptureItem.Visibility.PRIVATE, triage_state=CaptureItem.TriageState.INBOX).first() if capture_id else None
    if capture_id and capture is None:
        raise PermissionDenied
    if feature == "BREAKDOWN" and source_task is None:
        raise ValidationError("Select a current task before requesting a breakdown.")
    active = Q(status__in=ACTIVE)
    completed_today = Q(status=Task.Status.COMPLETED, completed_at__gte=local_start, completed_at__lt=local_end)
    population = readable.filter(active | completed_today | Q(pk=task_id))
    if feature == "BREAKDOWN":
        population = readable.filter(pk=task_id)
    elif feature in ("CAPTURE", "MEETING"):
        population = readable.none()
    counts = {"task_count": population.count(), "active_count": population.filter(active).count(),
        "critical_count": population.filter(active, priority=Task.Priority.CRITICAL).count(),
        "completed_today_count": population.filter(completed_today).count()}
    operator = build_operator(user=actor, now=now, persist=False) if scope == "PERSONAL" and feature not in ("CAPTURE", "MEETING") else None
    operator_ids = []
    if operator:
        for item in ([operator["now"]] if operator["now"] else []) + operator["next"] + operator["waiting"] + operator["decisions"]:
            if item.get("task_id") and item["task_id"] not in operator_ids:
                operator_ids.append(item["task_id"])
    plan = DailyPlan.objects.filter(owner=actor, plan_date=today).first() if scope == "PERSONAL" else None
    planned = list(plan.planned_tasks.filter(pk__in=readable).values_list("pk", flat=True)) if plan else []
    priorities = list(plan.top_priorities.filter(pk__in=readable).values_list("pk", flat=True)) if plan else []
    selected = list(population.annotate(_source=Case(When(pk=task_id, then=0),
        When(pk__in=operator_ids[:1], then=1), When(pk__in=priorities, then=2),
        When(pk__in=operator_ids, then=3), default=4, output_field=IntegerField()),
        _priority=priority_rank_expression()).order_by("_source", "_priority", "due_date", "pk")
        .select_related("remaining_effort_estimate")[:TASK_LIMIT])
    readiness = readiness_for_tasks(tasks=selected, user=actor)
    running = {}
    if scope == "PERSONAL":
        for pk, task_pk, started in TaskTimeEntry.objects.filter(task_id__in=[row.pk for row in selected],
                ended_at__isnull=True).values_list("pk", "task_id", "started_at"):
            running.setdefault(task_pk, []).append((pk, started))
    goals, links, goal_total = _goals(actor, selected, team)
    records, proofs, dependencies, efforts = [], {}, {}, {}
    for task in selected:
        ready = readiness[task.pk]
        record = {"id": task.pk, "title": task.title, "owner_id": task.owner_id,
            "company_id": task.company_id, "team_id": task.team_id,
            "status": task.status, "priority": task.priority, "progress_percent": task.progress,
            "due_date": str(task.due_date) if task.due_date else None,
            "due_time": task.due_time.isoformat(timespec="minutes") if task.due_time else None,
            "active": task.status in ACTIVE, "critical": task.status in ACTIVE and task.priority == Task.Priority.CRITICAL,
            "completed_today": task.status == Task.Status.COMPLETED and bool(task.completed_at and local_start <= task.completed_at < local_end),
            "goal_id": links.get(task.pk),
            "shared_blocker": task.blocked_reason[:400] if task.status == Task.Status.BLOCKED else "",
            "omitted_blocker_characters": max(0, len(task.blocked_reason) - 400) if task.status == Task.Status.BLOCKED else 0,
            "readiness": {"ready": ready.ready, "code": ready.code, "reason": ready.reason,
                          "prerequisite_count": ready.count}}
        if scope == "PERSONAL":
            elapsed = max(0, int(task.actual_duration.total_seconds())) if task.actual_duration else 0
            elapsed += sum(max(0, int((now - started).total_seconds())) for _, started in running.get(task.pk, []))
            effort = effort_for_task(task=task, now=now, logged=(elapsed, running.get(task.pk, [])))
            record.update(description=task.description[:1200], definition_of_done=task.definition_of_done[:1200],
                description_characters=len(task.description), definition_of_done_characters=len(task.definition_of_done),
                remaining_minutes=effort["remaining_minutes"], effort_state=effort["state"],
                needs_reestimate=effort["needs_reestimate"])
            if feature == "BREAKDOWN":
                existing = TaskChecklistItem.objects.filter(task=task).order_by("position", "pk")
                checklist_total = existing.count()
                checklist = [{"id": row.pk, "title": row.title, "completed": row.is_completed} for row in existing[:20]]
                record["checklist"] = {"total": checklist_total, "items": checklist,
                                       "omitted": max(0, checklist_total - len(checklist))}
            proofs[str(task.pk)] = work_version(task)
            efforts[str(task.pk)] = effort["version"]
        else:
            # Never hash notes, descriptions, private estimates or updated_at:
            # even a changing opaque digest could expose private-only edits.
            proofs[str(task.pk)] = _digest(record)
        dependencies[str(task.pk)] = ready.version
        records.append(record)
    task_ids = {row["id"] for row in records}
    requests = visible_requests_for(actor).filter(task_id__in=population.values("pk"), status__in=("OPEN", "ACKNOWLEDGED"))
    request_total = requests.count()
    requests = list(requests.filter(task_id__in=task_ids).order_by("expected_response_at", "pk")[:COORDINATION_LIMIT])
    context = {"schema_version": "3", "feature": feature, "actor_id": actor.pk, "scope": scope,
        "date": str(today), "timezone": zone_name, "generated_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=5)).isoformat(),
        "source": {"task_id": task_id, "capture_id": capture_id, "team_id": team_id},
        "allowed_actions": sorted(FEATURE_ACTIONS[feature]) if scope == "PERSONAL" else [],
        "source_text_is_untrusted": True,
        "question": question if feature != "MEETING" else "", "meeting_text": question if feature == "MEETING" else "",
        "tasks": records, "goals": goals, "capture": None,
        "people": [{"id": row.pk, "name": (row.get_full_name() or row.get_username())[:150]}
            for row in get_user_model().objects.filter(pk__in={task.owner_id for task in selected}).order_by("pk")]
            if scope == "MANAGER" else [],
        "counts": {**counts, "goal_count": goal_total, "coordination_count": request_total,
                   "planned_count": len(planned), "priority_count": len(priorities),
                   "population": "Current readable work in the selected scope; unreadable work is excluded."},
        "references": {"now_task_id": operator["now"]["task_id"] if operator and operator["now"] else None,
            "next_task_ids": [row["task_id"] for row in operator["next"]] if operator else [],
            "waiting_task_ids": [row["task_id"] for row in operator["waiting"]] if operator else [],
            "decision_task_ids": [row["task_id"] for row in operator["decisions"] if row.get("task_id")] if operator else [],
            "planned_task_ids": planned, "priority_task_ids": priorities,
            "completed_task_ids": [row["id"] for row in records if row["completed_today"]]},
        "capacity": _capacity(operator["capacity"]) if operator else {"known": False, "reason": "Private capacity details are excluded from manager assistance."},
        "operator": {"source_digest": operator["source_digest"], "counts": operator["counts"],
            "exclusions": operator["exclusions"], "stale": operator["freshness"]["stale"],
            "focus_active": operator["focus_active"]} if operator else None,
        "coordination": [{"id": row.pk, "task_id": row.task_id, "kind": row.kind, "status": row.status,
            "reason": row.reason[:400], "requested_action": row.requested_action[:400],
            "expected_response_at": row.expected_response_at.isoformat()} for row in requests],
        "proofs": {"tasks": proofs, "dependencies": dependencies, "efforts": efforts,
            "population": _digest({**counts, "goal_count": goal_total, "coordination_count": request_total}),
            "task_children": task_children_proofs(selected) if scope == "PERSONAL" else {},
            "captures": {str(capture.pk): capture.version} if capture else {},
            "plans": {str(today): plan_version(plan)} if scope == "PERSONAL" else {},
            "capacity": operator["capacity"]["version"] if operator else None,
            "operator": operator["source_digest"] if operator else None,
            "goals": {}, "coordination": {str(row.pk): str(row.revision) for row in requests}},
        "context_limits": {"max_characters": max_characters, "task_limit": TASK_LIMIT,
                           "characters": 0, "utf8_bytes": 0, "content_token_upper_bound": 0}}
    if capture:
        context["capture"] = {"id": capture.pk, "kind": capture.kind, "title": capture.title,
            "content": capture.content[:8000], "tags": capture.tags, "url": capture.url,
            "content_characters": len(capture.content), "omitted_content_characters": max(0, len(capture.content) - 8000)}
    protected = {task_id} if task_id else set()
    if operator and operator["now"]:
        protected.add(operator["now"]["task_id"])
    return _fit(context, max_characters, protected)
