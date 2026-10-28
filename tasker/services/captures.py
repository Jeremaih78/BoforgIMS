"""Owner writes and explicit team reads, independent of AI and calendar dates."""
import uuid

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounting.models import Company
from tasker.capture_models import CaptureEvent, CaptureItem, normalize_tags
from tasker.models import DailyPlan, Goal, Initiative, Task, Team
from tasker.organization import validate_task_scope, visible_teams_for
from tasker.services.notes import normalize_notes


class CaptureConflict(ValidationError):
    def __init__(self, item):
        self.item = item
        self.version = item.version
        self.current = capture_payload(item)
        super().__init__("This capture changed elsewhere. Compare the saved version with your draft before saving again.")


class CaptureReplayError(ValidationError):
    pass


def capture_payload(item):
    return {"id": item.pk, "kind": item.kind, "title": item.title, "content": item.content,
            "url": item.url, "tags": item.tags, "pinned": item.pinned, "version": item.version,
            "visibility": item.visibility, "triage_state": item.triage_state,
            "is_deleted": item.is_deleted, "shared_team_id": item.shared_team_id}


def visible_captures_for(user, include_deleted=False):
    queryset = CaptureItem.all_objects.all() if include_deleted else CaptureItem.objects.all()
    if not user.is_authenticated or not user.is_active or not user.has_perm("tasker.view_task"):
        return queryset.none()
    return queryset.select_related("owner", "shared_team", "legacy_plan", "promoted_task", "promoted_initiative").filter(
        Q(owner=user) | Q(visibility=CaptureItem.Visibility.TEAM, shared_team__in=visible_teams_for(user), is_deleted=False)
    )


def _actor(user):
    if not user or not user.is_authenticated or not user.is_active:
        raise PermissionDenied
    # Serialize same-owner capture requests without blocking FK key-share
    # checks by organization writers that already hold a company/team lock.
    actor = get_user_model().objects.select_for_update(no_key=True).filter(pk=user.pk, is_active=True).first()
    if actor is None or not actor.has_perm("tasker.view_task"):
        raise PermissionDenied
    return actor


def _owned(user, pk):
    item = CaptureItem.all_objects.select_for_update().filter(pk=pk, owner=user).first()
    if item is None:
        raise PermissionDenied
    return item


def _version(value):
    try:
        if isinstance(value, bool) or int(value) < 1 or str(int(value)) != str(value):
            raise ValueError
        return int(value)
    except (TypeError, ValueError):
        raise ValidationError({"version": "Reload this capture before saving."}) from None


def _content_data(*, kind, content, title="", url="", tags=None, pinned=False):
    if not all(isinstance(value, str) for value in (kind, content, title, url)):
        raise ValidationError("Capture fields must contain text.")
    if not isinstance(pinned, bool):
        raise ValidationError({"pinned": "Choose a valid pin state."})
    if not content.strip() and kind == CaptureItem.Kind.LINK and url.strip():
        content = url.strip()
    return {"kind": kind, "content": normalize_notes(content), "title": title.strip(), "url": url.strip(),
            "tags": normalize_tags(tags), "pinned": pinned}


def _audit(item, user, action, **ids):
    CaptureEvent.objects.create(capture=item, actor=user, action=action, version=item.version, details=ids)


def _save(item, user, action, **ids):
    item.version += 1
    item.updated_by = user
    item.save()
    _audit(item, user, action, **ids)
    return item


@transaction.atomic
def create_capture(*, user, client_key, kind, content, title="", url="", tags=None, pinned=False):
    user = _actor(user)
    try:
        key = uuid.UUID(str(client_key))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError({"client_key": "Reload capture to obtain a new submission identifier."}) from None
    data = _content_data(kind=kind, content=content, title=title, url=url, tags=tags, pinned=pinned)
    existing = CaptureItem.all_objects.select_for_update().filter(owner=user, client_key=key).first()
    if existing:
        # The retained original payload remains stable after later edits.
        fingerprint = _creation_fingerprint(data)
        if existing.submission_fingerprint == fingerprint:
            return existing
        raise CaptureReplayError("This submission identifier already belongs to another capture. Reload before creating new work.")
    item = CaptureItem(owner=user, client_key=key, submission_fingerprint=_creation_fingerprint(data),
                       original_text=content if content.strip() else data["content"], created_by=user, updated_by=user, **data)
    item.save()
    _audit(item, user, "capture.created")
    return item


