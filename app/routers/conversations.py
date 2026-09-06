"""Conversations, saved views, canned responses, audit, and knowledge routes (27.2)."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections.abc import AsyncIterator
from time import perf_counter
from typing import Annotated
from typing import Annotated as TAnnotated  # noqa: F401
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse

from app.labels import normalize_conversation_labels
from app.main import (
    _conversation_quota_exceeded,
    _message_date_for_quality,
    _message_intent_for_quality,
    _message_prompt_version_for_quality,
    conversation_out,
    message_out,
    require_any_permission,
    require_permission,
)
from app.pagination import (
    InvalidCursorError,
    decode_conversation_cursor,
    decode_message_cursor,
    encode_conversation_cursor,
    encode_message_cursor,
)
from app.routers.common import RouteDeps
from app.schemas import (
    AssignConversationRequest,
    AuditEventOut,
    BulkConversationActionOut,
    BulkConversationActionRequest,
    ConversationDetail,
    ConversationLabelOut,
    ConversationLabelsRequest,
    ConversationOut,
    ConversationPriorityRequest,
    ConversationSummaryOut,
    ConversationThreadsOut,
    CreateConversationRequest,
    FeedbackOut,
    FeedbackRequest,
    InternalNoteRequest,
    MessageOut,
    OperatorMessageRequest,
    SavedQueueViewCreateRequest,
    SavedQueueViewOut,
    ThreadOut,
)
from app.security import Principal, Role

logger = logging.getLogger("helix")


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database
    orchestrator = deps.orchestrator
    services = deps.services
    turn_worker = deps.turn_worker
    settings = deps.settings

    @router.get("/api/conversations", response_model=list[ConversationOut])
    def list_conversations(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        response: Response,
        status: Annotated[
            str | None,
            Query(pattern=r"^(open|waiting_human|human_active|resolved)$"),
        ] = None,
        search: Annotated[str | None, Query(max_length=120)] = None,
        label: Annotated[str | None, Query(max_length=32)] = None,
        priority: Annotated[str | None, Query(pattern=r"^(normal|high)$")] = None,
        channel: Annotated[str | None, Query(max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")] = None,
        assigned_to: Annotated[str | None, Query(max_length=80)] = None,
        claimed_by: Annotated[str | None, Query(max_length=80)] = None,
        mine: Annotated[bool, Query()] = False,
        unassigned: Annotated[bool, Query()] = False,
        unclaimed: Annotated[bool, Query()] = False,
        sla_breached: Annotated[bool | None, Query()] = None,
        needs_response: Annotated[bool | None, Query()] = None,
        archived: Annotated[bool, Query()] = False,
        sort: Annotated[
            str,
            Query(pattern=r"^(priority|waiting|sla|updated)$"),
        ] = "priority",
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
    ) -> list[ConversationOut]:
        if cursor and offset:
            raise HTTPException(status_code=400, detail="cursor and offset cannot be combined")
        if mine and assigned_to:
            raise HTTPException(status_code=400, detail="mine and assigned_to cannot be combined")
        if unassigned and assigned_to:
            raise HTTPException(
                status_code=400, detail="unassigned and assigned_to cannot be combined"
            )
        if unclaimed and claimed_by:
            raise HTTPException(
                status_code=400, detail="unclaimed and claimed_by cannot be combined"
            )
        try:
            decoded_cursor = decode_conversation_cursor(cursor) if cursor else None
        except InvalidCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if decoded_cursor is not None and decoded_cursor[0] != sort:
            raise HTTPException(status_code=400, detail="cursor sort does not match requested sort")
        try:
            normalized_label = normalize_conversation_labels([label])[0] if label else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        resolved_assigned = principal.actor_id if mine else assigned_to
        try:
            rows = database.list_conversations(
                principal.tenant_id,
                status=status,
                search=search,
                label=normalized_label,
                priority=priority,
                channel=channel,
                assigned_to=resolved_assigned,
                claimed_by=claimed_by,
                unassigned=unassigned,
                unclaimed=unclaimed,
                sla_breached=sla_breached,
                needs_response=needs_response,
                sort=sort,
                limit=limit + 1,
                offset=offset,
                cursor=decoded_cursor,
                archived=archived,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        has_more = len(rows) > limit
        visible_rows = rows[:limit]
        response.headers["X-Has-More"] = str(has_more).lower()
        response.headers["X-Page-Limit"] = str(limit)
        response.headers["X-Page-Offset"] = str(offset)
        response.headers["X-Queue-Sort"] = sort
        if has_more and visible_rows:
            last = visible_rows[-1]
            if sort == "waiting":
                sort_key = str(last.get("waiting_since") or "9999-12-31T00:00:00+00:00")
            elif sort == "sla":
                sort_key = str(last.get("sla_due_at") or "9999-12-31T00:00:00+00:00")
            else:
                sort_key = None
            response.headers["X-Next-Cursor"] = encode_conversation_cursor(
                int(last["queue_priority_rank"]),
                str(last["updated_at"]),
                str(last["id"]),
                sort=sort,
                sort_key=sort_key,
            )
        return [conversation_out(row) for row in visible_rows]

    @router.get("/api/events/queue")
    async def stream_queue_events(
        request: Request,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        timeout: Annotated[int, Query(ge=5, le=120)] = 45,
    ) -> StreamingResponse:
        async def event_stream() -> AsyncIterator[str]:
            yield "retry: 2000\n\n"
            last_watermark = database.queue_revision(principal.tenant_id)
            deadline = perf_counter() + timeout
            yield (
                "event: snapshot\ndata: "
                f"{json.dumps({'watermark': last_watermark}, ensure_ascii=False)}\n\n"
            )
            while True:
                if turn_worker.is_stopping:
                    yield 'event: shutdown\ndata: {"detail":"server shutting down"}\n\n'
                    break
                if await request.is_disconnected():
                    break
                current = database.queue_revision(principal.tenant_id)
                if current != last_watermark:
                    payload = {"watermark": current, "changed": True}
                    yield f"event: queue\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last_watermark = current
                if perf_counter() >= deadline:
                    yield 'event: timeout\ndata: {"detail":"stream timeout"}\n\n'
                    break
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(2.5 if not request.headers.get("X-Low-Perf") else 5.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @router.get("/api/saved-views", response_model=list[SavedQueueViewOut])
    def list_saved_views(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> list[SavedQueueViewOut]:
        return [
            SavedQueueViewOut(**row)
            for row in database.list_saved_views(principal.tenant_id, principal.actor_id)
        ]

    @router.post("/api/saved-views", response_model=SavedQueueViewOut, status_code=201)
    def create_saved_view(
        payload: SavedQueueViewCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> SavedQueueViewOut:
        try:
            row = database.create_saved_view(
                principal.tenant_id,
                principal.actor_id,
                payload.name,
                payload.filters.model_dump(exclude_none=True),
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(
                status_code=409, detail="A saved view with this name already exists"
            ) from exc
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "saved_view.created",
            {"view_id": row["id"], "name": row["name"], "filters": row["filters"]},
        )
        return SavedQueueViewOut(**row)

    @router.delete("/api/saved-views/{view_id}", status_code=204)
    def delete_saved_view(
        view_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> Response:
        if not database.delete_saved_view(principal.tenant_id, principal.actor_id, view_id):
            raise HTTPException(status_code=404, detail="Saved view not found")
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "saved_view.deleted",
            {"view_id": view_id},
        )
        return Response(status_code=204)

    @router.get("/api/conversation-labels", response_model=list[ConversationLabelOut])
    def list_conversation_labels(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> list[ConversationLabelOut]:
        return [
            ConversationLabelOut(**row)
            for row in database.list_conversation_labels(principal.tenant_id)
        ]

    @router.post(
        "/api/conversations/bulk-actions",
        response_model=BulkConversationActionOut,
    )
    def bulk_conversation_action(
        payload: BulkConversationActionRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> BulkConversationActionOut:
        force = principal.role in {Role.ADMIN, Role.SUPERVISOR}
        try:
            result = orchestrator.bulk_action(
                principal.tenant_id,
                principal.actor_id,
                payload.conversation_ids,
                payload.action,
                priority=payload.priority,
                labels=payload.labels,
                force=force,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return BulkConversationActionOut(**result)

    @router.post("/api/conversations", response_model=ConversationOut, status_code=201)
    def create_conversation(
        payload: CreateConversationRequest,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
    ) -> ConversationOut:
        # Phase 22.4: enforce the tenant conversation quota before creating.
        quota_error = _conversation_quota_exceeded(database, principal.tenant_id)
        if quota_error:
            raise HTTPException(
                status_code=429,
                detail=quota_error,
                headers={"Retry-After": "3600"},
            )
        row = database.create_conversation(
            principal.tenant_id,
            payload.customer_name,
            payload.customer_ref,
            payload.channel,
            principal.actor_id,
            settings.normal_sla_minutes,
        )
        # Phase 20.5: outbound webhook event; failures must not break creation.
        assert services.webhooks is not None
        try:
            services.webhooks.emit_event(
                principal.tenant_id,
                "conversation.created",
                {
                    "conversation_id": row["id"],
                    "customer_name": row["customer_name"],
                    "channel": row["channel"],
                    "created_at": row["created_at"],
                },
                str(uuid4()),
            )
        except Exception:
            logger.exception("webhook.emit_failed", extra={"event_type": "conversation.created"})
        return conversation_out(row)

    @router.patch("/api/conversations/{conversation_id}", response_model=ConversationOut)
    def update_conversation(
        conversation_id: str,
        payload: ConversationPriorityRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.update_priority(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                payload.priority,
            )
        )

    @router.put("/api/conversations/{conversation_id}/labels", response_model=ConversationOut)
    def replace_conversation_labels(
        conversation_id: str,
        payload: ConversationLabelsRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.replace_labels(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                payload.labels,
            )
        )

    @router.get("/api/conversations/{conversation_id}", response_model=ConversationDetail)
    def get_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        response: Response,
        message_limit: Annotated[int | None, Query(ge=1, le=500)] = None,
        message_cursor: Annotated[str | None, Query(max_length=512)] = None,
        messages_before: Annotated[bool, Query()] = False,
    ) -> ConversationDetail:
        conversation = database.get_conversation(principal.tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        try:
            decoded_message_cursor = (
                decode_message_cursor(message_cursor) if message_cursor else None
            )
        except InvalidCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        messages = database.list_messages(
            principal.tenant_id,
            conversation_id,
            limit=message_limit,
            cursor=decoded_message_cursor,
            before=messages_before,
        )
        # ROADMAP §18.4: when a ``message_limit`` slice is requested the detail
        # response carries the same opaque keyset cursors as /messages, so the
        # client can lazily load older messages upward without interpreting the
        # cursor payload itself (docs/API_POLICY.md §3 — echo, don't parse).
        # The default (no limit) still returns the full transcript unchanged.
        if message_limit is not None:
            response.headers["X-Has-More"] = str(len(messages) >= message_limit).lower()
            response.headers["X-Page-Limit"] = str(message_limit)
            if messages:
                first = messages[0]
                last = messages[-1]
                response.headers["X-Prev-Cursor"] = encode_message_cursor(
                    str(first["created_at"]), int(first["seq"])
                )
                response.headers["X-Next-Cursor"] = encode_message_cursor(
                    str(last["created_at"]), int(last["seq"])
                )
        return ConversationDetail(
            conversation=conversation_out(conversation),
            messages=[message_out(item) for item in messages],
            audit_events=[
                AuditEventOut(**item)
                for item in database.list_audit(principal.tenant_id, conversation_id)
            ],
            summaries=[
                ConversationSummaryOut(
                    kind=item["kind"],
                    content=item["content"],
                    source=item["source"],
                    updated_at=item["updated_at"],
                )
                for item in database.list_conversation_summaries(
                    principal.tenant_id, conversation_id
                )
            ],
        )

    @router.get(
        "/api/conversations/{conversation_id}/messages",
        response_model=list[MessageOut],
    )
    def list_conversation_messages(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        response: Response,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
        cursor: Annotated[str | None, Query(max_length=512)] = None,
        before: Annotated[bool, Query()] = False,
    ) -> list[MessageOut]:
        if not database.get_conversation(principal.tenant_id, conversation_id):
            raise LookupError("Conversation not found")
        try:
            decoded_cursor = decode_message_cursor(cursor) if cursor else None
        except InvalidCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        rows = database.list_messages(
            principal.tenant_id,
            conversation_id,
            limit=limit + 1,
            cursor=decoded_cursor,
            before=before,
        )
        has_more = len(rows) > limit
        if before:
            visible_rows = rows[-limit:] if has_more else rows
        else:
            visible_rows = rows[:limit]
        response.headers["X-Has-More"] = str(has_more).lower()
        response.headers["X-Page-Limit"] = str(limit)
        if visible_rows:
            first = visible_rows[0]
            last = visible_rows[-1]
            response.headers["X-Prev-Cursor"] = encode_message_cursor(
                str(first["created_at"]), int(first["seq"])
            )
            response.headers["X-Next-Cursor"] = encode_message_cursor(
                str(last["created_at"]), int(last["seq"])
            )
        return [message_out(item) for item in visible_rows]

    @router.post("/api/conversations/{conversation_id}/claim", response_model=ConversationOut)
    def claim_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.claim(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                force=principal.role in {Role.ADMIN, Role.SUPERVISOR},
            )
        )

    @router.post("/api/conversations/{conversation_id}/release", response_model=ConversationOut)
    def release_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.release_claim(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                force=principal.role in {Role.ADMIN, Role.SUPERVISOR},
            )
        )

    @router.post("/api/conversations/{conversation_id}/assign", response_model=ConversationOut)
    def assign_conversation(
        conversation_id: str,
        payload: AssignConversationRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.assign(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                payload.assignee_id,
                force=principal.role in {Role.ADMIN, Role.SUPERVISOR},
            )
        )

    # Phase 27.2 homes: turn intake/jobs and the collaboration
    # surface are separate route factories (app/routers/turn_jobs.py,
    # app/routers/collaboration.py).
    from app.routers.turn_jobs import build_turn_jobs_router
    from app.routers.collaboration import build_collaboration_router

    build_turn_jobs_router(deps, router)
    build_collaboration_router(deps, router)

    @router.post("/api/conversations/{conversation_id}/accept", response_model=ConversationOut)
    def accept_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        row = orchestrator.handoff(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            can_override=principal.role in {Role.ADMIN, Role.SUPERVISOR},
        )
        return conversation_out(row)

    @router.post(
        "/api/conversations/{conversation_id}/operator-messages",
        response_model=MessageOut,
    )
    def operator_message(
        conversation_id: str,
        payload: OperatorMessageRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> MessageOut:
        attachment_ids: list[str] = []
        if payload.attachment_ids:
            # Backlog (语音/富媒体消息): validate ids against this
            # tenant+conversation, then backfill the message link after the
            # reply is stored.
            try:
                attachment_ids = services.attachments.validate_for_message(
                    principal.tenant_id, conversation_id, payload.attachment_ids
                )
            except LookupError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from exc
        message = orchestrator.operator_reply(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            payload.content,
            can_override=principal.role in {Role.ADMIN, Role.SUPERVISOR},
            attachment_ids=attachment_ids,
        )
        if attachment_ids:
            services.attachments.attach_message(principal.tenant_id, message["id"], attachment_ids)
        return MessageOut(**message)

    @router.post(
        "/api/conversations/{conversation_id}/notes",
        response_model=MessageOut,
    )
    def internal_note(
        conversation_id: str,
        payload: InternalNoteRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> MessageOut:
        return MessageOut(
            **orchestrator.add_internal_note(
                principal.tenant_id,
                conversation_id,
                principal.actor_id,
                payload.content,
                reply_to=payload.reply_to,
            )
        )

    @router.post("/api/conversations/{conversation_id}/resolve", response_model=ConversationOut)
    def resolve_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        row = orchestrator.resolve(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            can_override=principal.role in {Role.ADMIN, Role.SUPERVISOR},
        )
        return conversation_out(row)

    @router.post("/api/conversations/{conversation_id}/reopen", response_model=ConversationOut)
    def reopen_conversation(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> ConversationOut:
        return conversation_out(
            orchestrator.reopen(principal.tenant_id, conversation_id, principal.actor_id)
        )

    @router.post("/api/conversations/{conversation_id}/feedback", response_model=FeedbackOut)
    def submit_feedback(
        conversation_id: str,
        payload: FeedbackRequest,
        principal: Annotated[
            Principal,
            Depends(require_any_permission("conversation:read", "conversation:write")),
        ],
    ) -> FeedbackOut:
        if not database.get_message(principal.tenant_id, conversation_id, payload.message_id):
            raise LookupError("Message not found")
        # Read the previous rating *before* persisting so the quality
        # aggregate can compute the correct delta (Phase 21.1).
        previous_rating = (
            services.quality.get_feedback_rating(
                principal.tenant_id, payload.message_id, principal.actor_id
            )
            if services.quality is not None
            else None
        )
        feedback = database.record_feedback(
            principal.tenant_id,
            conversation_id,
            payload.message_id,
            principal.actor_id,
            payload.rating,
            payload.reason,
        )
        database.audit(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            "message.feedback_recorded",
            {"message_id": payload.message_id, "rating": payload.rating},
        )
        # Phase 21.1: keep the daily quality aggregate in sync with feedback
        # changes. A negative rating lands in ``quality_daily``; flipping back
        # to positive decrements it. Best-effort; failures here never block
        # the feedback write that already succeeded. The bucket date is the
        # turn's date (the rated message's creation date), not the rating
        # submission date, so a same-bucket invariant holds across days.
        if services.quality is not None:
            try:
                services.quality.apply_feedback_rating(
                    principal.tenant_id,
                    message_id=payload.message_id,
                    actor=principal.actor_id,
                    new_rating=payload.rating,
                    previous_rating=previous_rating,
                    intent=_message_intent_for_quality(
                        database, principal.tenant_id, conversation_id, payload.message_id
                    ),
                    prompt_version=_message_prompt_version_for_quality(
                        database, principal.tenant_id, payload.message_id
                    ),
                    date_str=_message_date_for_quality(
                        database, principal.tenant_id, payload.message_id
                    )
                    or feedback["updated_at"][:10],
                )
            except Exception:
                logger.exception("failed to update quality aggregate for feedback")
        # ROADMAP 2.6.0: a negative rating flip auto-stages the exchange into
        # the AI governance registry (redacted at ingest, pending human
        # review) so the eval dataset pipeline sees live drift material.
        # Exactly-once per flip, mirroring the quality aggregate; best-effort
        # — the rating write already succeeded and must never be blocked.
        if services.ai_governance is not None and payload.rating == -1 and previous_rating != -1:
            try:
                rated_message = database.get_message(
                    principal.tenant_id, conversation_id, payload.message_id
                )
                services.ai_governance.ingest_online_feedback(
                    tenant_id=principal.tenant_id,
                    conversation_id=conversation_id,
                    source="negative_rating",
                    payload={
                        "message_id": payload.message_id,
                        "rating": payload.rating,
                        "reason": payload.reason or "",
                        "rated_content": (rated_message or {}).get("content", ""),
                    },
                )
            except Exception:
                logger.exception("failed to stage governance online feedback")
        return FeedbackOut(**feedback)

    @router.get(
        "/api/conversations/{conversation_id}/threads",
        response_model=ConversationThreadsOut,
    )
    def list_conversation_threads(
        conversation_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> ConversationThreadsOut:
        if not database.get_conversation(principal.tenant_id, conversation_id):
            raise LookupError("Conversation not found")
        notes = database.list_notes(principal.tenant_id, conversation_id)
        by_id = {note["id"]: note for note in notes}
        roots: list[dict] = []
        replies_by_root: dict[str, list[dict]] = {}
        for note in notes:
            reply_to = note.get("reply_to")
            if reply_to and reply_to in by_id:
                replies_by_root.setdefault(reply_to, []).append(note)
            else:
                roots.append(note)
        threads = [
            ThreadOut(
                root=message_out(root),
                replies=[
                    message_out(reply)
                    for reply in sorted(
                        replies_by_root.get(root["id"], []), key=lambda n: n["created_at"]
                    )
                ],
            )
            for root in roots
        ]
        return ConversationThreadsOut(threads=threads)

    @router.get("/api/conversations/{conversation_id}/events")
    async def stream_conversation_events(
        conversation_id: str,
        request: Request,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        timeout: Annotated[int, Query(ge=5, le=120)] = 45,
    ) -> StreamingResponse:
        """Supervisor live view (旁观模式) for an in-progress conversation.

        Read-only SSE: emits a ``snapshot`` with the current revision, then a
        ``conversation`` event whenever the conversation's updated_at or the
        latest message seq changes. ``conversation:read`` suffices (no
        ``operator:act``), so a supervisor can watch without claiming or
        touching the conversation.
        """

        async def event_stream() -> AsyncIterator[str]:
            yield "retry: 2000\n\n"
            last = database.conversation_revision(principal.tenant_id, conversation_id)
            deadline = perf_counter() + timeout
            yield (
                "event: snapshot\ndata: "
                f"{json.dumps({'conversation_id': conversation_id, 'revision': last}, ensure_ascii=False)}\n\n"
            )
            while True:
                if turn_worker.is_stopping:
                    yield 'event: shutdown\ndata: {"detail":"server shutting down"}\n\n'
                    break
                if await request.is_disconnected():
                    break
                current = database.conversation_revision(principal.tenant_id, conversation_id)
                if current != last:
                    payload = {
                        "conversation_id": conversation_id,
                        "revision": current,
                        "changed": current != "missing",
                    }
                    yield f"event: conversation\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    last = current
                if perf_counter() >= deadline:
                    yield 'event: timeout\ndata: {"detail":"stream timeout"}\n\n'
                    break
                yield "event: ping\ndata: {}\n\n"
                await asyncio.sleep(2.5 if not request.headers.get("X-Low-Perf") else 5.0)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
