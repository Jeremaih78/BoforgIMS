"""Bounded shared strategy facts with explicit unknowns and no private content."""
from datetime import date, datetime, timedelta
from math import ceil
import uuid
from zoneinfo import ZoneInfo

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Avg, Case, Count, Exists, F, IntegerField, Max, OuterRef, Q, When
from django.urls import reverse
from django.utils import timezone

from accounting.models import Company
from tasker.models import Task, TaskActivity, Team, TeamMembership
from tasker.organization import company_read_ids, managed_teams_for, visible_companies_for
from tasker.goal_models import Goal, KeyResult, TaskContribution
from tasker.services.coordination import scoped_tasks_for, visible_requests_for
from tasker.services.dependencies import readiness_for_tasks
from tasker.services.goals import visible_goals_for, key_result_progress
from tasker.services.workbench import work_version
from tasker.strategy_models import WorkDeclaration, WorkDeclarationEvent


LANE_LIMIT = 24
TASK_LIMIT = 100
GOAL_LIMIT = 12
EXCEPTION_LIMIT = 40
ACTIVE = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)
FORECAST_NOTICE = "Recorded shared work and declared capacity are facts, not a forecast or employee productivity score. Arrival and current/next work are explicit declarations; missing data is unknown."


class DeclarationConflict(ValidationError):
    pass


def _actor(user):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    actor = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def _identifier(value, *, optional=True):
    if value in (None, "") and optional:
        return None
    if type(value) not in (int, str) or len(str(value)) > 19 or not str(value).isdecimal() or not 0 < int(value) <= 9223372036854775807:
        raise ValidationError("Choose a valid current source.")
    return int(value)


def _day(value):
    if value is None:
        return timezone.now().astimezone(ZoneInfo("Africa/Harare")).date()
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except ValueError:
            raise ValidationError("Choose a valid calendar date.") from None
    if not isinstance(value, date) or isinstance(value, datetime) or not 1900 <= value.year <= 2200:
        raise ValidationError("Choose a valid calendar date.")
    return value


def strategy_teams_for(user):
    """Reading a company's work is distinct from being allowed to manage it."""
    if not user or not user.is_authenticated or not user.is_active or not user.has_perm("tasker.view_task"):
        return Team.objects.none()
    members = TeamMembership.objects.filter(user=user, is_active=True, user__is_active=True,
        team__company__is_active=True).values("team_id")
    return Team.objects.select_related("company").filter(company__is_active=True).filter(
        Q(pk__in=members) | Q(pk__in=managed_teams_for(user)) | Q(company_id__in=company_read_ids(user))).distinct()


def strategy_tasks_for(user):
    actor = _actor(user)
    return scoped_tasks_for(actor).filter(Q(team__in=strategy_teams_for(actor)) | Q(team__isnull=True))


@transaction.atomic
def declare_work(*, user, team_id, current_task_id=None, next_task_id=None, expected_revision="none"):
    """Only the authenticated employee can confirm their own current/next work."""
    if not getattr(settings, "TASKER_STRATEGY_ENABLED", False):
        raise ValidationError("Work declarations are not enabled for this rollout.")
    actor = _actor(user)
    team_id = _identifier(team_id, optional=False)
    current_task_id, next_task_id = _identifier(current_task_id), _identifier(next_task_id)
    if current_task_id is not None and current_task_id == next_task_id:
        raise ValidationError("Current and next work must be different tasks.")
    # Match AI proposal's actor -> Task -> Company -> Team lock order. NO_KEY
    # serializes first confirmations without conflicting with FK key-share locks.
    get_user_model().objects.select_for_update(no_key=True).get(pk=actor.pk)
    task_ids = sorted({pk for pk in (current_task_id, next_task_id) if pk is not None})
    tasks = {row.pk: row for row in Task.all_objects.select_for_update().filter(pk__in=task_ids).order_by("pk")}
    team = Team.objects.filter(pk=team_id, company__is_active=True).first()
    if team is None:
        raise PermissionDenied
    Company.objects.select_for_update().get(pk=team.company_id, is_active=True)
    Team.objects.select_for_update().get(pk=team.pk)
    actor = _actor(actor)
    if not TeamMembership.objects.filter(team=team, user=actor, is_active=True).exists():
        raise PermissionDenied
    readable = set(scoped_tasks_for(actor).filter(pk__in=task_ids, owner=actor, team=team,
        company_id=team.company_id, archived=False, status__in=ACTIVE).values_list("pk", flat=True))
    if readable != set(task_ids):
        raise PermissionDenied
    item = WorkDeclaration.objects.select_for_update().filter(user=actor, team=team).first()
    previous = str(item.revision) if item else "none"
    if str(expected_revision) != previous:
        raise DeclarationConflict("Your confirmation changed. Reload and review your current and next work.")
    item = item or WorkDeclaration(user=actor, team=team)
    item.current_task_id, item.next_task_id = current_task_id, next_task_id
    item.current_task_version = work_version(tasks[current_task_id]) if current_task_id else ""
    item.next_task_version = work_version(tasks[next_task_id]) if next_task_id else ""
    item.revision, item.confirmed_at = uuid.uuid4(), timezone.now()
    item.save()
    WorkDeclarationEvent.objects.create(declaration=item, actor=actor,
        current_task_id=current_task_id, next_task_id=next_task_id, revision=item.revision,
        created_at=item.confirmed_at)
    return item


