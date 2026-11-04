"""Tasker/Cloud protocol 2. Standard-library only; copied verbatim into Cloud."""

import hashlib
import hmac
import json
import re
import time
import uuid
from dataclasses import dataclass

VERSION = "2"
STATES = frozenset(
    {
        "queued",
        "scheduled",
        "processing",
        "deferred",
        "smtp_accepted",
        "rejected",
        "failed",
        "cancelled",
        "unknown",
    }
)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def sign(secret, timestamp, body):
    return hmac.new(
        secret.encode(), str(timestamp).encode() + b"." + body, hashlib.sha256
    ).hexdigest()


def verify(keys, key_id, timestamp, body, signature, *, now=None, tolerance=60):
    try:
        secret = keys[key_id]
        if (
            len(secret) < 32
            or abs((time.time() if now is None else now) - int(timestamp)) > tolerance
        ):
            raise ValueError()
        if not hmac.compare_digest(sign(secret, timestamp, body), signature):
            raise ValueError()
    except (KeyError, TypeError, ValueError):
        raise ValueError("invalid_signature") from None


@dataclass(frozen=True)
class SubmissionReceipt:
    message_id: str
    tenant_id: str
    client_id: str
    delivery_id: str
    payload_digest: str
    state: str
    state_version: int
    accepted_at: str | None
    provider_acceptance: str = "NOT_CONFIRMED"
    attempt_id: str | None = None

    @classmethod
    def parse(cls, value):
        try:
            if value["schema_version"] != VERSION or value["status"] not in STATES:
                raise ValueError()
            version = value["state_version"]
            if type(version) is not int or version < 1:
                raise ValueError()
            names = ("id", "tenant_id", "client_id", "delivery_id", "payload_digest")
            if any(not isinstance(value[n], str) or not value[n] for n in names):
                raise ValueError()
            for name in ("id", "client_id", "delivery_id"):
                uuid.UUID(value[name])
            if not re.fullmatch(r"[a-f0-9]{64}", value["payload_digest"]):
                raise ValueError()
            if value["status"] == "smtp_accepted":
                if not isinstance(value.get("submitted_at"), str) or not value["submitted_at"]:
                    raise ValueError()
            elif value.get("submitted_at") is not None:
                raise ValueError()
            acceptance = value.get(
                "provider_acceptance",
                "ACCEPTED"
                if value["status"] == "smtp_accepted"
                else "UNKNOWN"
                if value["status"] in ("processing", "unknown")
                else "NOT_CONFIRMED",
            )
            if (
                acceptance not in {"ACCEPTED", "UNKNOWN", "NOT_CONFIRMED"}
                or (acceptance == "ACCEPTED") != (value["status"] == "smtp_accepted")
                or value["status"] in ("processing", "unknown")
                and acceptance != "UNKNOWN"
                or value.get("delivery_evidence", "NONE") != "NONE"
            ):
                raise ValueError()
            if value.get("attempt_id") is not None:
                if not isinstance(value["attempt_id"], str):
                    raise ValueError()
                uuid.UUID(value["attempt_id"])
            return cls(
                *(value[n] for n in names),
                value["status"],
                version,
                value.get("submitted_at"),
                acceptance,
                value.get("attempt_id"),
            )
        except (KeyError, TypeError, ValueError):
            raise ValueError("invalid_receipt") from None
