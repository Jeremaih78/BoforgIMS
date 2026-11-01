"""Reviewed AI changes use the existing capture, plan, checklist and schedule services.

Model output supplies suggestions, never the actor or write authority. Apply and
undo serialize the actor, source records and proposal; no provider is called here.
"""
from copy import deepcopy
from datetime import date, datetime, timedelta
import hashlib
import json
import uuid

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounting.models import Company
from tasker.capture_models import CaptureItem, normalize_tags
from tasker.models import DailyPlan, DailyReview, Task, TaskActivity, TaskChecklistItem, Team
from tasker.organization import require_task_access
from tasker.planning_models import ScheduleBlock
from tasker.policies import can_change_task
from tasker.proposal_models import AIProposal, AIProposalEvent
from tasker.services.captures import capture_payload, update_capture
from tasker.services.coordination import scoped_tasks_for
from tasker.services.planner import plan_version, save_daily_plan
from tasker.services.workbench import work_version


class AIProposalConflict(ValidationError):
    pass


def proposal_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _actor(user, *, lock=False, require_ai=True):
    if not getattr(user, "is_authenticated", False):
        raise PermissionDenied
    rows = get_user_model().objects
    if lock:
        rows = rows.select_for_update(no_key=True)
    actor = rows.filter(pk=user.pk, is_active=True).first()
    permissions = ("tasker.use_ai", "tasker.view_task") if require_ai else ()
    if actor is None or (permissions and not actor.has_perms(permissions)):
        raise PermissionDenied
    return actor


def _revision(value):
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise AIProposalConflict("Reload the proposal and review its current version.") from None


def _request(proposal, actor):
    request = apps.get_model("tasker", "AIWorkRequest").objects.filter(pk=proposal.request_id, owner=actor).first()
    if request is None or request.scope != proposal.scope or request.feature != proposal.feature:
        raise PermissionDenied
    if request.status != "COMPLETED" or request.content_redacted_at is not None:
        raise AIProposalConflict("This AI result is no longer available to apply.")
    if request.context_digest != proposal.context_digest:
        raise AIProposalConflict("The proposal source evidence changed.")
    if proposal_digest(request.context_snapshot) != request.context_digest:
        raise AIProposalConflict("The retained source context changed.")
    source = request.context_snapshot.get("source", {})
    if any(source.get(key) != getattr(request, key) for key in ("task_id", "capture_id", "team_id")):
        raise PermissionDenied
    from tasker.ai.workflow_context import authorize_workflow_sources
    authorize_workflow_sources(user=actor, context=request.context_snapshot)
    return request


def _event(proposal, actor, action, action_ids=(), reason_code=None):
    details = {"action_ids": list(action_ids)}
    if reason_code:
        details["reason_code"] = reason_code
    return AIProposalEvent.objects.create(proposal=proposal, actor=actor, action=action, revision=proposal.revision, details=details)


def _content(proposal):
    if proposal.content_redacted_at is not None:
        raise AIProposalConflict("This result was redacted. Its content and automatic undo are unavailable.")
    if proposal.payload_digest != proposal_digest(proposal.payload):
        raise AIProposalConflict("The stored proposal content changed. Generate a new suggestion.")


def _action_sources(actions):
    tasks, captures, days = set(), set(), set()
    for action in actions:
        if "task_id" in action:
            tasks.add(action["task_id"])
        if "capture_id" in action:
            captures.add(action["capture_id"])
        if action["kind"] == "PLAN_INTENTIONS":
            tasks.update(action["task_ids"])
            tasks.update(action["priority_ids"])
            days.add(date.fromisoformat(action["plan_date"]))
    return tasks, captures, days


def _plan_state(plan, day):
    if plan is None:
        return {"exists": False, "date": day.isoformat()}
    return {"exists": True, "id": plan.pk, "date": day.isoformat(),
            "focus_area": plan.focus_area, "motivational_quote": plan.motivational_quote,
            "quick_notes": plan.quick_notes,
            "task_ids": sorted(plan.planned_tasks.values_list("pk", flat=True)),
            "priority_ids": sorted(plan.top_priorities.values_list("pk", flat=True))}


def _plan_guard(plan):
    if plan is None:
        return {"version": plan_version(None), "review": None}
    review = DailyReview.all_objects.filter(plan=plan).values("pk", "updated_at", "is_deleted").first()
    if review:
        review["updated_at"] = review["updated_at"].isoformat()
    return {"version": plan_version(plan), "review": review, "deleted": plan.is_deleted}


