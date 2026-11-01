"""One bounded proposal format; model output never supplies execution authority."""
from copy import deepcopy
from datetime import date, datetime
import re

from .exceptions import AIResponseValidationError


FEATURES = ("CAPTURE", "PLAN", "EXPLAIN", "BREAKDOWN", "BRIEFING", "COORDINATION_SUMMARY", "MEETING")
ALIASES = {"PLAN_DAY": "PLAN", "NEXT_TASK": "EXPLAIN", "PRIORITIZE": "PLAN",
           "BREAK_TASK": "BREAKDOWN", "EXPLAIN_TASK": "EXPLAIN", "SUMMARIZE_DAY": "BRIEFING"}
ACTION_FIELDS = {
    "CHECKLIST_ADD": {"id", "kind", "task_id", "title"},
    "CAPTURE_METADATA": {"id", "kind", "capture_id", "capture_kind", "title", "tags"},
    "PLAN_INTENTIONS": {"id", "kind", "plan_date", "task_ids", "priority_ids"},
    "SCHEDULE_BLOCK": {"id", "kind", "task_id", "starts_at", "ends_at"},
}
FEATURE_ACTIONS = {"CAPTURE": {"CAPTURE_METADATA"}, "PLAN": {"PLAN_INTENTIONS", "SCHEDULE_BLOCK"},
                   "BREAKDOWN": {"CHECKLIST_ADD"}, "EXPLAIN": set(), "BRIEFING": set(),
                   "COORDINATION_SUMMARY": set(), "MEETING": set()}


def normalize_feature(feature):
    value = str(feature).upper()
    value = ALIASES.get(value, value)
    if value not in FEATURES:
        raise AIResponseValidationError("Choose a supported workflow assistance feature.")
    return value


