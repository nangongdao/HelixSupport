"""Unit tests for the helix-client v2 surface (Phase 43.3).

Uses httpx's MockTransport to pin the v2 contract from the client side:
cursor-envelope parsing, automatic pagination, Idempotency-Key replay
detection, and the ``X-API-Version`` disclosure.
"""

from __future__ import annotations

import json

import httpx
import pytest
from helix_client import (
    ConversationPage,
    HelixClient,
    HelixNotFoundError,
)


def _client(transport: httpx.MockTransport) -> HelixClient:
    return HelixClient(
        base_url="https://support.example.com",
        api_key="test-key-12345678",
        tenant_id="demo",
        transport=transport,
    )


def _envelope(
    data: list[dict], next_cursor: str | None = None, api_version: str = "2.0"
) -> httpx.Response:
    headers = {"X-API-Version": api_version} if api_version else {}
    return httpx.Response(200, json={"data": data, "next_cursor": next_cursor}, headers=headers)


def test_list_conversations_v2_parses_envelope_and_version_header() -> None:
    call_log: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        return _envelope([{"id": "conv-1", "status": "open"}], next_cursor="CUR-2")

    with _client(httpx.MockTransport(handle)) as client:
        page = client.list_conversations_v2(limit=10)
    assert isinstance(page, ConversationPage)
    assert [item["id"] for item in page.data] == ["conv-1"]
    assert page.next_cursor == "CUR-2"
    assert page.api_version == "2.0"
    request = call_log[0]
    assert request.url.path == "/api/v2/conversations"
    assert request.headers["X-API-Key"] == "test-key-12345678"
    assert request.headers["X-Tenant-Id"] == "demo"
    assert request.url.params["limit"] == "10"


def test_list_conversations_v2_passes_cursor_and_filters() -> None:
    call_log: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        return _envelope([])

    with _client(httpx.MockTransport(handle)) as client:
        client.list_conversations_v2(cursor="CUR-1", sort="priority", status="open")
    params = call_log[0].url.params
    assert params["cursor"] == "CUR-1"
    assert params["sort"] == "priority"
    assert params["status"] == "open"


def test_iter_conversations_v2_walks_all_pages_without_duplicates() -> None:
    pages: dict[str | None, list[dict]] = {
        None: [{"id": f"conv-{i}"} for i in range(3)],
        "CUR-1": [{"id": f"conv-{i}"} for i in range(3, 5)],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        if cursor is None:
            return _envelope(pages[None], next_cursor="CUR-1")
        return _envelope(pages[cursor])

    with _client(httpx.MockTransport(handle)) as client:
        seen = [item["id"] for item in client.iter_conversations_v2(limit=3)]
    assert seen == ["conv-0", "conv-1", "conv-2", "conv-3", "conv-4"]


def test_iter_conversations_v2_stops_when_next_cursor_missing() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        body = {"data": [{"id": "only"}]}
        # Server omits next_cursor entirely — the iterator must still stop.
        return httpx.Response(200, json=body, headers={"X-API-Version": "2.0"})

    with _client(httpx.MockTransport(handle)) as client:
        seen = list(client.iter_conversations_v2())
    assert seen == [{"id": "only"}]


def test_iter_conversations_v2_bails_out_on_non_terminating_cursor() -> None:
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return _envelope([{"id": str(calls)}], next_cursor=f"CUR-{calls}")

    with _client(httpx.MockTransport(handle)) as client:
        iterator = client.iter_conversations_v2(limit=1)
        first = next(iterator)
        assert first == {"id": "1"}
        # Exhausting the generator surfaces the loop guard, not a hang.
        with pytest.raises(Exception, match="did not terminate"):
            list(iterator)


def test_create_conversation_v2_sends_idempotency_key() -> None:
    call_log: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        return httpx.Response(
            201,
            json={"id": "conv-new", "status": "open"},
            headers={"X-API-Version": "2.0"},
        )

    with _client(httpx.MockTransport(handle)) as client:
        result = client.create_conversation_v2("Ada", idempotency_key="idem-v2-1")
    assert result["_idempotent_replay"] is False
    request = call_log[0]
    assert request.url.path == "/api/v2/conversations"
    assert request.headers["Idempotency-Key"] == "idem-v2-1"
    assert json.loads(request.content)["customer_name"] == "Ada"


def test_create_conversation_v2_detects_replay_flag() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={"id": "conv-original", "status": "open"},
            headers={"X-API-Version": "2.0", "X-Idempotent-Replay": "true"},
        )

    with _client(httpx.MockTransport(handle)) as client:
        result = client.create_conversation_v2("Ada", idempotency_key="idem-v2-2")
    assert result["_idempotent_replay"] is True


def test_v2_errors_map_to_problem_details_subclasses() -> None:
    body = {
        "type": "urn:helix:error:not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "Conversation not found",
        "request_id": "req_v2",
        "code": "not_found",
    }

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json=body, headers={"X-API-Version": "2.0"})

    with (
        _client(httpx.MockTransport(handle)) as client,
        pytest.raises(HelixNotFoundError) as exc_info,
    ):
        client.get_conversation_v2("conv-nope")
    assert exc_info.value.request_id == "req_v2"


def test_list_messages_v2_returns_page() -> None:
    call_log: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        call_log.append(request)
        return _envelope([{"id": "msg-1", "role": "customer"}], next_cursor=None)

    with _client(httpx.MockTransport(handle)) as client:
        page = client.list_messages_v2("conv-1", limit=25)
    assert page.data[0]["id"] == "msg-1"
    assert page.next_cursor is None
    assert call_log[0].url.path == "/api/v2/conversations/conv-1/messages"
