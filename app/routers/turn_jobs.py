"""Turn intake and durable turn-job routes (Phase 27.2 home).

Extracted from ``app/routers/conversations.py`` when the route factory
outgrew the 800-line discipline: the customer-message intake, the durable
turn-job API (list/get/retry) and its SSE stream are one domain, mounted
by ``app.routers.conversations.build_router``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

import asyncio
import json

from collections.abc import AsyncIterator
from time import perf_counter

from app.intake import backpressure_reason
from app.main import (
    IDEMPOTENCY_KEY_PATTERN,
    require_any_permission,
    require_permission,
    turn_job_out,
)
from app.routers.common import RouteDeps
from app.schemas import MessageRequest, TurnJobOut, TurnResponse
from app.security import Principal
from uuid import uuid4


def build_turn_jobs_router(deps: RouteDeps, router: APIRouter) -> None:
    database = deps.database
    orchestrator = deps.orchestrator
    turn_worker = deps.turn_worker
    settings = deps.settings

    @router.post(
        "/api/conversations/{conversation_id}/messages",
        response_model=TurnResponse,
    )
    def post_customer_message(
        conversation_id: str,
        payload: MessageRequest,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TurnResponse:
        if idempotency_key and not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
        key = idempotency_key or f"turn_{uuid4().hex}"
        result = orchestrator.handle_customer_message(
            principal.tenant_id,
            conversation_id,
            payload.content,
            principal.actor_id,
            key,
        )
        return TurnResponse(**result)

    @router.post(
        "/api/conversations/{conversation_id}/turn-jobs",
        response_model=TurnJobOut,
        status_code=202,
    )
    def enqueue_turn_job(
        conversation_id: str,
        payload: MessageRequest,
        response: Response,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
        idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    ) -> TurnJobOut:
        if idempotency_key and not IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key):
            raise HTTPException(status_code=400, detail="Invalid Idempotency-Key")
        # Phase 29.2 backpressure: refuse intake when the queue is overloaded.
        overload = backpressure_reason(database, settings, principal.tenant_id)
        if overload:
            raise HTTPException(
                status_code=429,
                detail=overload,
                headers={"Retry-After": "30"},
            )
        key = idempotency_key or f"turn_{uuid4().hex}"
        job, replayed = orchestrator.queue_customer_message(
            principal.tenant_id,
            conversation_id,
            payload.content,
            principal.actor_id,
            key,
            settings.turn_job_max_attempts,
        )
        response.headers["Location"] = f"/api/turn-jobs/{job['id']}"
        response.headers["X-Idempotent-Replay"] = str(replayed).lower()
        turn_worker.notify()
        return turn_job_out(job, idempotent_replay=replayed)

    @router.get("/api/turn-jobs", response_model=list[TurnJobOut])
    def list_turn_jobs(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        response: Response,
        status: Annotated[
            str | None,
            Query(pattern=r"^(queued|processing|completed|failed)$"),
        ] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> list[TurnJobOut]:
        rows = database.list_turn_jobs(
            principal.tenant_id,
            status=status,
            limit=limit + 1,
            offset=offset,
        )
        response.headers["X-Has-More"] = str(len(rows) > limit).lower()
        response.headers["X-Page-Limit"] = str(limit)
        response.headers["X-Page-Offset"] = str(offset)
        return [turn_job_out(row, include_result=False) for row in rows[:limit]]

    @router.get("/api/turn-jobs/{job_id}", response_model=TurnJobOut)
    def get_turn_job(
        job_id: str,
        principal: Annotated[
            Principal,
            Depends(require_any_permission("conversation:read", "conversation:write")),
        ],
    ) -> TurnJobOut:
        job = database.get_turn_job(principal.tenant_id, job_id)
        if not job:
            raise LookupError("Turn job not found")
        return turn_job_out(job)

    @router.get("/api/turn-jobs/{job_id}/events")
    async def stream_turn_job_events(
        job_id: str,
        request: Request,
        principal: Annotated[
            Principal,
            Depends(require_any_permission("conversation:read", "conversation:write")),
        ],
        timeout: Annotated[int, Query(ge=1, le=120)] = 30,
    ) -> StreamingResponse:
        job = database.get_turn_job(principal.tenant_id, job_id)
        if not job:
            raise LookupError("Turn job not found")

        async def event_stream() -> AsyncIterator[str]:
            # Phase 29.1: advertise the reconnect interval and tell the client
            # to reconnect (drain) when the process is shutting down.
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
                    # Client gone: stop writing remaining paced chunks for this
                    # turn (Phase 19.5). The reply itself is already durable.
                    turn_worker.cancel_stream(job_id)
                    break
                current = database.get_turn_job(principal.tenant_id, job_id)
                if not current:
                    yield 'event: error\ndata: {"detail":"Turn job not found"}\n\n'
                    break
                # Emit any newly persisted output chunks before the job state,
                # so the client sees tokens arrive progressively while the
                # worker is still pacing writes.  ``seq`` is monotonic, so a
                # resumable poll can never skip or repeat a chunk.
                for chunk in database.list_turn_job_chunks(
                    principal.tenant_id, job_id, after_seq=last_chunk_seq
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
                    payload = turn_job_out(current).model_dump(mode="json")
                    yield f"event: job\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_status = status
                    last_updated = updated_at
                    if status in {"completed", "failed"}:
                        break
                if perf_counter() >= deadline:
                    yield 'event: timeout\ndata: {"detail":"stream timeout"}\n\n'
                    break
                yield "event: ping\ndata: {}\n\n"
                # ROADMAP 18.2c: poll cadence bounds worst-case TTFT transport
                # latency (the first poll is immediate, so this only affects how
                # long a token waits when the worker writes it mid-interval).
                await asyncio.sleep(settings.turn_job_sse_poll_interval_ms / 1000)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.post("/api/turn-jobs/{job_id}/retry", response_model=TurnJobOut, status_code=202)
    def retry_turn_job(
        job_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
    ) -> TurnJobOut:
        job = orchestrator.retry_turn_job(
            principal.tenant_id,
            job_id,
            principal.actor_id,
        )
        turn_worker.notify()
        return turn_job_out(job)