def _object(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _string(limit=4000):
    return {"type": "string", "maxLength": limit}


def _ids(limit=75):
    return {"type": "array", "items": {"type": "integer"}, "maxItems": limit}


ACTION_SCHEMAS = [
    _object({"id": _string(64), "kind": {"type": "string", "enum": ["CHECKLIST_ADD"]},
             "task_id": {"type": "integer"}, "title": _string(255)}),
    _object({"id": _string(64), "kind": {"type": "string", "enum": ["CAPTURE_METADATA"]},
             "capture_id": {"type": "integer"}, "capture_kind": {"type": "string", "enum": ["TASK", "IDEA", "NOTE", "LINK"]},
             "title": _string(255), "tags": {"type": "array", "items": _string(40), "maxItems": 12}}),
    _object({"id": _string(64), "kind": {"type": "string", "enum": ["PLAN_INTENTIONS"]},
             "plan_date": _string(10), "task_ids": _ids(50), "priority_ids": _ids(3)}),
    _object({"id": _string(64), "kind": {"type": "string", "enum": ["SCHEDULE_BLOCK"]},
             "task_id": {"type": "integer"}, "starts_at": _string(45), "ends_at": _string(45)}),
]
WORKFLOW_SCHEMA = _object({
    "schema_version": {"type": "string", "enum": ["3"]},
    "headline": _string(200), "summary": _string(), "reasoning": _string(),
    "actions": {"type": "array", "items": {"anyOf": ACTION_SCHEMAS}, "maxItems": 20},
    "questions": {"type": "array", "items": _string(600), "maxItems": 10},
    "citations": {"type": "array", "items": _object({"task_id": {"type": "integer"}, "claim": _string(600)}), "maxItems": 75},
    "date_hints": {"type": "array", "maxItems": 10, "items": _object({
        "phrase": _string(600), "interpreted_date": {"type": ["string", "null"]},
        "ambiguous": {"type": "boolean"}, "requires_confirmation": {"type": "boolean"},
    })},
})


def schema_for_feature(feature):
    feature = normalize_feature(feature)
    schema = deepcopy(WORKFLOW_SCHEMA)
    allowed = FEATURE_ACTIONS[feature]
    if allowed:
        schema["properties"]["actions"]["items"]["anyOf"] = [
            deepcopy(value) for value in ACTION_SCHEMAS
            if value["properties"]["kind"]["enum"][0] in allowed]
    else:
        schema["properties"]["actions"]["maxItems"] = 0
    return schema


def _fail():
    raise AIResponseValidationError()


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _text(value, maximum, *, blank=False):
    if type(value) is not str or len(value) > maximum or "\x00" in value or (not blank and not value.strip()):
        _fail()


def _array(value, maximum):
    if type(value) is not list or len(value) > maximum:
        _fail()


def _identifier(value, allowed):
    if type(value) is not int or value < 1 or value not in allowed:
        _fail()


def validate_workflow_payload(payload, context, feature=None):
    """Validate shape and source provenance; domain services recheck live state."""
    feature = normalize_feature(feature or context["feature"])
    _exact(payload, WORKFLOW_SCHEMA["required"])
    if payload["schema_version"] != "3":
        _fail()
    for key, limit in (("headline", 200), ("summary", 4000), ("reasoning", 4000)):
        _text(payload[key], limit)
    for key, limit in (("actions", 20), ("questions", 10), ("citations", 75), ("date_hints", 10)):
        _array(payload[key], limit)
    task_ids = {item["id"] for item in context.get("tasks", [])}
    capture_ids = {int(key) for key in context.get("proofs", {}).get("captures", {})}
    for text in payload["questions"]:
        _text(text, 600)
    for citation in payload["citations"]:
        _exact(citation, ("task_id", "claim"))
        _identifier(citation["task_id"], task_ids)
        _text(citation["claim"], 600)
    for hint in payload["date_hints"]:
        _exact(hint, ("phrase", "interpreted_date", "ambiguous", "requires_confirmation"))
        _text(hint["phrase"], 600)
        if type(hint["ambiguous"]) is not bool or hint["requires_confirmation"] is not True:
            _fail()
        if hint["interpreted_date"] is not None:
            _text(hint["interpreted_date"], 10)
            try:
                if date.fromisoformat(hint["interpreted_date"]).isoformat() != hint["interpreted_date"]:
                    _fail()
            except ValueError:
                _fail()
    if context.get("scope") == "MANAGER" and payload["actions"]:
        _fail()
    seen = set()
    for action in payload["actions"]:
        if type(action) is not dict or type(action.get("kind")) is not str or action["kind"] not in FEATURE_ACTIONS[feature]:
            _fail()
        _exact(action, ACTION_FIELDS[action["kind"]])
        _text(action["id"], 64)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", action["id"]) or action["id"] in seen:
            _fail()
        seen.add(action["id"])
        if "task_id" in action:
            _identifier(action["task_id"], task_ids)
        if action["kind"] == "CHECKLIST_ADD":
            _text(action["title"], 255)
        elif action["kind"] == "CAPTURE_METADATA":
            _identifier(action["capture_id"], capture_ids)
            if action["capture_kind"] not in ("TASK", "IDEA", "NOTE", "LINK"):
                _fail()
            _text(action["title"], 255, blank=True)
            _array(action["tags"], 12)
            for tag in action["tags"]:
                _text(tag, 40)
        elif action["kind"] == "PLAN_INTENTIONS":
            _text(action["plan_date"], 10)
            if action["plan_date"] not in context.get("proofs", {}).get("plans", {}):
                _fail()
            for key, maximum in (("task_ids", 50), ("priority_ids", 3)):
                _array(action[key], maximum)
                for identifier in action[key]:
                    _identifier(identifier, task_ids)
                if len(set(action[key])) != len(action[key]):
                    _fail()
            if not set(action["priority_ids"]).issubset(action["task_ids"]):
                _fail()
        elif action["kind"] == "SCHEDULE_BLOCK":
            for key in ("starts_at", "ends_at"):
                _text(action[key], 45)
                try:
                    parsed = datetime.fromisoformat(action[key])
                    if parsed.utcoffset() is None:
                        _fail()
                except ValueError:
                    _fail()
    return payload
