"""Typed receipts over the established, injected/mockable HTTP client."""

from typing import Protocol

from tasker.email_protocol import SubmissionReceipt


class HTTPClient(Protocol):
    def request(self, method: str, path: str, payload=None, *, key=None) -> dict: ...
    def submit(self, payload: dict, *, idempotency_key: str) -> dict: ...


class ReceiptPayload(dict):
    """Keep original signed facts for the inbox; expose a validated typed receipt."""

    def __init__(self, value: dict):
        self.receipt = SubmissionReceipt.parse(value)
        super().__init__(value)


class CloudEmailAdapter:
    def __init__(self, http: HTTPClient):
        self.http = http

    def submit(self, envelope: dict, *, idempotency_key: str) -> ReceiptPayload:
        return ReceiptPayload(self.http.submit(envelope, idempotency_key=idempotency_key))

    def request(self, method: str, path: str, payload=None, *, key=None) -> ReceiptPayload:
        return ReceiptPayload(self.http.request(method, path, payload, key=key))

    def lookup(self, *, idempotency_key: str) -> ReceiptPayload:
        from urllib.parse import urlencode

        return self.request(
            "GET", "/api/v1/communications/messages/lookup?" + urlencode({"key": idempotency_key})
        )

    def cancel(self, message_id: str, *, expected_version: int, reason: str) -> ReceiptPayload:
        return self.request(
            "POST",
            f"/api/v1/communications/messages/{message_id}/cancel",
            {"reason": reason, "expected_state_version": expected_version},
        )
