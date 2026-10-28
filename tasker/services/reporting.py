"""Versioned factual team reporting through the shared job/delivery pipeline.

Only explicit End day submissions mark a member closed. Private reflections,
notes, capture text and AI conversations are never report inputs. Email uses a
local deterministic adapter; this module has no SMTP/provider activation path.
"""
from datetime import date, datetime, time, timedelta
import hashlib
import json
import uuid
from zoneinfo import ZoneInfo

from django.apps import apps
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Exists, F, Min, OuterRef, Prefetch, Q
from django.utils import timezone

from accounting.models import Company
from tasker.models import DailyPlan, DailyReview, OrganizationAccess, OrganizationEvent, Task, TaskChecklistItem, Team, TeamMembership
from tasker.organization import can_manage_company, managed_teams_for
from tasker.report_models import EndDaySubmission, ManagerReportSnapshot, ReportPolicy
from tasker.services.capacity import aware_local


MEMBER_LIMIT = 200
TASK_LIMIT = 500
OUTCOME_LIMIT = 500
ACTIVE = (Task.Status.PENDING, Task.Status.IN_PROGRESS, Task.Status.BLOCKED)


class ReportConflict(ValueError):
    pass


class ReportTransportError(Exception):
    """An explicit test-adapter failure, with conservative ambiguity semantics."""
    def __init__(self, *, uncertain=False):
        self.uncertain = uncertain
        super().__init__("Report test delivery did not complete.")


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def _actor(user, *, lock=False):
    if not user or not user.is_authenticated:
        raise PermissionDenied
    qs = get_user_model().objects
    if lock:
        qs = qs.select_for_update(no_key=True)
    actor = qs.filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def report_teams_for(user):
    qs = Team.objects.select_related("company").filter(company__is_active=True)
    if not user or not user.is_authenticated:
        return qs.none()
    user = get_user_model().objects.filter(pk=user.pk, is_active=True).first()
    if user is None or not user.has_perm("tasker.view_task"):
        return qs.none()
    if user.is_superuser:
        return qs
    grants = OrganizationAccess.objects.filter(user=user, is_active=True, can_receive_reports=True, company__is_active=True)
    return qs.filter(Q(pk__in=managed_teams_for(user)) | Q(company_id__in=grants.values("company_id"))).distinct()


def visible_reports_for(user):
    return ManagerReportSnapshot.objects.select_related("team", "team__company").filter(team__in=report_teams_for(user))


def visible_submissions_for(user):
    if not user or not user.is_authenticated or not user.is_active or not user.has_perm("tasker.view_task"):
        return EndDaySubmission.objects.none()
    return EndDaySubmission.objects.select_related("team", "owner").filter(
        Q(owner=user) | Q(team__in=report_teams_for(user)), team__is_deleted=False, team__company__is_active=True)


def _policy(team):
    return ReportPolicy.objects.filter(company_id=team.company_id).first()


def _policy_revision(team):
    policy = _policy(team)
    return str(policy.revision) if policy else "unconfigured-v1"


def _day(value, *, zone="Africa/Harare", allow_future=False):
    if isinstance(value, str):
        try:
            value = date.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValidationError("Choose a valid report date.") from None
    if not isinstance(value, date) or isinstance(value, datetime) or not 1900 <= value.year <= 2200:
        raise ValidationError("Choose a valid report date.")
    if not allow_future and value > timezone.now().astimezone(ZoneInfo(zone)).date():
        raise ValidationError("A future date cannot be reported as actual work.")
    return value


def _latest_submissions(team, day, owners):
    rows = EndDaySubmission.objects.filter(team=team, report_date=day, owner_id__in=owners).order_by("owner_id", "-version", "-pk")
    latest = {}
    for row in rows:
        latest.setdefault(row.owner_id, row)
    return latest