def _task_guard(task):
    from tasker.ai.workflow_context import task_children_proof
    return {"version": work_version(task), "children": task_children_proof(task)}


def _capture_guard(item):
    return {"version": item.version, "content": proposal_digest(capture_payload(item))}


def _lock_sources(actor, actions, *, before_state=None, source_proofs=None):
    task_ids, capture_ids, days = _action_sources(actions)
    source_proofs = source_proofs or {}
    task_ids.update(int(pk) for pk in source_proofs.get("tasks", {}))
    capture_ids.update(int(pk) for pk in source_proofs.get("captures", {}))
    before_state = before_state or {}
    for state in before_state.get("plans", {}).values():
        days.add(date.fromisoformat(state["date"]))
        task_ids.update(state.get("task_ids", []))
        task_ids.update(state.get("priority_ids", []))
    plans = {item.plan_date: item for item in DailyPlan.objects.select_for_update().filter(owner=actor, plan_date__in=days).order_by("plan_date", "pk")}
    for plan in plans.values():
        task_ids.update(plan.planned_tasks.values_list("pk", flat=True))
        task_ids.update(plan.top_priorities.values_list("pk", flat=True))
    tasks = {item.pk: item for item in Task.all_objects.select_for_update().filter(pk__in=task_ids).order_by("pk")}
    list(Company.objects.select_for_update().filter(pk__in={item.company_id for item in tasks.values() if item.company_id}).order_by("pk"))
    list(Team.all_objects.select_for_update().filter(pk__in={item.team_id for item in tasks.values() if item.team_id}).order_by("pk"))
    captures = {item.pk: item for item in CaptureItem.all_objects.select_for_update().filter(pk__in=capture_ids).order_by("pk")}
    if any(action["kind"] == "SCHEDULE_BLOCK" for action in actions):
        from tasker.services.capacity import locked_policy
        locked_policy(actor)
    return tasks, captures, plans, days


def _live_task(actor, task):
    if task is None or task.owner_id != actor.pk or task.is_deleted or task.archived or task.status in (Task.Status.COMPLETED, Task.Status.CANCELLED):
        raise AIProposalConflict("A proposed task changed ownership or is no longer active.")
    require_task_access(actor, task, capability="tasker.view_task")
    if not can_change_task(actor, task):
        raise PermissionDenied


def _live_capture(actor, item):
    if item is None or item.owner_id != actor.pk or item.is_deleted or item.visibility != CaptureItem.Visibility.PRIVATE or item.triage_state != CaptureItem.TriageState.INBOX:
        raise AIProposalConflict("A capture changed audience or is no longer in your private Inbox.")


def _selected(proposal, selected_action_ids):
    if not isinstance(selected_action_ids, (list, tuple)) or not 1 <= len(selected_action_ids) <= 20:
        raise ValidationError("Select at least one displayed action.")
    if any(type(value) is not str for value in selected_action_ids) or len(set(selected_action_ids)) != len(selected_action_ids):
        raise ValidationError("Choose distinct displayed action identifiers.")
    offered = {action["id"]: action for action in proposal.payload.get("actions", [])}
    if any(value not in offered for value in selected_action_ids):
        raise ValidationError("Only the actions in this proposal can be applied.")
    ids = set(selected_action_ids)
    return [action for action in proposal.payload["actions"] if action["id"] in ids]