def _capacity(actor, subject, day):
    from tasker.services.capacity import capacity_for_day
    unknown = {"known": False, "committed_minutes": None, "available_minutes": None,
        "usable_minutes": None, "overload_minutes": None, "unknown_effort_count": None,
        "timezone_name": None,
        "reason": "Capacity is unknown or is not shared with your role."}
    try:
        facts = capacity_for_day(user=subject, day=day, actor=actor)
    except (PermissionDenied, ValidationError):
        return unknown
    keys = ("known", "committed_minutes", "available_minutes", "usable_minutes", "overload_minutes", "unknown_effort_count")
    return {**{key: facts[key] for key in keys}, "timezone_name": facts["timezone_name"],
        "reason": "Aggregate commitments can include work outside this board; private titles and exclusion notes are not shared." if facts["known"] else "Work hours and interruption buffer are not configured; capacity is unknown."}


def build_strategy_board(*, user, team_id=None, company_id=None, day=None, view="execution", page=1):
    actor = _actor(user)
    team_id, company_id, day = _identifier(team_id), _identifier(company_id), _day(day)
    if view not in ("execution", "dependencies", "capacity"):
        raise ValidationError("Choose execution, dependencies or capacity.")
    if type(page) not in (str, int) or not str(page).isdecimal() or int(page) < 1:
        raise ValidationError("Choose a valid board page.")
    now = timezone.now()
    teams_qs = strategy_teams_for(actor).order_by("company__name", "name", "pk")
    if team_id and not teams_qs.filter(pk=team_id).exists():
        raise PermissionDenied
    if company_id and not visible_companies_for(actor).filter(pk=company_id).exists():
        raise PermissionDenied
    if team_id:
        teams_qs = teams_qs.filter(pk=team_id)
    if company_id:
        teams_qs = teams_qs.filter(company_id=company_id)
    if team_id and not teams_qs.exists():
        raise PermissionDenied
    selected_team_ids = teams_qs.values("pk")
    # Team filters exclude unrelated personal and company-only work explicitly.
    tasks_qs = strategy_tasks_for(actor).filter(archived=False)
    tasks_qs = tasks_qs.filter(team_id=team_id) if team_id else tasks_qs.filter(Q(team_id__in=selected_team_ids) | Q(team__isnull=True))
    if company_id:
        tasks_qs = tasks_qs.filter(company_id=company_id)
    wide_teams = Team.objects.filter(Q(pk__in=managed_teams_for(actor)) | Q(company_id__in=company_read_ids(actor)))
    memberships = TeamMembership.objects.filter(team_id__in=selected_team_ids, is_active=True, user__is_active=True).filter(
        Q(user=actor) | Q(team_id__in=wide_teams)).select_related("user", "team", "team__company").order_by("team__company__name", "team__name", "user__username", "pk")
    total_lanes = memberships.count()
    pages = max(1, ceil(total_lanes / LANE_LIMIT))
    page = min(int(page), pages)
    member_rows = list(memberships[(page-1)*LANE_LIMIT:page*LANE_LIMIT])
    pairs = {(member.user_id, member.team_id) for member in member_rows}
    member_ids = {member.user_id for member in member_rows}
    board_team_ids = {member.team_id for member in member_rows}
    declarations = {(row.user_id, row.team_id): row for row in WorkDeclaration.objects.filter(
        user_id__in=member_ids, team_id__in=board_team_ids).select_related("current_task", "next_task")}
    declared_ids = {pk for row in declarations.values() for pk in (row.current_task_id, row.next_task_id) if pk}
    lane_condition = Q(pk__in=[])
    for owner_id, selected_team in sorted(pairs):
        lane_condition |= Q(owner_id=owner_id, team_id=selected_team)
    current_member = TeamMembership.objects.filter(user_id=OuterRef("owner_id"), team_id=OuterRef("team_id"),
        is_active=True, user__is_active=True)
    # Owner is non-null in this schema. Legacy/inactive/unqualified ownership is
    # an intervention to review, never an invented 'unassigned employee'.
    unallocated = tasks_qs.annotate(_qualified=Exists(current_member)).filter(Q(team__isnull=True) | Q(_qualified=False))
    chosen_qs = tasks_qs.filter(lane_condition | Q(pk__in=unallocated.values("pk")))
    chosen = list(chosen_qs.annotate(_declared=Case(When(pk__in=declared_ids, then=0), default=1, output_field=IntegerField()),
        _priority=Case(When(priority="CRITICAL", then=0), When(priority="HIGH", then=1), When(priority="MEDIUM", then=2), default=3, output_field=IntegerField()))
        .order_by("_declared", "_priority", "due_date", "pk")[:TASK_LIMIT])
    readiness = readiness_for_tasks(tasks=chosen, user=actor) if chosen else {}
    chosen_ids = {row.pk for row in chosen}
    contributions = {row.task_id: row.initiative.goal_id for row in TaskContribution.objects.filter(
        task_id__in=chosen_ids, initiative__is_deleted=False, initiative__goal__in=visible_goals_for(actor)).select_related("initiative")}
    request_rows = list(visible_requests_for(actor).filter(task_id__in=chosen_ids,
        status__in=("OPEN", "ACKNOWLEDGED")).select_related("resolver").order_by("raised_at", "pk")[:240])
    by_task = {}
    for request in request_rows:
        by_task.setdefault(request.task_id, request)
    blocked_since_by_task = {row["task_id"]: row["since"] for row in TaskActivity.objects.filter(
        task_id__in=chosen_ids, event_type=TaskActivity.EventType.STATUS,
        changes__status__to=Task.Status.BLOCKED).values("task_id").annotate(since=Max("created_at"))}
    canonical, dependencies = {}, []
    for task in chosen:
        ready = readiness[task.pk]
        request = by_task.get(task.pk)
        blocker = None
        if task.status == "BLOCKED" or request:
            blocked_since = request.raised_at if request else blocked_since_by_task.get(task.pk)
            blocker = {"reason": (task.blocked_reason or (request.reason if request else "Shared request needs a response"))[:500],
                "age_minutes": max(0, int((now-blocked_since).total_seconds()//60)) if blocked_since else None,
                "age_basis": "request_created" if request else "blocked_status_event" if blocked_since else "unknown",
                "resolver": (request.resolver.get_full_name() or request.resolver.get_username()) if request else None,
                "request_url": reverse("ims:tasker:coordination_detail", args=(request.pk,)) if request else ""}
        canonical[task.pk] = {"id": task.pk, "title": task.title, "url": task.get_absolute_url(),
            "owner_id": task.owner_id, "team_id": task.team_id, "priority": task.priority, "status": task.status,
            "progress": task.progress, "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_time": task.due_time.isoformat() if task.due_time else None,
            "readiness": {"ready": ready.ready, "code": ready.code, "reason": ready.reason},
            "goal_id": contributions.get(task.pk), "blocker": blocker, "last_work_update": task.updated_at.isoformat()}
        for edge in ready.prerequisites:
            dependencies.append({"task_id": task.pk, "prerequisite_id": edge.task_id if edge.accessible else None,
                "title": edge.title if edge.accessible else "Unavailable prerequisite", "url": edge.url if edge.accessible else "",
                "accessible": edge.accessible, "ready": edge.completed if edge.accessible else False})
    summaries = {(row["owner_id"], row["team_id"]): row for row in tasks_qs.values("owner_id", "team_id").annotate(
        total=Count("pk"), active=Count("pk", filter=Q(status__in=ACTIVE)), blocked=Count("pk", filter=Q(status="BLOCKED")),
        overdue=Count("pk", filter=Q(status__in=ACTIVE, due_date__lt=day)), critical=Count("pk", filter=Q(status__in=ACTIVE, priority="CRITICAL")),
        progress_percent=Avg("progress"))}
    # Arrival service retains current-membership AND recorded-team authority.
    from tasker.services.arrival import visible_arrivals_for
    arrivals = {}
    for selected_team in sorted(board_team_ids):
        try:
            rows = visible_arrivals_for(actor, team=selected_team, local_date=day).filter(user_id__in=member_ids)
        except PermissionDenied:
            rows = visible_arrivals_for(actor, local_date=day).filter(teams__pk=selected_team)
        for row in rows:
            arrivals[(row.user_id, selected_team)] = {"declared": True, "effective_at": row.effective_at.isoformat(), "revision": str(row.revision)}
    from tasker.services.reporting import report_teams_for, visible_submissions_for, visible_reports_for
    report_team_ids = set(report_teams_for(actor).filter(pk__in=board_team_ids).values_list("pk", flat=True))
    submissions = {}
    for row in visible_submissions_for(actor).filter(team_id__in=board_team_ids, owner_id__in=member_ids,
            report_date=day).only("id", "owner_id", "team_id", "version", "created_at").order_by("owner_id", "team_id", "-version", "-pk"):
        submissions.setdefault((row.owner_id, row.team_id), row)
    reports = {}
    for row in visible_reports_for(actor).filter(team_id__in=board_team_ids, report_date=day).only(
            "id", "team_id", "version", "created_at").order_by("team_id", "-version", "-pk"):
        reports.setdefault(row.team_id, row)
    from .business_guidance import board_business_work
    business = board_business_work(actor,pairs=pairs,company_id=company_id,team_id=team_id)
    exceptions, lanes, capacity_cache = [], [], {}
    for member in member_rows:
        key = (member.user_id, member.team_id)
        summary = summaries.get(key, {"total": 0, "active": 0, "blocked": 0, "overdue": 0, "critical": 0, "progress_percent": None}).copy()
        summary["progress_percent"] = round(summary["progress_percent"], 1) if summary["progress_percent"] is not None else None
        source_actions=business['lanes'].get(key,[])
        summary['source_count']=len(source_actions)
        summary['source_critical']=sum(item['priority']=='CRITICAL' for item in source_actions)
        lane_tasks = [row for row in canonical.values() if (row["owner_id"], row["team_id"]) == key]
        summary["omitted_tasks"] = max(0, summary["total"]-len(lane_tasks))
        declaration = declarations.get(key)
        current, following = None, None
        invalid_declaration = False
        if declaration:
            for field, proof in (("current_task", "current_task_version"), ("next_task", "next_task_version")):
                task = getattr(declaration, field)
                valid = bool(task and task.pk in canonical and task.owner_id == member.user_id and task.team_id == member.team_id
                    and task.status in ACTIVE and not task.archived and not task.is_deleted and getattr(declaration, proof) == work_version(task))
                if task and not valid:
                    invalid_declaration = True
                if valid and field == "current_task": current = canonical[task.pk]
                if valid and field == "next_task": following = canonical[task.pk]
        stale = not declaration or invalid_declaration or declaration.confirmed_at < now-timedelta(hours=4) or declaration.confirmed_at.astimezone(ZoneInfo("Africa/Harare")).date() != day
        confirmation = {"current": current, "next": following, "last_confirmed": declaration.confirmed_at.isoformat() if declaration else None,
            "stale": stale, "needs_update": invalid_declaration, "revision": str(declaration.revision) if declaration else "none"}
        if member.user_id not in capacity_cache:
            capacity_cache[member.user_id] = _capacity(actor, member.user, day)
        capacity = capacity_cache[member.user_id]
        submission, report = submissions.get(key), reports.get(member.team_id)
        report_known = member.user_id == actor.pk or member.team_id in report_team_ids
        end_day = {"known": report_known, "submitted": submission is not None if report_known else None, "submitted_at": submission.created_at.isoformat() if submission else None,
            "version": submission.version if submission else None,
            "url": reverse("ims:tasker:report_detail", args=(report.pk,)) if report else
                reverse("ims:tasker:daily_review")+"?date="+day.isoformat() if member.user_id == actor.pk else ""}
        lane = {"user_id": member.user_id, "name": member.user.get_full_name() or member.user.get_username(),
            "team_id": member.team_id, "team_name": member.team.name, "is_self": member.user_id == actor.pk,
            "declaration": confirmation, "tasks": lane_tasks, "business_actions": business["lanes"].get(key,[]), "summary": summary, "capacity": capacity,
            "arrival": arrivals.get(key, {"declared": False, "effective_at": None, "revision": None}), "end_day": end_day}
        lanes.append(lane)
        if summary['source_critical']:
            exceptions.append({'code':'critical_source_work','label':'Critical source work needs attention',
                'detail':f'{lane["name"]}: {summary["source_critical"]} critical operational actions in this displayed lane.',
                'url':reverse('ims:tasker:business_list'),'user_id':member.user_id,'team_id':member.team_id})
        url = reverse("ims:tasker:team_detail", args=(member.team_id,)) if actor.has_perm("tasker.view_team") else reverse("ims:tasker:task_list")
        if stale:
            exceptions.append({"code": "stale_confirmation", "label": "Work confirmation needs an update", "detail": f'{lane["name"]}: no current confirmation, changed work, or confirmation older than four hours.', "url": url, "user_id": member.user_id, "team_id": member.team_id})
        if not capacity["known"]:
            exceptions.append({"code": "capacity_unknown", "label": "Capacity is unknown", "detail": f'{lane["name"]}: confirm working hours and availability before committing more work.', "url": reverse("ims:tasker:work_policy") if lane["is_self"] else url, "user_id": member.user_id, "team_id": member.team_id})
        elif capacity["overload_minutes"]:
            exceptions.append({"code": "overload", "label": "Accepted commitments exceed capacity", "detail": f'{lane["name"]}: {capacity["overload_minutes"]} minutes beyond declared usable capacity.', "url": reverse("ims:tasker:planning_home") if lane["is_self"] else url, "user_id": member.user_id, "team_id": member.team_id})
        if capacity["known"] and summary["active"] and capacity["available_minutes"] == 0:
            exceptions.append({"code": "insufficient_capacity", "label": "No remaining declared capacity", "detail": f'{lane["name"]}: review commitments before adding more work.', "url": url, "user_id": member.user_id, "team_id": member.team_id})
    goals_qs = visible_goals_for(actor).exclude(status=Goal.Status.CLOSED)
    if company_id: goals_qs = goals_qs.filter(company_id=company_id)
    elif team_id: goals_qs = goals_qs.filter(company_id=teams_qs.first().company_id).filter(Q(team__isnull=True) | Q(team_id=team_id))
    goals = list(goals_qs.order_by("end_date", "pk")[:GOAL_LIMIT])
    goal_work = {row["initiative__goal_id"]: row["total"] for row in TaskContribution.objects.filter(
        task__in=tasks_qs, initiative__is_deleted=False, initiative__goal_id__in=[goal.pk for goal in goals]).values("initiative__goal_id").annotate(total=Count("pk"))}
    from tasker.business_outcome_models import BusinessContribution
    source_records={item['id']:item for values in business['lanes'].values() for item in values}
    source_records.update({item['id']:item for item in business['unallocated']})
    goal_source_work={}
    for mapping in BusinessContribution.objects.filter(link_id__in=source_records,
            key_result__is_deleted=False,key_result__goal_id__in=[goal.pk for goal in goals]).select_related('key_result__goal'):
        source=source_records.get(str(mapping.link_id))
        goal=mapping.key_result.goal
        if source and source['company_id']==goal.company_id and mapping.team_id==source['team_id'] and (
                goal.team_id is None or goal.team_id==source['team_id']):
            goal_source_work[goal.pk]=goal_source_work.get(goal.pk,0)+1
    goal_records = []
    for goal in goals:
        result = {"id": goal.pk, "title": goal.title, "url": goal.get_absolute_url(), "status": goal.status,
            "linked_task_count": goal_work.get(goal.pk, 0), "linked_business_count":goal_source_work.get(goal.pk,0), "known_progress": None}
        # Do not average unrelated measures or turn checklist progress into sales.
        key_results = list(KeyResult.objects.filter(goal=goal).select_related("goal")[:2])
        if len(key_results) == 1:
            progress = key_result_progress(key_results[0])
            result["known_progress"] = float(progress["progress"]) if progress["known"] else None
        goal_records.append(result)
        if not goal_work.get(goal.pk) and not goal_source_work.get(goal.pk):
            exceptions.append({"code": "orphan_goal", "label": "No linked work in this view", "detail": goal.title+": links outside this view may exist.", "url": goal.get_absolute_url()})
    orphan_ids = set(unallocated.filter(pk__in=chosen_ids).values_list("pk", flat=True))
    inactive_owner_tasks = {task.pk for task in chosen if not task.owner.is_active}
    unallocated_records = [value for pk, value in canonical.items() if pk in orphan_ids]
    for task in unallocated_records:
        if task["team_id"] is not None or task["id"] in inactive_owner_tasks:
            exceptions.append({"code": "owner_review", "label": "Owner needs review", "detail": task["title"]+": current owner is inactive or is not a current team member.", "url": task["url"], "task_id": task["id"]})
    for request in request_rows:
        if request.expected_response_at < now:
            exceptions.append({"code": "overdue_handoff", "label": "Shared response is overdue", "detail": canonical[request.task_id]["title"],
                "url": reverse("ims:tasker:coordination_detail", args=(request.pk,)), "task_id": request.task_id})
    total_tasks, total_goals = tasks_qs.count(), goals_qs.count()
    # Filter choices are bounded, but the actual lane/task population remains a
    # queryset. Omissions are explicit so a partial board cannot imply no work.
    teams = list(teams_qs[:100])
    companies = list(visible_companies_for(actor).order_by("name", "pk")[:100])
    return {"scope_label": "Current authorized shared work; personal/unmapped work appears only to its authorized owner.",
        "day": day.isoformat(), "view": view,
        "teams": [{"id": team.pk, "name": team.name, "company_id": team.company_id, "company_name": team.company.name,
            "url": reverse("ims:tasker:team_detail", args=(team.pk,))} for team in teams],
        "companies": [{"id": company.pk, "name": company.name} for company in companies],
        "goals": goal_records, "lanes": lanes, "unallocated_tasks": unallocated_records,
        "business":{key:business[key] for key in ('total','omitted','unallocated')},
        "dependencies": dependencies[:240], "exceptions": exceptions[:EXCEPTION_LIMIT],
        "counts": {"lane_total": total_lanes, "included_lanes": len(lanes), "omitted_lanes": max(0,total_lanes-len(lanes)),
            "task_total": total_tasks, "included_tasks": len(chosen), "omitted_tasks": max(0,total_tasks-len(chosen)),
            "goal_total": total_goals, "omitted_goals": max(0,total_goals-len(goals)), "exception_total": len(exceptions),
            "omitted_exceptions": max(0,len(exceptions)-EXCEPTION_LIMIT), "omitted_dependencies": max(0,len(dependencies)-240),
            "exception_scope": "Included lanes and tasks only"},
        "pagination": {"page": page, "pages": pages, "has_previous": page>1, "has_next": page<pages},
        "generated_at": now.isoformat(), "forecast_notice": FORECAST_NOTICE}