def _team_facts(team, day):
    """Batch only current team work and explicit submission metadata."""
    policy = _policy(team)
    zone_name = policy.timezone_name if policy else "Africa/Harare"
    day = _day(day, zone=zone_name)
    start, end = aware_local(day, time.min, zone_name), aware_local(day + timedelta(days=1), time.min, zone_name)
    membership_qs = TeamMembership.objects.filter(team=team, is_active=True, user__is_active=True).select_related("user").order_by("user_id")
    member_total = membership_qs.count()
    memberships = list(membership_qs[:MEMBER_LIMIT])
    owner_ids = [row.user_id for row in memberships]
    latest = _latest_submissions(team, day, owner_ids)
    plan_rows = list(DailyPlan.objects.filter(owner_id__in=owner_ids, plan_date=day).values_list("pk", "owner_id"))
    plan_owners = dict(plan_rows)
    intention_ids = set()
    intention_total = 0
    for field in ("planned_tasks", "top_priorities"):
        through = getattr(DailyPlan, field).through
        links = through.objects.filter(dailyplan_id__in=plan_owners, task__team=team,
            task__company_id=team.company_id, task__is_deleted=False, task__owner_id__in=owner_ids)
        intention_total += links.count()
        intention_ids.update(links.values_list("task_id", flat=True)[:TASK_LIMIT])
    tasks_qs = Task.objects.filter(team=team, company_id=team.company_id, owner_id__in=owner_ids).filter(
        Q(status__in=ACTIVE, archived=False) | Q(status=Task.Status.COMPLETED, completed_at__gte=start, completed_at__lt=end) | Q(pk__in=intention_ids))
    task_total = tasks_qs.count()
    tasks = list(tasks_qs.order_by("pk").prefetch_related(Prefetch("checklist_items",
        queryset=TaskChecklistItem.objects.filter(is_completed=False).order_by("position", "pk")[:1], to_attr="report_first_steps"))[:TASK_LIMIT])
    task_map = {task.pk: task for task in tasks}
    from tasker.planning_models import ScheduleBlock
    accepted_blocks_qs = ScheduleBlock.objects.filter(owner_id__in=owner_ids, task_id__in=task_map,
        task__owner_id__in=owner_ids, state="ACCEPTED", starts_at__lt=end, ends_at__gt=start).order_by("pk")
    block_total = accepted_blocks_qs.count()
    blocks = list(accepted_blocks_qs[:TASK_LIMIT])
    from tasker.arrival_models import ArrivalCorrection, ArrivalDeclaration
    arrivals = list(ArrivalDeclaration.objects.filter(user_id__in=owner_ids, teams=team,
        registered_at__gte=start, registered_at__lt=end).annotate(report_correction_count=Count("corrections", distinct=True)).prefetch_related(
        Prefetch("corrections", queryset=ArrivalCorrection.objects.order_by("-created_at", "-pk")[:1], to_attr="report_corrections")).order_by("user_id", "-registered_at"))
    arrival_map = {}
    for arrival in arrivals:
        arrival_map.setdefault(arrival.user_id, arrival)
    from tasker.goal_models import OutcomeMeasurement
    outcomes_qs = OutcomeMeasurement.objects.filter(team=team, company_id=team.company_id, employee_id__in=owner_ids,
        occurred_on=day, status="ACCEPTED", accepted_at__isnull=False,
        key_result__is_deleted=False, key_result__goal__is_deleted=False,
        key_result__goal__company_id=team.company_id).filter(Q(key_result__goal__team__isnull=True) | Q(key_result__goal__team=team)).filter(
        Q(task__isnull=True) | Q(task__team=team, task__company_id=team.company_id, task__owner_id=F("employee_id"), task__status=Task.Status.COMPLETED, task__is_deleted=False))
    outcome_total = outcomes_qs.count()
    outcomes = list(outcomes_qs.select_related("key_result", "task").order_by("pk")[:OUTCOME_LIMIT])
    try:
        request_model = apps.get_model("tasker", "CoordinationRequest")
    except LookupError:
        request_model = None
    requests = list(request_model.objects.filter(task_id__in=task_map, kind="BLOCKER", status__in=("OPEN", "ACKNOWLEDGED"),
        source_company_id=team.company_id, source_team_id=team.pk).select_related("resolver").order_by("raised_at", "pk")[:TASK_LIMIT]) if request_model else []
    now = timezone.now()
    members = []
    for membership in memberships:
        employee = membership.user
        owned = [task for task in tasks if task.owner_id == employee.pk]
        accepted = [task for task in owned if task.status == Task.Status.COMPLETED and task.progress == 100 and task.completed_at and start <= task.completed_at < end]
        active = [task for task in owned if not task.archived and task.status in ACTIVE]
        active.sort(key=lambda task: ({"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}[task.priority], task.due_date or date.max, task.pk))
        submission, arrival = latest.get(employee.pk), arrival_map.get(employee.pk)
        task_fact = lambda task: {"task_id": task.pk, "title": task.title, "status": task.status,
            "priority": task.priority, "due_date": task.due_date.isoformat() if task.due_date else None,
            "due_time": task.due_time.isoformat() if task.due_time else None}
        blockers = [{"task_id": task.pk, "title": task.title, "reason": task.blocked_reason,
            "resolver_id": None, "resolver_name": None, "requested_action": None, "raised_at": None, "age_minutes": None}
            for task in active if task.status == Task.Status.BLOCKED]
        structured_ids = set()
        for request in requests:
            if request.task_id not in {task.pk for task in owned} or request.source_owner_id != employee.pk:
                continue
            task = task_map[request.task_id]
            structured_ids.add(task.pk)
            blockers.append({"task_id": task.pk, "title": task.title, "request_id": request.pk,
                "reason": request.reason, "requested_action": request.requested_action,
                "resolver_id": request.resolver_id, "resolver_name": request.resolver.get_full_name() or request.resolver.username,
                "raised_at": request.raised_at.isoformat(), "acknowledged_at": request.acknowledged_at.isoformat() if request.acknowledged_at else None,
                "age_minutes": max(0, int((now - request.raised_at).total_seconds() // 60))})
        blockers = [item for item in blockers if item.get("request_id") or item["task_id"] not in structured_ids]
        member = {"employee_id": employee.pk, "name": employee.get_full_name() or employee.username, "role": membership.role,
            "submission": {"state": "CLOSED" if submission else "MISSING", "version": submission.version if submission else None,
                "submitted_at": submission.created_at.isoformat() if submission else None},
            "arrival": {"state": "DECLARED" if arrival else "NOT_DECLARED", "registered_at": arrival.registered_at.isoformat() if arrival else None,
                "effective_at": ((arrival.report_corrections[0].effective_at if arrival.report_corrections else arrival.registered_at).isoformat()) if arrival else None,
                "local_date": arrival.local_date.isoformat() if arrival else None, "timezone_name": arrival.timezone_name if arrival else None,
                "correction_count": arrival.report_correction_count if arrival else 0},
            "accepted_tasks": [{**task_fact(task), "completed_at": task.completed_at.isoformat()} for task in accepted],
            "outcomes": [{"outcome_id": row.pk, "task_id": row.task_id, "key_result_id": row.key_result_id,
                "measure": row.key_result.title, "value": str(row.value), "unit": row.key_result.unit,
                "accepted_at": row.accepted_at.isoformat()} for row in outcomes if row.employee_id == employee.pk],
            "commitments": [{**task_fact(task), "kind": "DAILY_INTENTION"} for task in active if task.pk in intention_ids],
            "blockers": blockers,
            "deadline_risks": [{**task_fact(task), "risk": "OVERDUE" if task.due_date < day else "DUE_TODAY"}
                for task in active if task.due_date and task.due_date <= day],
            "next_steps": [{**task_fact(task), "first_action": task.report_first_steps[0].title if task.report_first_steps else task.title,
                "guidance": "Candidate for employee review; recheck readiness and capacity before Start"}
                for task in active if task.status != Task.Status.BLOCKED][:3]}
        for block in blocks:
            task = task_map[block.task_id]
            if block.owner_id == employee.pk and task.owner_id == employee.pk:
                member["commitments"].append({**task_fact(task), "kind": "ACCEPTED_TIME_BLOCK", "block_id": block.pk,
                    "starts_at": block.starts_at.isoformat(), "ends_at": block.ends_at.isoformat()})
        members.append(member)
    return {"team_id": team.pk, "team_name": team.name, "company_id": team.company_id,
        "report_date": day.isoformat(), "timezone_name": zone_name, "members": members,
        "counts": {"members": member_total, "shown_members": len(members), "closed": sum(row["submission"]["state"] == "CLOSED" for row in members),
            "missing": sum(row["submission"]["state"] == "MISSING" for row in members), "tasks_reviewed": len(tasks)},
        "omitted": {"members": max(0, member_total-len(members)), "tasks": max(0, task_total-len(tasks)),
            "outcomes": max(0, outcome_total-len(outcomes)), "time_blocks": max(0, block_total-len(blocks)),
            "intention_links": max(0, intention_total-2*TASK_LIMIT)},
        "source_tasks": list({task.pk: {"task_id": task.pk, "owner_id": task.owner_id}
            for task in tasks + [row.task for row in outcomes if row.task_id]}.values())}


def _content_digest(facts, policy_revision):
    # Ages are displayed at generation, but ticking minutes are not a new fact.
    canonical = json.loads(json.dumps(facts))
    for member in canonical["members"]:
        for blocker in member["blockers"]:
            blocker.pop("age_minutes", None)
    canonical.pop("as_of", None)
    return _digest([policy_revision, canonical])


@transaction.atomic
def close_day(*, user, plan=None, review=None):
    actor = _actor(user, lock=True)
    if not actor.has_perm("tasker.view_dailyreview"):
        raise PermissionDenied
    if review is not None:
        review = DailyReview.objects.select_related("plan").filter(pk=review.pk, plan__owner=actor).first()
        plan = review.plan if review else None
    elif plan is not None:
        review = DailyReview.objects.filter(plan_id=plan.pk, plan__owner=actor).first()
        plan = review.plan if review else None
    if review is None or plan is None:
        raise ValidationError("Save your current End day review before submitting shared report facts.")
    from tasker.services.arrival import current_business_zone
    if plan.owner_id != actor.pk or plan.plan_date != timezone.now().astimezone(current_business_zone(actor)).date():
        raise PermissionDenied
    teams = list(Team.objects.filter(company__is_active=True, memberships__user=actor, memberships__is_active=True,
        memberships__is_deleted=False).distinct().order_by("pk"))
    submissions = []
    from tasker.services.jobs import enqueue_job
    for team in teams:
        all_facts = _team_facts(team, plan.plan_date)
        member = next((row for row in all_facts["members"] if row["employee_id"] == actor.pk), None)
        if member is None:
            raise ValidationError("This team exceeds the bounded review. Ask a manager to review its reporting roster.")
        member.pop("submission", None)
        digest = _content_digest({"members": [member]}, "employee-facts-v1")
        latest = EndDaySubmission.objects.filter(owner=actor, team=team, report_date=plan.plan_date).order_by("-version").first()
        if latest and latest.source_digest == digest:
            submission = latest
        else:
            submission = EndDaySubmission.objects.create(owner=actor, team=team, report_date=plan.plan_date,
                version=(latest.version+1) if latest else 1, source_digest=digest, facts=member, review=review)
        submissions.append(submission)
        enqueue_job(kind="reports.build", dedupe_key=f"report-submission:{submission.pk}",
            payload={"team_id": team.pk, "report_date": plan.plan_date.isoformat()}, owner=actor)
    return tuple(submissions)


def eligible_report_recipients(team):
    grants = OrganizationAccess.objects.filter(company_id=team.company_id, company__is_active=True,
        can_receive_reports=True, is_active=True, user__is_active=True).select_related("user").order_by("user_id")
    return [row.user for row in grants[:MEMBER_LIMIT] if row.user.has_perm("tasker.view_task")]


def _assert_sources_current(report):
    members = report.facts.get("members", [])
    recorded = {row["employee_id"] for row in members}
    current = set(TeamMembership.objects.filter(team=report.team, user_id__in=recorded,
        is_active=True, user__is_active=True).values_list("user_id", flat=True))
    if recorded != current:
        raise ReportConflict("The report's membership sources changed. Generate a current factual revision.")
    refs = report.facts.get("source_tasks", [])
    current_tasks = dict(Task.objects.filter(pk__in=[row["task_id"] for row in refs], team=report.team,
        company_id=report.team.company_id).values_list("pk", "owner_id"))
    if any(current_tasks.get(row["task_id"]) != row["owner_id"] for row in refs):
        raise ReportConflict("A report task changed scope or availability. Generate a current factual revision.")
    outcome_refs = {row["outcome_id"]: member["employee_id"] for member in members for row in member.get("outcomes", [])}
    if outcome_refs:
        from tasker.goal_models import OutcomeMeasurement
        current_outcomes = dict(OutcomeMeasurement.objects.filter(pk__in=outcome_refs,
            team=report.team, company_id=report.team.company_id, status="ACCEPTED", employee_id__in=recorded,
            key_result__is_deleted=False, key_result__goal__is_deleted=False,
            key_result__goal__company_id=report.team.company_id).values_list("pk", "employee_id"))
        if current_outcomes != outcome_refs:
            raise ReportConflict("Accepted-outcome sources changed. Generate a current factual revision.")


def get_report(*, user, pk):
    actor = _actor(user)
    report = visible_reports_for(actor).filter(pk=pk).first()
    if report is None:
        raise PermissionDenied
    _assert_sources_current(report)
    return report


def snapshot_can_view(user, snapshot):
    try:
        get_report(user=user, pk=snapshot.pk)
        return True
    except (PermissionDenied, ReportConflict):
        return False


@transaction.atomic
def _build_snapshot(*, team_id, report_date, generated_by=None):
    company_id = Team.objects.filter(pk=team_id).values_list("company_id", flat=True).first()
    if not Company.objects.select_for_update().filter(pk=company_id, is_active=True).exists():
        raise ReportConflict("This team's reporting scope is no longer active.")
    team = Team.objects.select_for_update(of=("self",)).select_related("company").filter(pk=team_id, company__is_active=True).first()
    if team is None:
        raise ReportConflict("This team's reporting scope is no longer active.")
    if generated_by is not None and not report_teams_for(generated_by).filter(pk=team.pk).exists():
        raise PermissionDenied
    facts = _team_facts(team, report_date)
    policy_revision = _policy_revision(team)
    digest = _content_digest(facts, policy_revision)
    latest = ManagerReportSnapshot.objects.filter(team=team, report_date=report_date).order_by("-version").first()
    if latest and latest.source_digest == digest:
        report = latest
    else:
        report = ManagerReportSnapshot.objects.create(team=team, report_date=report_date,
            version=latest.version+1 if latest else 1, source_digest=digest,
            facts={**facts, "as_of": timezone.now().isoformat()}, policy_revision=policy_revision,
            generated_by=generated_by)
    from tasker.services.notifications import enqueue_delivery
    for recipient in eligible_report_recipients(team):
        enqueue_delivery(recipient=recipient, channel="EMAIL", dedupe_key=f"team-report:{report.pk}:recipient:{recipient.pk}", report_snapshot=report)
    return report


def build_team_report(*, user, team, report_date):
    actor = _actor(user)
    team_id = getattr(team, "pk", team)
    selected = report_teams_for(actor).filter(pk=team_id).first()
    if selected is None:
        raise PermissionDenied
    day = _day(report_date, zone=_policy(selected).timezone_name if _policy(selected) else "Africa/Harare")
    return _build_snapshot(team_id=selected.pk, report_date=day, generated_by=actor)


def handle_build_report(payload, job=None):
    from tasker.services.jobs import JobCancelled
    team_id = payload.get("team_id")
    if type(team_id) is not int or team_id < 1:
        return JobCancelled("report_scope_invalid")
    try:
        _build_snapshot(team_id=team_id, report_date=_day(payload.get("report_date"), allow_future=True))
    except (ReportConflict, ValidationError):
        return JobCancelled("report_scope_changed")


@transaction.atomic
def save_report_policy(*, user, company, expected_revision="unconfigured-v1", timezone_name="Africa/Harare", cutoff_time=None,
        consolidation="UNCONFIGURED", correction_policy="UNCONFIGURED", missing_policy="UNCONFIGURED", weekdays=None,
        email_mode="UNCONFIGURED", retention_days=None):
    actor = _actor(user, lock=True)
    company_id = getattr(company, "pk", company)
    company = Company.objects.select_for_update().filter(pk=company_id, is_active=True).first()
    if company is None or not can_manage_company(actor, company):
        raise PermissionDenied
    policy = ReportPolicy.objects.select_for_update().filter(company=company).first()
    if str(expected_revision) != (str(policy.revision) if policy else "unconfigured-v1"):
        raise ReportConflict("The reporting policy changed. Keep your draft and review the saved policy before retrying.")
    policy = policy or ReportPolicy(company=company)
    for key, value in {"timezone_name": timezone_name, "cutoff_time": cutoff_time, "consolidation": consolidation,
            "correction_policy": correction_policy, "missing_policy": missing_policy, "weekdays": weekdays if weekdays is not None else [],
            "email_mode": email_mode, "retention_days": retention_days}.items():
        setattr(policy, key, value)
    policy.configured = bool(cutoff_time is not None and policy.weekdays and consolidation != "UNCONFIGURED" and
        correction_policy == "APPEND_REVISION" and missing_policy == "SHOW_MISSING" and email_mode == "TEST")
    policy.revision, policy.updated_by = uuid.uuid4(), actor
    policy.save()
    OrganizationEvent.objects.create(company=company, actor=actor, action="report.policy_saved", object_type="ReportPolicy",
        object_id=str(policy.pk), details={"revision": str(policy.revision), "configured": policy.configured, "email_mode": policy.email_mode})
    return policy


class MockReportEmailAdapter:
    """A deterministic test receipt; never reads an email address or mail backend."""
    def send(self, *, recipient, subject, body, idempotency_key):
        return {"state": "TEST_ACCEPTED", "reference": "test:" + _digest(idempotency_key)[:24]}


def render_report_email(report):
    facts = report.facts
    lines = [f"{facts['team_name']} — {report.report_date.isoformat()} — revision {report.version}",
        f"As of {facts['as_of']} ({facts['timezone_name']})", "Arrival is self-reported. No worked-hour or productivity percentage is inferred."]
    for member in facts["members"]:
        lines.append(f"{member['name']}: {'End day submitted' if member['submission']['state']=='CLOSED' else 'No report submission'}")
        lines.append(f"  Accepted tasks: {len(member['accepted_tasks'])}; accepted outcomes: {len(member['outcomes'])}; commitments: {len(member['commitments'])}; blockers: {len(member['blockers'])}.")
        for task in member["accepted_tasks"]:
            lines.append(f"  Completed: {task['title']}")
        for blocker in member["blockers"]:
            lines.append(f"  Waiting: {blocker['title']} — {blocker['requested_action'] or blocker['reason'] or 'Review blocker'}")
        for task in member["next_steps"]:
            lines.append(f"  Review next: {task['first_action']}")
    if any(facts["omitted"].values()):
        lines.append("This is a bounded report. Open the report to review disclosed omission counts.")
    return {"subject": f"Tasker team report — {report.report_date.isoformat()} — revision {report.version}", "body": "\n".join(lines)}


@transaction.atomic
def handle_report_delivery(payload, job=None, adapter=None):
    from tasker.notification_models import NotificationDelivery
    from tasker.services.jobs import JobCancelled, JobDeferred, JobRetry, assert_current_job_lease
    delivery_id = payload.get("delivery_id")
    if type(delivery_id) is not int or delivery_id < 1:
        return JobCancelled("report_delivery_invalid")
    reference = NotificationDelivery.objects.filter(pk=delivery_id, channel="EMAIL").values(
        "recipient_id", "report_snapshot__team_id", "report_snapshot__team__company_id").first()
    if reference is None:
        return JobCancelled("report_delivery_missing")
    # Recipient changes, preferences and focus starts use this same first lock.
    # Scope then precedes delivery rows, matching task/coordinator cancellation.
    # Never acquire a Task after this ledger lock.
    list(get_user_model().objects.select_for_update(no_key=True).filter(pk=reference["recipient_id"]))
    list(Company.objects.select_for_update().filter(pk=reference["report_snapshot__team__company_id"]))
    list(Team.all_objects.select_for_update().filter(pk=reference["report_snapshot__team_id"]))
    delivery = NotificationDelivery.objects.select_for_update(of=("self",)).select_related("recipient", "report_snapshot__team__company").filter(pk=delivery_id, channel="EMAIL").first()
    if delivery is None or delivery.report_snapshot is None:
        return JobCancelled("report_delivery_missing")
    if delivery.state in {"TEST_ACCEPTED", "PROVIDER_ACCEPTED", "DELIVERED", "CANCELLED", "UNKNOWN"}:
        return None
    report, actor = delivery.report_snapshot, delivery.recipient
    def settle(state, code="", run_after=None):
        delivery.state, delivery.error_code, delivery.next_attempt_at = state, code, run_after
        delivery.save(update_fields=["state", "error_code", "next_attempt_at"])
    if not actor.is_active or not actor.has_perm("tasker.view_task") or not report.team.company.is_active or report.team.is_deleted or not OrganizationAccess.objects.filter(
            user=actor, company_id=report.team.company_id, is_active=True, can_receive_reports=True).exists():
        settle("CANCELLED", "recipient_access_changed")
        return JobCancelled("recipient_access_changed")
    latest = ManagerReportSnapshot.objects.filter(team=report.team, report_date=report.report_date).order_by("-version").first()
    if latest is None or latest.pk != report.pk:
        settle("CANCELLED", "report_superseded")
        return JobCancelled("report_superseded")
    try:
        _assert_sources_current(report)
        current = _team_facts(report.team, report.report_date)
        if _content_digest(current, _policy_revision(report.team)) != report.source_digest:
            raise ReportConflict("Report facts changed")
    except (ReportConflict, ValidationError):
        settle("CANCELLED", "report_sources_changed")
        from tasker.services.jobs import enqueue_job
        enqueue_job(kind="reports.build", dedupe_key=f"report-repair:{report.pk}:{_digest(current) if 'current' in locals() else 'scope'}",
            payload={"team_id": report.team_id, "report_date": report.report_date.isoformat()})
        return JobCancelled("report_sources_changed")
    policy = _policy(report.team)
    if not policy or not policy.configured or policy.email_mode != "TEST" or policy.email_enabled or getattr(settings, "TASKER_REPORT_EMAIL_LIVE_ENABLED", False):
        settle("HELD", "report_policy_unconfigured" if not policy or not policy.configured else "live_email_disabled")
        return JobCancelled("report_policy_unconfigured")
    now = timezone.now()
    local = now.astimezone(ZoneInfo(policy.timezone_name))
    if report.report_date.weekday() not in policy.weekdays:
        settle("HELD", "outside_reporting_days")
        return JobCancelled("outside_reporting_days")
    if policy.consolidation == "AT_CUTOFF" and local.date() == report.report_date and local.time().replace(tzinfo=None) < policy.cutoff_time:
        due = aware_local(report.report_date, policy.cutoff_time, policy.timezone_name)
        settle("HELD", "awaiting_report_cutoff", due)
        return JobDeferred(run_after=due, reason="awaiting_report_cutoff")
    from tasker.services.notifications import delivery_window
    instant, reason = delivery_window(recipient=actor, now=now)
    if instant > now:
        settle("HELD", reason, instant)
        return JobDeferred(run_after=instant, reason=reason)
    delivery.attempt_count += 1
    delivery.attempted_at = now
    delivery.save(update_fields=["attempt_count", "attempted_at"])
    if job is not None:
        assert_current_job_lease()
    message = render_report_email(report)
    try:
        receipt = (adapter or MockReportEmailAdapter()).send(recipient=actor, **message, idempotency_key=delivery.dedupe_key)
    except ReportTransportError as error:
        if error.uncertain:
            settle("UNKNOWN", "report_delivery_uncertain")
            return JobCancelled("report_delivery_uncertain")
        retry_at = now+timedelta(minutes=1)
        settle("FAILED", "report_test_adapter_failed", retry_at)
        return JobRetry("report_test_adapter_failed", run_after=retry_at)
    except Exception:
        settle("UNKNOWN", "report_delivery_uncertain")
        return JobCancelled("report_delivery_uncertain")
    if not isinstance(receipt, dict) or receipt.get("state") != "TEST_ACCEPTED":
        settle("UNKNOWN", "unexpected_test_receipt")
        return JobCancelled("unexpected_test_receipt")
    delivery.state, delivery.error_code, delivery.accepted_at, delivery.next_attempt_at = "TEST_ACCEPTED", "", now, None
    delivery.save(update_fields=["state", "error_code", "accepted_at", "next_attempt_at"])


def report_status_summary(report):
    from tasker.notification_models import NotificationDelivery
    states = dict(NotificationDelivery.objects.filter(report_snapshot=report).values("state").annotate(total=Count("pk")).values_list("state", "total"))
    return {"state": "GENERATED", "version": report.version, "delivery_states": states,
        "missing_submissions": report.facts["counts"]["missing"], "omitted": report.facts["omitted"],
        "live_email_enabled": False}


def reconcile_reports(*, now=None, limit=100):
    """Repair explicit submissions and configured cutoff snapshots; never infer a close."""
    from tasker.services.jobs import enqueue_job
    from tasker.job_models import OutboxJob
    from tasker.notification_models import NotificationDelivery
    now = now or timezone.now()
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValidationError("Choose a report sweep limit from 1 to 1,000.")
    # Group revisions and owners into one team/date check. Unchecked explicit
    # closes go first, with never-built dates winning a tied check timestamp.
    snapshots = ManagerReportSnapshot.objects.filter(team_id=OuterRef("team_id"), report_date=OuterRef("report_date"))
    submissions = EndDaySubmission.objects.filter(team__is_deleted=False, team__company__is_active=True).values(
        "team_id", "report_date").annotate(_last_checked=Min("last_checked_at"), _has_report=Exists(snapshots)).order_by(
        F("_last_checked").asc(nulls_first=True), "_has_report", "report_date", "team_id")
    pairs = [(row["team_id"], row["report_date"]) for row in submissions[:limit]]
    for team_id, day in pairs:
        EndDaySubmission.objects.filter(team_id=team_id, report_date=day).update(last_checked_at=now)

    policies = list(ReportPolicy.objects.filter(configured=True, email_mode="TEST", consolidation="AT_CUTOFF",
        company__is_active=True).order_by(F("last_checked_at").asc(nulls_first=True), "pk")[:limit])
    due_policies = []
    for policy in policies:
        ReportPolicy.objects.filter(pk=policy.pk).update(last_checked_at=now)
        local = now.astimezone(ZoneInfo(policy.timezone_name))
        if local.weekday() in policy.weekdays and local.time().replace(tzinfo=None) >= policy.cutoff_time:
            due_policies.append((policy, local.date()))
    remaining = limit
    for index, (policy, day) in enumerate(due_policies):
        budget = max(1, remaining // (len(due_policies)-index))
        teams = Team.objects.filter(company_id=policy.company_id).order_by("pk")
        team_ids = list(teams.filter(pk__gt=policy.reconcile_team_cursor).values_list("pk", flat=True)[:budget])
        if len(team_ids) < budget:
            team_ids.extend(teams.filter(pk__lte=policy.reconcile_team_cursor).values_list("pk", flat=True)[:budget-len(team_ids)])
        if team_ids:
            ReportPolicy.objects.filter(pk=policy.pk).update(reconcile_team_cursor=team_ids[-1])
        pairs.extend((team_id, day) for team_id in team_ids)
        remaining -= len(team_ids)

    requested = 0
    for team_id, day in dict.fromkeys(pairs):
        team = Team.objects.filter(pk=team_id, company__is_active=True).first()
        if team is None:
            continue
        try:
            facts = _team_facts(team, day)
        except ValidationError:
            continue
        digest = _content_digest(facts, _policy_revision(team))
        latest = ManagerReportSnapshot.objects.filter(team=team, report_date=day).order_by("-version").first()
        if latest is None or latest.source_digest != digest:
            enqueue_job(kind="reports.build", dedupe_key=f"report-reconcile:{team_id}:{day}:{digest}",
                payload={"team_id": team_id, "report_date": day.isoformat()})
            requested += 1
    deliveries = list(NotificationDelivery.objects.filter(channel="EMAIL", state__in=("QUEUED", "HELD"),
        report_snapshot__team__is_deleted=False, report_snapshot__team__company__is_active=True).select_related(
        "report_snapshot__team", "recipient").order_by(F("last_checked_at").asc(nulls_first=True), "pk")[:limit])
    for delivery in deliveries:
        NotificationDelivery.objects.filter(pk=delivery.pk).update(last_checked_at=now)
        policy = _policy(delivery.report_snapshot.team)
        # Known transport failures retain the original job's bounded budget;
        # UNKNOWN deliveries and exhausted FAILED deliveries are never revived.
        if not policy or not policy.configured or OutboxJob.objects.filter(kind="reports.deliver",
                payload__delivery_id=delivery.pk, state__in=("QUEUED", "RUNNING")).exists():
            continue
        enqueue_job(kind="reports.deliver", dedupe_key=f"reports.deliver:{delivery.pk}:{policy.revision}",
            payload={"delivery_id": delivery.pk}, owner=delivery.recipient, run_after=delivery.next_attempt_at or now)
        requested += 1
    return {"requested": requested, "reviewed_pairs": len(set(pairs)), "reviewed_policies": len(policies), "reviewed_deliveries": len(deliveries)}