def _validate_live(actor, proposal, actions, records, *, check_proofs=True):
    tasks, captures, plans, days = records
    original_tasks = {int(pk) for pk in proposal.source_proofs.get("tasks", {})}
    original_captures = {int(pk) for pk in proposal.source_proofs.get("captures", {})}
    if any(pk not in tasks or tasks[pk].owner_id != actor.pk or tasks[pk].is_deleted for pk in original_tasks):
        raise PermissionDenied
    if scoped_tasks_for(actor).filter(pk__in=original_tasks).count() != len(original_tasks):
        raise PermissionDenied
    if any(pk not in captures or captures[pk].owner_id != actor.pk or captures[pk].is_deleted
           or captures[pk].visibility != CaptureItem.Visibility.PRIVATE for pk in original_captures):
        raise PermissionDenied
    task_ids, capture_ids, _ = _action_sources(actions)
    for pk in task_ids:
        task = tasks.get(pk)
        _live_task(actor, task)
        if check_proofs:
            expected = proposal.source_proofs.get("tasks", {}).get(str(pk))
            child = proposal.source_proofs.get("task_children", {}).get(str(pk))
            from tasker.ai.workflow_context import task_children_proof
            if expected != work_version(task) or child != task_children_proof(task):
                raise AIProposalConflict("A task or its checklist changed after this suggestion. Generate a fresh proposal.")
    for pk in capture_ids:
        item = captures.get(pk)
        _live_capture(actor, item)
        if check_proofs and proposal.source_proofs.get("captures", {}).get(str(pk)) != item.version:
            raise AIProposalConflict("The capture changed after this suggestion. Generate a fresh proposal.")
    for day in days:
        if check_proofs and proposal.source_proofs.get("plans", {}).get(day.isoformat()) != plan_version(plans.get(day)):
            raise AIProposalConflict("The day's intentions changed. Review a fresh proposal.")
    signatures = set()
    targets = set()
    checklist_titles = {}
    if check_proofs and any(action["kind"] in ("PLAN_INTENTIONS", "SCHEDULE_BLOCK") for action in actions):
        if any(hint["ambiguous"] or hint["interpreted_date"] is None for hint in proposal.payload["date_hints"]):
            raise AIProposalConflict("The proposed dates need clarification. Enter an unambiguous date in a new request or use Plan manually.")
    for action in actions:
        signature = json.dumps({key: value for key, value in action.items() if key != "id"}, sort_keys=True)
        if signature in signatures:
            raise ValidationError("The same change cannot appear twice in a proposal.")
        signatures.add(signature)
        if action["kind"] in ("CAPTURE_METADATA", "PLAN_INTENTIONS"):
            target = (action["kind"], action.get("capture_id", action.get("plan_date")))
            if target in targets:
                raise ValidationError("Review one metadata or intention change for each source at a time.")
            targets.add(target)
        if action["kind"] == "CHECKLIST_ADD" and not actor.has_perm("tasker.add_taskchecklistitem"):
            raise PermissionDenied
        if check_proofs and action["kind"] == "CHECKLIST_ADD":
            pk = action["task_id"]
            if pk not in checklist_titles:
                checklist_titles[pk] = {" ".join(title.split()).casefold() for title in
                    TaskChecklistItem.objects.filter(task_id=pk).values_list("title", flat=True)}
            normalized = " ".join(action["title"].split()).casefold()
            if normalized in checklist_titles[pk]:
                raise AIProposalConflict("This checklist step is already recorded. Review the existing steps before adding it.")
            checklist_titles[pk].add(normalized)
        if action["kind"] == "CAPTURE_METADATA":
            item = captures[action["capture_id"]]
            if action["capture_kind"] == CaptureItem.Kind.LINK and not item.url:
                raise ValidationError("A link suggestion needs a link entered explicitly in the original capture.")
            normalize_tags(action["tags"])
        if action["kind"] == "PLAN_INTENTIONS":
            capability = "tasker.change_dailyplan" if plans.get(date.fromisoformat(action["plan_date"])) else "tasker.add_dailyplan"
            if not actor.has_perms(("tasker.view_dailyplan", capability)):
                raise PermissionDenied
        if check_proofs and action["kind"] == "SCHEDULE_BLOCK":
            from tasker.services.scheduling import preview_schedule
            preview = preview_schedule(user=actor, task=tasks[action["task_id"]], starts_at=datetime.fromisoformat(action["starts_at"]), ends_at=datetime.fromisoformat(action["ends_at"]))
            if preview["hard_errors"] or preview["warnings"]:
                raise AIProposalConflict("This proposed block is not currently feasible. Review working hours, estimates, conflicts and dependencies in Plan.")


def _save_intentions(actor, day, task_ids, priority_ids, plan):
    from tasker.forms import DailyPlanForm
    values = {"focus_area": plan.focus_area if plan else "", "motivational_quote": plan.motivational_quote if plan else "",
              "quick_notes": plan.quick_notes if plan else "", "planned_tasks": list(task_ids), "top_priorities": list(priority_ids),
              "plan_version": plan_version(plan)}
    form = DailyPlanForm(data=values, instance=plan, user=actor, plan_date=day)
    if not form.is_valid():
        raise AIProposalConflict("The day's intentions are no longer valid. Review the current tasks and plan.")
    return save_daily_plan(form=form, user=actor, plan_date=day)


