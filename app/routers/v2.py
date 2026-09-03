"""API v2 (ROADMAP 43.3): explicit resource version, cursor envelopes,
honoured Idempotency-Key, and Problem Details everywhere.

v1 keeps serving unchanged (≥12-month window once v2 goes GA); v2 differs
visibly:

- pagination lives in the response body as ``{"data": [...],
  "next_cursor": "..."}`` instead of headers;
- every response carries ``X-API-Version: 2.0``;
- writes honour an ``Idempotency-Key`` header — a replay returns the
  original resource with ``X-Idempotent-Replay: true`` instead of creating
  a duplicate;
- creating a conversation records its domain event in the SAME transaction
  (transactional outbox, 43.3).

Core fields of returned resources are identical to v1 — the shadow-read
contract test pins that so no semantic change can slip in silently.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from app.event_outbox import DomainEventOutbox
from app.db._util import utc_now
from app.main import require_permission
from app.pagination import (
    InvalidCursorError,
    decode_conversation_cursor,
    decode_message_cursor,
    encode_conversation_cursor,
    encode_message_cursor,
)
from app.routers.common import RouteDeps
from app.security import Principal

API_VERSION = "2.0"


def _v2_response(response: Response) -> None:
    response.headers["X-API-Version"] = API_VERSION


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter(prefix="/api/v2")
    database = deps.database
    outbox = DomainEventOutbox(database)

    def _conversation_out(row: dict[str, Any]) -> dict[str, Any]:
        # Shadow-read contract: these core fields are byte-identical to what
        # v1 serves for the same row (pinned by tests/test_api_v2.py).
        return {
            "id": row["id"],
            "status": row["status"],
            "priority": row["priority"],
            "channel": row["channel"],
            "customer_name": row["customer_name"],
            "customer_ref": row.get("customer_ref"),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @router.get(
        "/conversations",
        summary="List conversations (cursor-paginated)",
        description=(
            "Keyset-paginated queue listing. Cursors are opaque and live in "
            "the response body next_cursor field; core fields match v1 "
            "exactly (shadow-read contract)."
        ),
        tags=["v2:conversations"],
    )
    def list_conversations_v2(
        response: Response,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        sort: Annotated[str, Query()] = "updated",
        status_filter: Annotated[str | None, Query(alias="status")] = None,
    ) -> dict[str, Any]:
        _v2_response(response)
        decoded = None
        if cursor:
            try:
                decoded = decode_conversation_cursor(cursor)
            except InvalidCursorError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        try:
            rows = database.list_conversations(
                principal.tenant_id,
                status=status_filter,
                sort=sort if sort != "updated" else "updated",
                limit=limit + 1,
                cursor=decoded,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = None
        if has_more and visible:
            last = visible[-1]
            if sort == "priority":
                next_cursor = encode_conversation_cursor(
                    int(last["queue_priority_rank"]),
                    str(last["updated_at"]),
                    str(last["id"]),
                    sort="priority",
                )
            else:
                sort_key = {
                    "waiting": str(last.get("waiting_since") or ""),
                    "sla": str(last.get("sla_due_at") or "9999-12-31T00:00:00+00:00"),
                }.get(sort) or str(last["updated_at"])
                next_cursor = encode_conversation_cursor(
                    0,
                    str(last["updated_at"]),
                    str(last["id"]),
                    sort="updated" if sort not in {"waiting", "sla"} else sort,
                    sort_key=sort_key,
                )
        return {"data": [_conversation_out(row) for row in visible], "next_cursor": next_cursor}

    @router.get(
        "/conversations/{conversation_id}",
        summary="Fetch one conversation",
        description=(
            "Single conversation by id; archived conversations resolve "
            "transparently, mirroring v1 semantics."
        ),
        tags=["v2:conversations"],
    )
    def get_conversation_v2(
        response: Response,
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> dict[str, Any]:
        _v2_response(response)
        conversation = database.get_conversation(principal.tenant_id, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return _conversation_out(conversation)

    @router.get(
        "/conversations/{conversation_id}/messages",
        summary="List messages (keyset-paginated)",
        description=(
            "Stable keyset pagination over (created_at, seq) so equal-"
            "timestamp messages never skip or repeat."
        ),
        tags=["v2:conversations"],
    )
    def list_messages_v2(
        response: Response,
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        _v2_response(response)
        decoded = None
        if cursor:
            try:
                decoded = decode_message_cursor(cursor)
            except InvalidCursorError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = database.list_messages(
            principal.tenant_id, conversation_id, limit=limit + 1, cursor=decoded
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        next_cursor = (
            encode_message_cursor(str(visible[-1]["created_at"]), int(visible[-1]["seq"]))
            if has_more and visible
            else None
        )
        data = [
            {
                "id": row["id"],
                "role": row["role"],
                "author": row["author"],
                "content": row["content"],
                "created_at": row["created_at"],
            }
            for row in visible
        ]
        return {"data": data, "next_cursor": next_cursor}

    @router.post(
        "/conversations",
        status_code=201,
        summary="Create a conversation (Idempotency-Key honoured)",
        description=(
            "Creates a conversation and records its domain event in the same "
            "transaction (transactional outbox). Replaying the same "
            "Idempotency-Key returns the original resource with "
            "X-Idempotent-Replay: true."
        ),
        tags=["v2:conversations"],
    )
    def create_conversation_v2(
        response: Response,
        payload: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
        idempotency_key: Annotated[
            str | None,
            Header(alias="Idempotency-Key", max_length=128),
        ] = None,
    ) -> dict[str, Any]:
        _v2_response(response)
        customer_name = str(payload.get("customer_name") or "").strip()
        channel = str(payload.get("channel") or "web").strip()
        customer_ref = payload.get("customer_ref")
        if not customer_name or len(customer_name) > 120:
            raise HTTPException(status_code=422, detail="customer_name is required (<=120)")
        if len(channel) > 40:
            raise HTTPException(status_code=422, detail="channel too long")

        scope = "v2:create-conversation"
        if idempotency_key:
            with database.connect() as connection:
                existing = connection.execute(
                    """SELECT resource_id, status_code FROM api_idempotency
                    WHERE scope = ? AND tenant_id = ? AND idempotency_key = ?""",
                    (scope, principal.tenant_id, idempotency_key),
                ).fetchone()
            if existing is not None:
                stored = database.get_conversation(
                    principal.tenant_id, str(existing["resource_id"])
                )
                if stored is not None:
                    response.headers["X-Idempotent-Replay"] = "true"
                    response.status_code = int(existing["status_code"])
                    return _conversation_out(stored)

        # One transaction: business row, idempotency mapping, and the domain
        # event commit together — the transactional-outbox guarantee.

        with database.connect() as connection:
            conversation = database.create_conversation(
                principal.tenant_id,
                customer_name,
                str(customer_ref) if customer_ref else None,
                channel,
                principal.actor_id,
                deps.settings.normal_sla_minutes,
                connection=connection,
            )
            conversation_id = str(conversation["id"])
            if idempotency_key:
                connection.execute(
                    """INSERT INTO api_idempotency
                    (scope, tenant_id, idempotency_key, resource_id, status_code, created_at)
                    VALUES (?, ?, ?, ?, 201, ?)""",
                    (scope, principal.tenant_id, idempotency_key, conversation_id, utc_now()),
                )
            outbox.record(
                principal.tenant_id,
                "helix.conversation.created",
                {
                    "conversation_id": conversation_id,
                    "channel": channel,
                    "customer_name": customer_name,
                    "customer_verified": bool(customer_ref),
                    "source_api": "v2",
                },
                connection=connection,
            )

        # Post-steps run after the outbox transaction commits — they open
        # their own connections and would deadlock inside it.
        database.increment_tenant_usage_conversations(principal.tenant_id, utc_now()[:10])
        database.audit(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            "conversation.created",
            {"channel": channel, "customer_verified": bool(customer_ref), "source_api": "v2"},
        )
        return _conversation_out(
            database.get_conversation(principal.tenant_id, conversation_id) or {}
        )

    return router
