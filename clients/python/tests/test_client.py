"""Tests for the helix-client Python SDK (Phase 25.4).

Uses httpx's MockTransport so no server is required. The mock verifies
request shaping (headers, path, idempotency keys) and returns canned
Problem Details bodies for error mapping.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from helix_client import (
    HelixClient,
    HelixConflictError,
    HelixNotFoundError,
    HelixRateLimitError,
    HelixValidationError,
    verify_webhook_signature,
)


def _handler(status: int, body: Any, *, call_log: list | None = None) -> httpx.MockTransport:
    def handle(request: httpx.Request) -> httpx.Response:
        if call_log is not None:
            call_log.append(request)
        return httpx.Response(status, json=body, headers={"Retry-After": "2"})

    return httpx.MockTransport(handle)


@pytest.fixture()
def call_log() -> list:
    return []


def _client(transport: httpx.MockTransport) -> HelixClient:
    return HelixClient(
        base_url="https://support.example.com",
        api_key="test-key-12345678",
        tenant_id="demo",
        transport=transport,
    )


def test_create_conversation_sends_auth_and_tenant_headers() -> None:
    call_log: list = []
    client = _client(_handler(201, {"id": "conv-1", "status": "open"}, call_log=call_log))
    with client:
        body = client.create_conversation("Ada", customer_ref="CUST-1")
    assert body["id"] == "conv-1"
    request = call_log[0]
    assert request.headers["X-API-Key"] == "test-key-12345678"
    assert request.headers["X-Tenant-Id"] == "demo"
    assert request.url.path == "/api/conversations"
    assert json.loads(request.content)["customer_name"] == "Ada"


def test_send_message_sends_idempotency_key() -> None:
    call_log: list = []
    client = _client(_handler(200, {"assistant_message": {"id": "msg-1"}}, call_log=call_log))
    with client:
        client.send_message("conv-1", "hi", idempotency_key="idem-abc")
    request = call_log[0]
    assert request.headers["Idempotency-Key"] == "idem-abc"


def test_not_found_raises_typed_error() -> None:
    body = {
        "type": "urn:helix:error:not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "Conversation not found",
        "instance": "/api/conversations/conv-nope",
        "request_id": "req_123",
        "code": "not_found",
    }
    client = _client(_handler(404, body))
    with client, pytest.raises(HelixNotFoundError) as exc_info:
        client.get_conversation("conv-nope")
    assert exc_info.value.code == "not_found"
    assert exc_info.value.status == 404
    assert exc_info.value.request_id == "req_123"


def test_conflict_raises_typed_error() -> None:
    body = {"detail": "shortcut exists", "code": "conflict", "status": 409}
    client = _client(_handler(409, body))
    with client, pytest.raises(HelixConflictError):
        client.create_knowledge_draft(
            title="T", content="body", tags=["x"], source_url="https://e.com"
        )


def test_validation_422_raises_typed_error() -> None:
    body = {
        "detail": "Request validation failed",
        "code": "validation_error",
        "status": 422,
        "errors": [{"type": "string_too_short", "loc": ["body", "customer_name"]}],
    }
    client = _client(_handler(422, body))
    with client, pytest.raises(HelixValidationError) as exc_info:
        client.create_conversation("")
    assert exc_info.value.body["errors"][0]["loc"] == ["body", "customer_name"]


def test_rate_limit_429_carries_retry_after() -> None:
    body = {"detail": "rate limited", "code": "rate_limited", "status": 429}
    client = _client(_handler(429, body))
    with client, pytest.raises(HelixRateLimitError) as exc_info:
        client.list_conversations()
    assert exc_info.value.retry_after == 2


def test_transient_5xx_is_retried() -> None:
    call_log: list = []

    def flaky(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        if len(call_log) == 1:
            return httpx.Response(503, json={"detail": "boom", "code": "http_error"})
        return httpx.Response(200, json=[])

    client = HelixClient(
        base_url="https://support.example.com",
        api_key="test-key-12345678",
        transport=httpx.MockTransport(flaky),
    )
    with client:
        result = client.list_conversations()
    assert result == []
    assert len(call_log) == 2


def test_stream_turn_job_parses_sse_events() -> None:
    sse_body = (
        'event: snapshot\ndata: {"status": "processing"}\n\n'
        'event: snapshot\ndata: {"status": "completed", "result": {"assistant_message": {"content": "ok"}}}\n\n'
    )

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=sse_body, headers={"Content-Type": "text/event-stream"})

    client = _client(httpx.MockTransport(handle))
    with client:
        events = list(client.stream_turn_job("job-1"))
    assert events[0]["event"] == "snapshot"
    assert events[0]["data"]["status"] == "processing"
    assert events[1]["data"]["status"] == "completed"
    assert events[1]["data"]["result"]["assistant_message"]["content"] == "ok"


def test_verify_webhook_signature_round_trip() -> None:
    import hashlib
    import hmac

    secret = "endpoint-secret-123"
    timestamp = "1784000000"
    body = json.dumps({"event_type": "conversation.created", "event_id": "evt-1"}).encode()
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert verify_webhook_signature(
        secret=secret, timestamp=timestamp, body=body, signature=expected
    )
    assert not verify_webhook_signature(
        secret=secret, timestamp=timestamp, body=body, signature="wrong"
    )