def _save_capture_metadata(actor, item, *, kind, title, tags):
    source = (item.content, item.original_text, item.url, item.pinned, item.visibility, item.shared_team_id)
    update_capture(user=actor, pk=item.pk, version=item.version, kind=kind, title=title, tags=tags,
                   content=item.content, url=item.url, pinned=item.pinned)
    item.refresh_from_db()
    if source != (item.content, item.original_text, item.url, item.pinned, item.visibility, item.shared_team_id):
        # Legacy capture text may not have gone through the normalizer. Never
        # normalize or rewrite its body as a side effect of an AI metadata edit.
        raise AIProposalConflict("This capture needs a manual metadata review to preserve its original source exactly.")


def _checkpoint(actor, records, effects):
    tasks, captures, plans, days = records
    result = {"tasks": {}, "captures": {}, "plans": {}, "blocks": {}}
    for pk, task in tasks.items():
        task.refresh_from_db()
        result["tasks"][str(pk)] = _task_guard(task)
    for pk, item in captures.items():
        item.refresh_from_db()
        result["captures"][str(pk)] = _capture_guard(item)
    for day in days:
        plan = DailyPlan.objects.filter(owner=actor, plan_date=day).first()
        result["plans"][day.isoformat()] = _plan_guard(plan)
    for effect in effects:
        if effect["kind"] == "SCHEDULE_BLOCK":
            block = ScheduleBlock.objects.get(pk=effect["block_id"])
            result["blocks"][str(block.pk)] = {"revision": str(block.revision), "state": block.state}
    return result


@transaction.atomic
def create_proposal(*, user, request_id, feature, payload, context):
    from tasker.ai.workflow_schema import normalize_feature, validate_workflow_payload
    actor = _actor(user)
    feature = normalize_feature(feature)
    request = apps.get_model("tasker", "AIWorkRequest").objects.filter(pk=request_id, owner=actor).first()
    if request is None or request.status != "COMPLETED" or request.content_redacted_at is not None:
        raise PermissionDenied
    if request.feature != feature or context != request.context_snapshot or context.get("actor_id") != actor.pk or context.get("scope") != request.scope:
        raise PermissionDenied
    digest = proposal_digest(context)
    if request.context_digest != digest:
        raise AIProposalConflict("The request context fingerprint does not match its source.")
    validate_workflow_payload(payload, context, feature=feature)
    if len(json.dumps(payload, ensure_ascii=False).encode()) > 64000:
        raise ValidationError("The AI result is too large to review safely.")
    expires = min(timezone.now() + timedelta(minutes=30), request.expires_at)
    if expires <= timezone.now():
        raise AIProposalConflict("This request expired before its result could be reviewed.")
    proposal, created = AIProposal.objects.get_or_create(request=request, defaults={
        "owner": actor, "feature": feature, "scope": request.scope, "payload": deepcopy(payload),
        "payload_digest": proposal_digest(payload), "context_digest": digest,
        "source_proofs": deepcopy(context.get("proofs", {})), "expires_at": expires,
        "created_by": actor, "updated_by": actor,
    })
    if proposal.owner_id != actor.pk or proposal.payload_digest != proposal_digest(payload) or proposal.context_digest != digest:
        raise AIProposalConflict("A different result was already recorded for this request.")
    if created:
        _event(proposal, actor, "created")
    return proposal


def get_proposal(*, user, proposal_id):
    actor = _actor(user)
    proposal = AIProposal.objects.filter(pk=proposal_id, owner=actor).first()
    if proposal is None:
        raise PermissionDenied
    if proposal.content_redacted_at is not None:
        return proposal
    _content(proposal)
    _request(proposal, actor)
    return proposal