def _creation_fingerprint(data):
    # Idempotency proof only, not content exported into shared task/company logs.
    import hashlib
    import json
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@transaction.atomic
def update_capture(*, user, pk, version, kind, title, content, url="", tags=None, pinned=False):
    user = _actor(user)
    item = _owned(user, pk)
    version = _version(version)
    if item.is_deleted:
        raise ValidationError("Restore this capture before editing it.")
    data = _content_data(kind=kind, title=title, content=content, url=url, tags=tags, pinned=pinned)
    same = all((normalize_notes(item.content) if field == "content" else getattr(item, field)) == value for field, value in data.items())
    if version != item.version and not (version < item.version and same):
        raise CaptureConflict(item)
    if same:
        return item
    for field, value in data.items():
        setattr(item, field, value)
    return _save(item, user, "capture.updated")


def _sharing_team(user, team):
    team_id = getattr(team, "pk", team)
    company_id = Team.objects.filter(pk=team_id).values_list("company_id", flat=True).first()
    if not company_id:
        raise PermissionDenied
    Company.objects.select_for_update().get(pk=company_id)
    team = Team.objects.select_for_update(of=("self",)).select_related("company").filter(pk=team_id, company__is_active=True).first()
    if team is None or not visible_teams_for(user).filter(pk=team.pk).exists():
        raise PermissionDenied
    return team


@transaction.atomic
def mutate_capture(*, user, pk, version, action, team=None):
    user = _actor(user)
    item = _owned(user, pk)
    version = _version(version)
    if item.is_deleted and action not in ("restore", "delete"):
        raise ValidationError("Restore this capture before changing it.")
    if action in ("pin", "unpin"):
        desired = {"pinned": action == "pin"}
    elif action in ("archive", "unarchive"):
        state = CaptureItem.TriageState.ARCHIVED if action == "archive" else (
            CaptureItem.TriageState.PROMOTED if item.promoted_task_id or item.promoted_initiative_id else CaptureItem.TriageState.INBOX)
        desired = {"triage_state": state}
    elif action in ("delete", "restore"):
        desired = {"is_deleted": action == "delete"}
    elif action == "share":
        selected = _sharing_team(user, team)
        desired = {"visibility": CaptureItem.Visibility.TEAM, "shared_team_id": selected.pk}
    elif action == "private":
        desired = {"visibility": CaptureItem.Visibility.PRIVATE, "shared_team_id": None}
    else:
        raise ValidationError("Choose a supported capture action.")
    same = all(getattr(item, field) == value for field, value in desired.items())
    if version != item.version and not (version < item.version and same):
        raise CaptureConflict(item)
    if same:
        return item
    for field, value in desired.items():
        setattr(item, field, value)
    if action in ("delete", "restore"):
        item.deleted_at = timezone.now() if item.is_deleted else None
        item.deleted_by = user if item.is_deleted else None
    return _save(item, user, f"capture.{action}", team_id=item.shared_team_id)


