"""Embeddable Web Chat widget endpoints (Phase 23).

Public endpoints that authenticate via a signed ``X-Widget-Token`` instead of
an API key, so a customer's browser can open a session and chat without
credentials:

- ``POST /api/widget/sessions`` — create a conversation bound to the token's
  tenant (anonymous or named customer), return the conversation plus a fresh
  per-session token.
- ``POST /api/widget/sessions/{conversation_id}/messages`` — send a customer
  message; ``channel_message_id`` dedup means replaying the same channel
  message never creates a second turn (Phase 23.2).
- ``GET /api/widget/sessions/{conversation_id}/messages`` — list messages.
- ``GET /api/widget/sessions/{conversation_id}/stream`` — SSE event stream of
  the latest turn job for the conversation.

The router is mounted on the app so it inherits the request-controls
middleware (CSP, security headers) automatically.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, cast
from time import perf_counter

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from typing_extensions import Annotated

from app.config import Settings
from app.context import bind_tenant_scope
from app.database import Database
from app.main import AppServices, message_out
from app.intake import backpressure_reason
from app.orchestrator import ConversationOrchestrator
from app.schemas import (
    ConversationOut,
    MessageOut,
    TurnResponse,
    WidgetMessageSendRequest,
    WidgetSessionCreateRequest,
    WidgetSessionOut,
)
from app.widget_token import WidgetToken, WidgetTokenError, sign_token, verify_token

router = APIRouter(prefix="/api/widget", tags=["widget"])


def _services(request: Request) -> AppServices:
    services: AppServices | None = getattr(request.app.state, "services", None)
    if services is None:
        raise HTTPException(status_code=500, detail="services not initialized")
    return services


def _verify_widget_token(
    request: Request,
    x_widget_token: Annotated[str | None, Header(alias="X-Widget-Token")] = None,
) -> WidgetToken:
    """Dependency: verify the signed widget token and return its payload."""
    services = _services(request)
    settings: Settings = services.settings
    if not x_widget_token:
        raise HTTPException(status_code=401, detail="Missing X-Widget-Token header")
    try:
        return verify_token(secret=settings.widget_secret, token=x_widget_token)
    except WidgetTokenError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _ensure_tenant(token: WidgetToken, database: Database) -> None:
    """404 when the token's tenant is not provisioned (never leak existence)."""
    if not database.tenant_exists(token.tenant_id):
        raise HTTPException(status_code=404, detail="Tenant not found")
    # Phase 43.2 contract (a): the verified widget token is the customer-side
    # identity — bind its tenant as the ambient RLS scope for this endpoint.
    bind_tenant_scope(token.tenant_id)


def _fresh_token(
    settings: Settings,
    tenant_id: str,
    conversation_id: str,
    customer_ref: str | None = None,
) -> str:
    return sign_token(
        secret=settings.widget_secret,
        tenant_id=tenant_id,
        customer_ref=customer_ref,
        conversation_id=conversation_id,
        ttl_seconds=3600,
    )


def _ensure_session_token(token: WidgetToken, conversation_id: str) -> None:
    """Require the fresh token issued for this exact widget session."""
    if token.conversation_id != conversation_id:
        # Keep the response indistinguishable from an unknown conversation.
        raise HTTPException(status_code=404, detail="Conversation not found")


@router.post("/sessions", response_model=WidgetSessionOut, status_code=201)
def create_widget_session(
    request: Request,
    payload: WidgetSessionCreateRequest,
    token: Annotated[WidgetToken, Depends(_verify_widget_token)],
) -> WidgetSessionOut:
    """Open a widget chat session bound to the token's tenant."""
    services = _services(request)
    database: Database = services.database
    _ensure_tenant(token, database)
    customer_name = payload.customer_name or "Widget Visitor"
    conversation = database.create_conversation(
        token.tenant_id,
        customer_name,
        token.customer_ref,
        payload.channel,
        actor="widget",
        sla_minutes=services.settings.normal_sla_minutes,
    )
    session_token = _fresh_token(
        services.settings,
        token.tenant_id,
        str(conversation["id"]),
        token.customer_ref,
    )
    return WidgetSessionOut(
        conversation=ConversationOut(**conversation),
        widget_token=session_token,
    )