@transaction.atomic
def apply_proposal(*, user, proposal_id, expected_revision, selected_action_ids):
    actor = _actor(user, lock=True)
    preview = AIProposal.objects.filter(pk=proposal_id, owner=actor).first()
    if preview is None:
        raise PermissionDenied
    _content(preview)
    _request(preview, actor)
    if preview.scope != "PERSONAL":
        raise PermissionDenied
    actions = _selected(preview, selected_action_ids)
    records = _lock_sources(actor, actions, source_proofs=preview.source_proofs)
    proposal = AIProposal.objects.select_for_update().get(pk=preview.pk, owner=actor)
    _content(proposal)
    _request(proposal, actor)
    revision = _revision(expected_revision)
    selected_ids = [action["id"] for action in actions]
    if proposal.state == AIProposal.State.APPLIED:
        if proposal.selected_action_ids == selected_ids and revision in (str(proposal.revision), proposal.apply_receipt.get("review_revision")):
            _validate_live(actor, proposal, actions, records, check_proofs=False)
            return proposal
        raise AIProposalConflict("This proposal was already applied with a different selection.")
    if proposal.state != AIProposal.State.PENDING or proposal.expires_at <= timezone.now() or revision != str(proposal.revision):
        raise AIProposalConflict("This proposal expired, changed or was already consumed. Review a fresh suggestion.")
    _validate_live(actor, proposal, actions, records)
    tasks, captures, plans, days = records
    before = {"task_progress": {str(pk): task.progress for pk, task in tasks.items() if task.owner_id == actor.pk},
              "captures": {str(pk): {key: getattr(item, key) for key in ("kind", "title", "tags")} for pk, item in captures.items()},
              "plans": {day.isoformat(): _plan_state(plans.get(day), day) for day in days}}
    effects = []
    for action in actions:
        kind = action["kind"]
        if kind == "CHECKLIST_ADD":
            from tasker.services.tasks import add_checklist_item
            item = add_checklist_item(task=tasks[action["task_id"]], user=actor, title=action["title"])
            effects.append({"kind": kind, "action_id": action["id"], "task_id": item.task_id, "item_id": item.pk})
        elif kind == "CAPTURE_METADATA":
            item = captures[action["capture_id"]]
            _save_capture_metadata(actor, item, kind=action["capture_kind"], title=action["title"], tags=action["tags"])
            effects.append({"kind": kind, "action_id": action["id"], "capture_id": item.pk})
        elif kind == "PLAN_INTENTIONS":
            day = date.fromisoformat(action["plan_date"])
            plan = _save_intentions(actor, day, action["task_ids"], action["priority_ids"], plans.get(day))
            plans[day] = plan
            effects.append({"kind": kind, "action_id": action["id"], "plan_date": day.isoformat(), "plan_id": plan.pk})
        elif kind == "SCHEDULE_BLOCK":
            from tasker.services.scheduling import accept_schedule, preview_schedule, save_schedule_draft
            task = tasks[action["task_id"]]
            block = save_schedule_draft(user=actor, task=task, starts_at=datetime.fromisoformat(action["starts_at"]), ends_at=datetime.fromisoformat(action["ends_at"]))
            fresh = preview_schedule(user=actor, task=task, starts_at=block.starts_at, ends_at=block.ends_at, block=block)
            if fresh["hard_errors"] or fresh["warnings"]:
                raise AIProposalConflict("The proposed schedule is no longer feasible.")
            block = accept_schedule(user=actor, block=block, expected_version=str(block.revision), preview_version=fresh["version"])
            effects.append({"kind": kind, "action_id": action["id"], "task_id": task.pk, "block_id": block.pk})
    proposal.before_state = before
    proposal.after_state = _checkpoint(actor, records, effects)
    proposal.selected_action_ids = selected_ids
    proposal.apply_receipt = {"review_revision": revision, "effects": effects}
    proposal.state, proposal.applied_at, proposal.revision, proposal.updated_by = AIProposal.State.APPLIED, timezone.now(), uuid.uuid4(), actor
    proposal.save()
    _event(proposal, actor, "applied", selected_ids)
    return proposal