@transaction.atomic
def promote_capture(*, user, pk, version, target="task", title="", due_date=None, team=None, goal=None):
    user = _actor(user)
    item = _owned(user, pk)
    version = _version(version)
    if target not in ("task", "initiative"):
        raise ValidationError({"target": "Choose task or initiative."})
    if version > item.version:
        raise CaptureConflict(item)
    if item.promoted_task_id or item.promoted_initiative_id:
        if (target == "task") != bool(item.promoted_task_id):
            raise CaptureReplayError("This capture already has a promoted destination.")
        if item.promoted_task_id:
            from tasker.policies import visible_tasks_for
            if not user.has_perm("tasker.add_task"):
                raise PermissionDenied
            destination = visible_tasks_for(user).filter(pk=item.promoted_task_id).first()
        else:
            from tasker.services.goals import _lock_scope, _require_manage, visible_initiatives_for
            if not getattr(settings, "TASKER_DIRECTION_ENABLED", False):
                raise PermissionDenied
            destination = visible_initiatives_for(user).filter(pk=item.promoted_initiative_id).first()
            if destination:
                _lock_scope(destination.goal.company_id, destination.goal.team_id)
                destination.refresh_from_db()
                _require_manage(user, destination.goal, "add_initiative")
        if destination is None:
            raise PermissionDenied
        return destination
    if item.is_deleted or item.triage_state == CaptureItem.TriageState.ARCHIVED:
        raise ValidationError("Restore or unarchive this capture before promoting it.")
    if version != item.version:
        raise CaptureConflict(item)
    selected_title = title.strip() if isinstance(title, str) else ""
    selected_title = selected_title or item.title or item.content.splitlines()[0][:255]
    description = item.content
    if item.url and item.url not in description:
        description += "\n\n" + item.url
    description += "\n\nCapture source (access follows its visibility): " + item.get_absolute_url()
    if target == "task":
        if not user.has_perm("tasker.add_task"):
            raise PermissionDenied
        selected_team = _sharing_team(user, team) if team else None
        task = Task(owner=user, title=selected_title, description=description, due_date=due_date,
                    team=selected_team, company=selected_team.company if selected_team else None,
                    assigned_by=user, created_by=user, updated_by=user)
        # Allocate the URL before validation. Different users can promote equal
        # titles concurrently without racing the title-based legacy slug loop.
        task.slug = f"capture-{item.pk}-{uuid.uuid4().hex}"
        validate_task_scope(user, task, lock=True)
        task.full_clean()
        task.save()
        item.promoted_task = task
        destination = task
    else:
        if not getattr(settings, "TASKER_DIRECTION_ENABLED", False):
            raise PermissionDenied
        from tasker.services.goals import _lock_scope, _require_manage, _validate_owner
        selected_goal = Goal.objects.select_related("company", "team").filter(pk=getattr(goal, "pk", goal)).first()
        if selected_goal is None:
            raise PermissionDenied
        _lock_scope(selected_goal.company_id, selected_goal.team_id)
        selected_goal.refresh_from_db()
        _require_manage(user, selected_goal, "add_initiative")
        _validate_owner(user.pk, selected_goal)
        destination = Initiative.objects.create(goal=selected_goal, owner=user, title=selected_title,
                                                description=description, created_by=user, updated_by=user)
        item.promoted_initiative = destination
    item.triage_state = CaptureItem.TriageState.PROMOTED
    _save(item, user, "capture.promoted", target=target, destination_id=destination.pk)
    return destination


@transaction.atomic
def import_legacy_notes(*, user, plan_pk):
    user = _actor(user)
    plan = DailyPlan.all_objects.select_for_update().filter(pk=plan_pk, owner=user).first()
    if plan is None or not user.has_perm("tasker.view_dailyplan"):
        raise PermissionDenied
    existing = CaptureItem.all_objects.filter(legacy_plan=plan).first()
    if existing:
        return existing
    if not plan.quick_notes.strip():
        raise ValidationError("This scratchpad is empty.")
    item = CaptureItem(owner=user, kind=CaptureItem.Kind.NOTE, title=f"Scratchpad · {plan.plan_date:%d %b %Y}",
                       content=plan.quick_notes, original_text=plan.quick_notes, legacy_plan=plan,
                       created_by=user, updated_by=user)
    item.save()
    _audit(item, user, "capture.legacy_imported", plan_id=plan.pk)
    return item


def optional_capture_proposal(*, item, user):
    """Future AI seam: original capture is already durable; no provider is called."""
    if item.owner_id != user.pk or not visible_captures_for(user).filter(pk=item.pk).exists():
        raise PermissionDenied
    return {"available": False, "reason": "AI extraction is not enabled; choose dates and goals explicitly.",
            "capture_id": item.pk, "version": item.version, "proposal": None, "requires_confirmation": True}
