"""Helix Support API client (Phase 25.4; v2 support Phase 43.3).

A thin, typed client over the Helix HTTP API covering conversations,
messages, turn jobs (including SSE streaming), feedback, knowledge, and
webhook signature verification. Depends only on ``httpx``.

Usage::

    from helix_client import HelixClient

    client = HelixClient(base_url="https://support.example.com", api_key="...")
    conv = client.create_conversation(customer_name="Ada")
    turn = client.send_message(conv["id"], "我的订单到哪了？", idempotency_key="k-1")

API v2 (Phase 43.3): ``*_v2`` methods speak the cursor-envelope contract —
pagination in the response body as ``{"data": [...], "next_cursor": ...}``,
every response carries ``X-API-Version``, and writes honour an
``Idempotency-Key`` whose replay returns the original resource flagged by
``X-Idempotent-Replay: true``::

    page = client.list_conversations_v2(limit=50)
    for conv in client.iter_conversations_v2(limit=50):  # auto-paginates
        ...
    replay = client.create_conversation_v2("Ada", idempotency_key="k-2")

Error handling: HTTP errors raise :class:`HelixError` (or a subclass) whose
``code``/``status`` mirror the RFC 9457 Problem Details body the server
returns.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Self

import httpx

__all__ = [
    "ConversationPage",
    "HelixAuthenticationError",
    "HelixClient",
    "HelixConflictError",
    "HelixError",
    "HelixNotFoundError",
    "HelixPermissionError",
    "HelixRateLimitError",
    "HelixValidationError",
    "verify_webhook_signature",
]

# Safety bound for iter_conversations_v2: a server that never clears
# next_cursor must not loop forever inside the SDK.
_MAX_V2_PAGES = 10_000


class HelixError(RuntimeError):
    """A server error carrying the RFC 9457 Problem Details fields."""

    def __init__(self, message: str, *, status: int, code: str, body: dict[str, Any]) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body
        self.request_id = body.get("request_id")
        self.instance = body.get("instance")


class HelixAuthenticationError(HelixError):
    pass


class HelixPermissionError(HelixError):
    pass


class HelixNotFoundError(HelixError):
    pass


class HelixConflictError(HelixError):
    pass


class HelixRateLimitError(HelixError):
    def __init__(
        self, message: str, *, status: int, code: str, body: dict[str, Any], retry_after: int | None
    ) -> None:
        super().__init__(message, status=status, code=code, body=body)
        self.retry_after = retry_after


class HelixValidationError(HelixError):
    pass


_STATUS_ERROR_CLASSES: dict[int, type[HelixError]] = {
    401: HelixAuthenticationError,
    403: HelixPermissionError,
    404: HelixNotFoundError,
    409: HelixConflictError,
    429: HelixRateLimitError,
    422: HelixValidationError,
}


@dataclass(frozen=True)
class ConversationPage:
    """One page of the v2 cursor envelope (43.3).

    Attributes:
        data: Conversation resources; core fields are byte-identical to v1.
        next_cursor: Opaque keyset cursor for the next page, or ``None``
            when this is the last page.
        api_version: The server's ``X-API-Version`` response header.
    """

    data: list[dict[str, Any]]
    next_cursor: str | None
    api_version: str | None = None


def _raise_for_response(response: httpx.Response) -> None:
    if response.is_success:
        return
    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text, "code": "http_error", "status": response.status_code}
    message = str(body.get("detail") or body.get("title") or f"HTTP {response.status_code}")
    error_class = _STATUS_ERROR_CLASSES.get(response.status_code, HelixError)
    if error_class is HelixRateLimitError:
        retry_after = response.headers.get("Retry-After")
        raise HelixRateLimitError(
            message,
            status=response.status_code,
            code=str(body.get("code", "rate_limited")),
            body=body,
            retry_after=int(retry_after) if retry_after and retry_after.isdigit() else None,
        )
    raise error_class(
        message,
        status=response.status_code,
        code=str(body.get("code", "http_error")),
        body=body,
    )


class HelixClient:
    """Thin client over the Helix Support HTTP API."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        tenant_id: str | None = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.tenant_id = tenant_id
        headers = {"X-API-Key": api_key}
        if tenant_id:
            headers["X-Tenant-Id"] = tenant_id
        self._client = httpx.Client(
            base_url=self.base_url,
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max(0, max_retries)

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ------------------------------------------------------------------ core

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        idempotency_key: str | None = None,
    ) -> Any:
        request_headers = dict(headers or {})
        if idempotency_key:
            request_headers["Idempotency-Key"] = idempotency_key
        attempts = 0
        while True:
            response = self._client.request(
                method,
                path,
                json=json_body,
                params=params,
                headers=request_headers,
            )
            if response.status_code < 500 or attempts >= self._max_retries:
                _raise_for_response(response)
                return response.json()
            attempts += 1

    def _request_raw(
        self,
        method: str,
        path: str,
        **kwargs: Any,
    ) -> tuple[Any, httpx.Response]:
        """Like :meth:`_request` but also returns the final response object
        so callers can inspect version/replay headers (43.3)."""
        attempts = 0
        while True:
            response = self._client.request(method, path, **kwargs)
            if response.status_code < 500 or attempts >= self._max_retries:
                _raise_for_response(response)
                return response.json(), response
            attempts += 1

    # ------------------------------------------------------------ conversations

    def list_conversations(self, **params: Any) -> list[dict[str, Any]]:
        """List conversations; pass queue filters as keyword args."""
        return self._request("GET", "/api/conversations", params=params)

    def create_conversation(
        self,
        customer_name: str,
        *,
        customer_ref: str | None = None,
        channel: str = "web",
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"customer_name": customer_name, "channel": channel}
        if customer_ref:
            body["customer_ref"] = customer_ref
        return self._request("POST", "/api/conversations", json_body=body)

    def get_conversation(self, conversation_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/conversations/{conversation_id}")

    def send_message(
        self,
        conversation_id: str,
        content: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Send a customer turn; returns the assistant reply."""
        return self._request(
            "POST",
            f"/api/conversations/{conversation_id}/messages",
            json_body={"content": content},
            idempotency_key=idempotency_key,
        )

    # ------------------------------------------------- conversations v2 (43.3)

    def list_conversations_v2(
        self,
        *,
        cursor: str | None = None,
        limit: int = 50,
        sort: str = "updated",
        status: str | None = None,
    ) -> ConversationPage:
        """Fetch one page of the v2 cursor envelope.

        Returns a :class:`ConversationPage` whose ``next_cursor`` feeds the
        next call; core fields of each item match v1 exactly (shadow-read
        contract). Prefer :meth:`iter_conversations_v2` for full scans.
        """
        params: dict[str, Any] = {"limit": limit, "sort": sort}
        if cursor:
            params["cursor"] = cursor
        if status:
            params["status"] = status
        body, response = self._request_raw("GET", "/api/v2/conversations", params=params)
        return ConversationPage(
            data=list(body.get("data") or []),
            next_cursor=body.get("next_cursor"),
            api_version=response.headers.get("X-API-Version"),
        )

    def iter_conversations_v2(
        self,
        *,
        limit: int = 50,
        sort: str = "updated",
        status: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Iterate every conversation via automatic cursor pagination.

        Yields items from successive pages until ``next_cursor`` is ``None``.
        """
        cursor: str | None = None
        seen_pages = 0
        while True:
            page = self.list_conversations_v2(cursor=cursor, limit=limit, sort=sort, status=status)
            yield from page.data
            if not page.next_cursor:
                return
            cursor = page.next_cursor
            seen_pages += 1
            if seen_pages > _MAX_V2_PAGES:
                raise HelixError(
                    "cursor pagination did not terminate",
                    status=0,
                    code="sdk_pagination_loop",
                    body={},
                )

    def get_conversation_v2(self, conversation_id: str) -> dict[str, Any]:
        """Fetch one conversation under the v2 contract (shadow-read fields)."""
        return self._request("GET", f"/api/v2/conversations/{conversation_id}")

    def list_messages_v2(
        self,
        conversation_id: str,
        *,
        cursor: str | None = None,
        limit: int = 100,
    ) -> ConversationPage:
        """List a conversation's messages (keyset over created_at + seq)."""
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        body, response = self._request_raw(
            "GET", f"/api/v2/conversations/{conversation_id}/messages", params=params
        )
        return ConversationPage(
            data=list(body.get("data") or []),
            next_cursor=body.get("next_cursor"),
            api_version=response.headers.get("X-API-Version"),
        )

    def create_conversation_v2(
        self,
        customer_name: str,
        *,
        customer_ref: str | None = None,
        channel: str = "web",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a conversation under the v2 contract.

        Pass ``idempotency_key`` to make retries side-effect free: a replay
        returns the original resource and the response carries
        ``X-Idempotent-Replay: true``. The returned dict gains an
        ``_idempotent_replay`` key reflecting that header. Server-side, the
        domain event commits in the same transaction as the business row
        (transactional outbox).
        """
        body: dict[str, Any] = {"customer_name": customer_name, "channel": channel}
        if customer_ref:
            body["customer_ref"] = customer_ref
        result, response = self._request_raw(
            "POST",
            "/api/v2/conversations",
            json=body,
            headers={"Idempotency-Key": idempotency_key} if idempotency_key else None,
        )
        replayed = response.headers.get("X-Idempotent-Replay")
        if isinstance(result, dict):
            result["_idempotent_replay"] = replayed is not None and replayed.lower() == "true"
        return result

    # ----------------------------------------------------------------- turn jobs

    def create_turn_job(
        self,
        conversation_id: str,
        content: str,
        *,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/conversations/{conversation_id}/turn-jobs",
            json_body={"content": content},
            idempotency_key=idempotency_key,
        )

    def get_turn_job(self, job_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/turn-jobs/{job_id}")

    def stream_turn_job(self, job_id: str) -> Iterator[dict[str, Any]]:
        """Stream SSE events for a turn job.

        Yields parsed ``{"event": ..., "data": ...}`` dicts until the stream
        closes. The ``timeout`` event indicates the server closed a
        long-poll; reconnect by calling this again.
        """
        with self._client.stream(
            "GET", f"/api/turn-jobs/{job_id}/events", headers={"Accept": "text/event-stream"}
        ) as response:
            _raise_for_response(response)
            for line in response.iter_lines():
                if line.startswith("event:"):
                    event = line[len("event:") :].strip()
                    continue
                if line.startswith("data:"):
                    data = line[len("data:") :].strip()
                    yield {"event": event, "data": json.loads(data) if data else None}

    # --------------------------------------------------------------- feedback

    def submit_feedback(
        self,
        conversation_id: str,
        message_id: str,
        rating: int,
        *,
        reason: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"message_id": message_id, "rating": rating}
        if reason:
            body["reason"] = reason
        return self._request(
            "POST", f"/api/conversations/{conversation_id}/feedback", json_body=body
        )

    # --------------------------------------------------------------- knowledge

    def list_knowledge(self) -> list[dict[str, Any]]:
        return self._request("GET", "/api/knowledge")

    def create_knowledge_draft(
        self,
        *,
        title: str,
        content: str,
        tags: list[str],
        category: str = "general",
        source_url: str,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/knowledge/drafts",
            json_body={
                "title": title,
                "content": content,
                "tags": tags,
                "category": category,
                "source_url": source_url,
            },
        )

    # -------------------------------------------------- admin: tenants & members

    def provision_tenant(
        self,
        tenant_id: str,
        name: str,
        *,
        conversation_quota: int | None = None,
        daily_turn_budget: int | None = None,
        allowed_models: list[str] | None = None,
        storage_quota_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Idempotently provision a tenant (Phase 22.1)."""
        body: dict[str, Any] = {"tenant_id": tenant_id, "name": name}
        if conversation_quota is not None:
            body["conversation_quota"] = conversation_quota
        if daily_turn_budget is not None:
            body["daily_turn_budget"] = daily_turn_budget
        if allowed_models is not None:
            body["allowed_models"] = allowed_models
        if storage_quota_bytes is not None:
            body["storage_quota_bytes"] = storage_quota_bytes
        return self._request("POST", "/api/admin/tenants", json_body=body)

    def get_tenant_quota(self, tenant_id: str) -> dict[str, Any]:
        return self._request("GET", f"/api/admin/tenants/{tenant_id}/quota")

    def set_tenant_quota(
        self,
        tenant_id: str,
        *,
        conversation_quota: int | None = None,
        storage_quota_bytes: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {}
        if conversation_quota is not None:
            body["conversation_quota"] = conversation_quota
        if storage_quota_bytes is not None:
            body["storage_quota_bytes"] = storage_quota_bytes
        return self._request("PUT", f"/api/admin/tenants/{tenant_id}/quota", json_body=body)

    def invite_member(self, tenant_id: str, actor_id: str, role: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/admin/tenants/{tenant_id}/members",
            json_body={"actor_id": actor_id, "role": role},
        )

    def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        return self._request("GET", f"/api/admin/tenants/{tenant_id}/members")

    def update_member_role(self, tenant_id: str, actor_id: str, role: str) -> dict[str, Any]:
        return self._request(
            "PATCH",
            f"/api/admin/tenants/{tenant_id}/members/{actor_id}",
            json_body={"role": role},
        )

    def deactivate_member(self, tenant_id: str, actor_id: str) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/admin/tenants/{tenant_id}/members/{actor_id}/deactivate",
        )

    def export_tenant_usage(
        self,
        *,
        tenant_id: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Raw daily usage rows for billing (Phase 22.4)."""
        params: dict[str, Any] = {"limit": limit}
        if tenant_id:
            params["tenant_id"] = tenant_id
        if since:
            params["since"] = since
        if until:
            params["until"] = until
        return self._request("GET", "/api/admin/usage", params=params)

    # ------------------------------------------- widget chat (Phase 23)

    def widget_create_session(
        self,
        *,
        widget_token: str,
        customer_name: str | None = None,
        channel: str = "web_chat",
    ) -> dict[str, Any]:
        """Open a widget chat session with a signed customer token (23.1).

        The response contains a short-lived ``widget_token`` bound to the
        returned conversation. Use that fresh token for subsequent message,
        history, and stream calls; the bootstrap token is create-session-only.
        """
        body: dict[str, Any] = {"channel": channel}
        if customer_name:
            body["customer_name"] = customer_name
        return self._request(
            "POST",
            "/api/widget/sessions",
            json_body=body,
            headers={"X-Widget-Token": widget_token},
        )

    def widget_send_message(
        self,
        *,
        widget_token: str,
        conversation_id: str,
        content: str,
        channel_message_id: str | None = None,
        async_mode: bool = False,
    ) -> dict[str, Any]:
        """Send a widget message; channel_message_id replays are idempotent."""
        body: dict[str, Any] = {"content": content}
        if channel_message_id:
            body["channel_message_id"] = channel_message_id
        params = {"async_mode": "true"} if async_mode else None
        return self._request(
            "POST",
            f"/api/widget/sessions/{conversation_id}/messages",
            json_body=body,
            params=params,
            headers={"X-Widget-Token": widget_token},
        )

    # ------------------------------------------------------------------ me

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/me")


def verify_webhook_signature(
    *,
    secret: str,
    timestamp: str,
    body: bytes,
    signature: str,
) -> bool:
    """Verify an outbound webhook delivery (Phase 20.5/25.4).

    The server signs ``f"{timestamp}." + body`` with HMAC-SHA256 using the
    endpoint secret. Consumers should also reject timestamps older than a
    small window to prevent replay.
    """
    expected = hmac.new(
        secret.encode("utf-8"), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature)