@transaction.atomic
def undo_proposal(*, user, proposal_id, expected_revision):
    actor = _actor(user, lock=True)
    preview = AIProposal.objects.filter(pk=proposal_id, owner=actor).first()
    if preview is None:
        raise PermissionDenied
    _content(preview)
    _request(preview, actor)
    if preview.scope != "PERSONAL":
        raise PermissionDenied
    actions = _selected(preview, preview.selected_action_ids)
    records = _lock_sources(actor, actions, before_state=preview.before_state, source_proofs=preview.source_proofs)
    proposal = AIProposal.objects.select_for_update().get(pk=preview.pk, owner=actor)
    _content(proposal)
    _request(proposal, actor)
    revision = _revision(expected_revision)
    if proposal.state == AIProposal.State.UNDONE and revision in (str(proposal.revision), proposal.apply_receipt.get("undo_review_revision")):
        _validate_live(actor, proposal, actions, records, check_proofs=False)
        return proposal
    if proposal.state != AIProposal.State.APPLIED or revision != str(proposal.revision):
        raise AIProposalConflict("This proposal is no longer the applied version you reviewed.")
    _validate_live(actor, proposal, actions, records, check_proofs=False)
    effects = proposal.apply_receipt.get("effects", [])
    if _checkpoint(actor, records, effects) != proposal.after_state:
        raise AIProposalConflict("Work changed after this proposal was applied. Automatic undo would overwrite a later edit.")
    tasks, captures, plans, days = records
    checklist_tasks = set()
    for effect in reversed(effects):
        kind = effect["kind"]
        if kind == "CHECKLIST_ADD":
            if not actor.has_perm("tasker.delete_taskchecklistitem"):
                # Existing employee roles authorize editing/checklist creation;
                # removing only this untouched, AI-created row is compensation.
                if not actor.has_perm("tasker.change_taskchecklistitem"):
                    raise PermissionDenied
            item = TaskChecklistItem.objects.select_for_update().filter(pk=effect["item_id"], task_id=effect["task_id"]).first()
            if item is None or item.is_completed:
                raise AIProposalConflict("A generated checklist item changed. Review it manually.")
            item.delete(user=actor)
            checklist_tasks.add(item.task_id)
        elif kind == "CAPTURE_METADATA":
            item = captures[effect["capture_id"]]
            state = proposal.before_state["captures"][str(item.pk)]
            _save_capture_metadata(actor, item, kind=state["kind"], title=state["title"], tags=state["tags"])
        elif kind == "PLAN_INTENTIONS":
            day = date.fromisoformat(effect["plan_date"])
            plan = DailyPlan.objects.get(pk=effect["plan_id"], owner=actor)
            state = proposal.before_state["plans"][day.isoformat()]
            if state["exists"]:
                _save_intentions(actor, day, state["task_ids"], state["priority_ids"], plan)
            else:
                if not actor.has_perm("tasker.change_dailyplan") or DailyReview.all_objects.filter(plan=plan).exists():
                    raise AIProposalConflict("The generated plan has been used or cannot be removed. Review its intentions manually.")
                plan.top_priorities.clear()
                plan.planned_tasks.clear()
                plan.delete(user=actor)
        elif kind == "SCHEDULE_BLOCK":
            from tasker.services.scheduling import cancel_schedule
            block = ScheduleBlock.objects.select_for_update().get(pk=effect["block_id"])
            cancel_schedule(user=actor, block=block, expected_version=str(block.revision))
    for pk in checklist_tasks:
        from tasker.services.lifecycle import save_without_task_activity
        from tasker.services.tasks import sync_checklist_progress
        task = tasks[pk]
        sync_checklist_progress(task=task, user=actor)
        task.refresh_from_db()
        original = proposal.before_state["task_progress"][str(pk)]
        if task.progress != original:
            task.progress, task.updated_by = original, actor
            save_without_task_activity(task, update_fields=["progress", "updated_by", "updated_at"])
        TaskActivity.objects.create(task=task, event_type=TaskActivity.EventType.CHECKLIST,
                                    description="Untouched AI checklist additions undone", changes={"proposal_id": str(proposal.pk)}, created_by=actor, updated_by=actor)
    proposal.apply_receipt = {**proposal.apply_receipt, "undo_review_revision": revision}
    proposal.state, proposal.undone_at, proposal.revision, proposal.updated_by = AIProposal.State.UNDONE, timezone.now(), uuid.uuid4(), actor
    proposal.save()
    _event(proposal, actor, "undone", proposal.selected_action_ids)
    return proposal


@transaction.atomic
def cancel_proposal(*, user, proposal_id, expected_revision):
    actor = _actor(user, lock=True, require_ai=False)
    proposal = AIProposal.objects.select_for_update().filter(pk=proposal_id, owner=actor).first()
    if proposal is None:
        raise PermissionDenied
    revision = _revision(expected_revision)
    if proposal.state == AIProposal.State.CANCELLED:
        return proposal
    if proposal.state != AIProposal.State.PENDING or revision != str(proposal.revision):
        raise AIProposalConflict("This proposal is no longer pending.")
    proposal.state, proposal.revision, proposal.updated_by = AIProposal.State.CANCELLED, uuid.uuid4(), actor
    proposal.save()
    _event(proposal, actor, "cancelled")
    return proposal
