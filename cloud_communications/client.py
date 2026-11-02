"""Dependency-free IMS client. Keep the same idempotency key across every HTTP retry."""

import hashlib
import hmac
import json
import secrets
import ssl
import time
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class APIError(Exception):
    def __init__(self, code, *, status=None, retryable=False):
        super().__init__(code)
        self.code, self.status, self.retryable = code, status, retryable


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward service credentials to another destination.


class CommunicationsClient:
    def __init__(self, base_url, token, *, timeout=15, opener=None):
        origin = urlsplit(base_url)
        if (
            origin.scheme != "https"
            or not origin.hostname
            or origin.username
            or origin.password
            or origin.path not in {"", "/"}
            or origin.query
            or origin.fragment
            or origin.port not in (None, 443)
        ):
            raise ValueError("Use the verified Business Cloud HTTPS origin.")
        if not token or len(token) > 200:
            raise ValueError("A service credential is required.")
        self.base_url, self.token, self.timeout = base_url.rstrip("/"), token, timeout
        self.opener = opener or build_opener(
            NoRedirect(), HTTPSHandler(context=ssl.create_default_context())
        )

    def request(self, method, path, payload=None, *, key=None):
        body = (
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
            if payload is not None
            else b""
        )
        timestamp, nonce = str(int(time.time())), secrets.token_urlsafe(24)
        canonical = "\n".join((timestamp, nonce, method, path, hashlib.sha256(body).hexdigest()))
        signature = hmac.new(self.token.encode(), canonical.encode(), hashlib.sha256).hexdigest()
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-Boforg-Timestamp": timestamp,
            "X-Boforg-Nonce": nonce,
            "X-Boforg-Signature": signature,
            "User-Agent": "Boforg-IMS-Communications/1.0",
        }
        if key:
            headers["Idempotency-Key"] = key
        request = Request(
            self.base_url + path,
            data=body if payload is not None else None,
            method=method,
            headers=headers,
        )
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1024 * 1024 + 1)
                if len(raw) > 1024 * 1024:
                    raise APIError("response_too_large", retryable=True)
                result = json.loads(raw)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except HTTPError as error:
            raise APIError(
                "http_error",
                status=error.code,
                retryable=error.code in {408, 425, 429} or error.code >= 500,
            ) from None
        except (URLError, OSError, ValueError):
            # The service may already have committed. Reuse the original key when retrying.
            raise APIError("service_unavailable", retryable=True) from None

    def submit(self, payload, *, idempotency_key):
        return self.request("POST", "/api/v1/communications/messages", payload, key=idempotency_key)

    def submit_event(self, payload, *, idempotency_key):
        return self.request("POST", "/api/v1/communications/events", payload, key=idempotency_key)

    def status(self, message_id):
        return self.request("GET", f"/api/v1/communications/messages/{uuid.UUID(str(message_id))}")

    def history(self, *, external_reference=None, updated_after=None, page=1):
        query = {"page": page}
        if external_reference:
            query["external_reference"] = external_reference
        if updated_after:
            query["updated_after"] = updated_after
        return self.request("GET", "/api/v1/communications/history?" + urlencode(query))

    def capabilities(self):
        return self.request("GET", "/api/v1/communications/health")

    def cancel(self, message_id, *, reason):
        return self.request(
            "POST",
            f"/api/v1/communications/messages/{uuid.UUID(str(message_id))}/cancel",
            {"reason": reason},
        )

    def retry_reviewed(self, message_id, *, reason, idempotency_key):
        return self.request(
            "POST",
            f"/api/v1/communications/messages/{uuid.UUID(str(message_id))}/retry",
            {"reason": reason, "acknowledge_duplicate_risk": True},
            key=idempotency_key,
        )


def verify_callback(secret, timestamp, signature, body, *, now=None):
    try:
        if (
            len(secret) < 32
            or abs((now if now is not None else time.time()) - int(timestamp)) > 300
        ):
            raise ValueError()
        expected = hmac.new(
            secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ValueError()
        value = json.loads(body)
        uuid.UUID(value["event_id"])
        uuid.UUID(value["message_id"])
        if value["event_type"] != "communication.status" or not isinstance(
            value["updated_at"], str
        ):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, AttributeError):
        raise APIError("invalid_callback") from None