@router.post(
    "/sessions/{conversation_id}/messages",
)
def send_widget_message(
    request: Request,
    conversation_id: str,
    payload: WidgetMessageSendRequest,
    token: Annotated[WidgetToken, Depends(_verify_widget_token)],
    async_mode: Annotated[bool, Query()] = False,
) -> dict[str, Any]:
    """Send a customer message; channel_message_id replays are idempotent.

    Default (sync) returns the completed ``TurnResponse``. With
    ``async_mode=true`` the message is enqueued as a turn job and the
    response is ``{"job_id": ..., "status": "queued"}``; the client then
    streams progressive output from ``GET /stream``. Either way, replaying
    the same ``channel_message_id`` never creates a second turn.
    """
    services = _services(request)
    database: Database = services.database
    orchestrator: ConversationOrchestrator = services.orchestrator
    _ensure_tenant(token, database)
    _ensure_session_token(token, conversation_id)
    conversation = database.get_conversation(token.tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    # Channel-level idempotency: a channel_message_id already recorded for
    # this conversation is a replay; return the original turn.
    if payload.channel_message_id:
        existing = database.get_message_by_channel_id(
            token.tenant_id, conversation_id, payload.channel_message_id
        )
        if existing is not None:
            cached = database.get_turn_by_message_id(
                token.tenant_id, conversation_id, existing["id"]
            )
            if cached is not None:
                cached["idempotent_replay"] = True
                return TurnResponse(**cached).model_dump(mode="json")
    actor = token.customer_ref or "widget"
    key = f"widget-{conversation_id}-{actor}-{payload.channel_message_id or ''}"
    if async_mode:
        overload = backpressure_reason(database, services.settings, token.tenant_id)
        if overload:
            raise HTTPException(
                status_code=429,
                detail=overload,
                headers={"Retry-After": "30"},
            )
        job, replayed = orchestrator.queue_customer_message(
            token.tenant_id,
            conversation_id,
            payload.content,
            actor_id=actor,
            idempotency_key=key,
            max_attempts=services.settings.turn_job_max_attempts,
            channel_message_id=payload.channel_message_id,
        )
        services.turn_worker.notify()
        return {
            "job_id": job["id"],
            "status": job["status"],
            "idempotent_replay": replayed,
        }
    try:
        result = orchestrator.handle_customer_message(
            token.tenant_id,
            conversation_id,
            payload.content,
            actor_id=actor,
            idempotency_key=key,
            channel_message_id=payload.channel_message_id,
        )
    except Exception as exc:
        # Map domain errors to the shared error contract codes.
        from app.orchestrator import (
            IdempotencyConflictError,
            InvalidTransitionError,
            TurnInProgressError,
        )

        if isinstance(exc, LookupError):
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if isinstance(exc, TurnInProgressError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, IdempotencyConflictError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, InvalidTransitionError):
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if isinstance(exc, ValueError):
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        raise
    return TurnResponse(**result).model_dump(mode="json")


@router.get(
    "/sessions/{conversation_id}/messages",
    response_model=list[MessageOut],
)
def list_widget_messages(
    request: Request,
    response: Response,
    conversation_id: str,
    token: Annotated[WidgetToken, Depends(_verify_widget_token)],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[MessageOut]:
    services = _services(request)
    database: Database = services.database
    _ensure_tenant(token, database)
    _ensure_session_token(token, conversation_id)
    conversation = database.get_conversation(token.tenant_id, conversation_id)
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
    response.headers["X-Conversation-Status"] = str(conversation["status"])
    rows = [
        row
        for row in database.list_messages(token.tenant_id, conversation_id, limit=limit)
        if row.get("role") not in {"internal", "internal_note"}
    ]
    return [message_out(row) for row in rows]


@router.get("/sessions/{conversation_id}/stream")
async def stream_widget_turn(
    request: Request,
    conversation_id: str,
    token: Annotated[WidgetToken, Depends(_verify_widget_token)],
    timeout: Annotated[int, Query(ge=1, le=120)] = 30,
) -> StreamingResponse:
    """SSE stream of the conversation's latest turn job (Phase 23.1).

    Reuses the same token/job events as the operator turn-job stream so the
    widget gets progressive output without holding an API key.
    """
    services = _services(request)
    database: Database = services.database
    turn_worker = services.turn_worker
    _ensure_tenant(token, database)
    _ensure_session_token(token, conversation_id)
    job = database.get_latest_turn_job(token.tenant_id, conversation_id)
    if not job:
        raise HTTPException(status_code=404, detail="No turn job for conversation")
    job_id = job["id"]

    async def event_stream() -> AsyncIterator[str]:
        # Phase 29.1: reconnect hint + drain signal on graceful shutdown.
        # 43.2: the generator runs during response streaming — re-bind the
        # token's tenant scope so the polling reads stay row-level scoped.
        bind_tenant_scope(token.tenant_id)
        yield "retry: 2000\n\n"
        last_status = ""
        last_updated = ""
        last_chunk_seq = 0
        deadline = perf_counter() + timeout
        while True:
            if turn_worker.is_stopping:
                yield 'event: shutdown\ndata: {"detail":"server shutting down"}\n\n'
                break
            if await request.is_disconnected():
                turn_worker.cancel_stream(job_id)
                break
            current = database.get_turn_job(token.tenant_id, job_id)
            if not current:
                yield 'event: error\ndata: {"detail":"Turn job not found"}\n\n'
                break
            for chunk in database.list_turn_job_chunks(
                token.tenant_id, job_id, after_seq=last_chunk_seq
            ):
                payload = {
                    "seq": int(chunk["seq"]),
                    "content": str(chunk["content"]),
                }
                yield f"event: token\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                last_chunk_seq = int(chunk["seq"])
            status = str(current["status"])
            updated_at = str(current["updated_at"])
            if status != last_status or updated_at != last_updated:
                yield (
                    "event: job\n"
                    f"data: {json.dumps(cast(Any, current), ensure_ascii=False, default=str)}\n\n"
                )
                last_status = status
                last_updated = updated_at
                if status in {"completed", "failed"}:
                    break
            if perf_counter() >= deadline:
                yield 'event: timeout\ndata: {"detail":"stream timeout"}\n\n'
                break
            yield "event: ping\ndata: {}\n\n"
            await asyncio.sleep(0.2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
