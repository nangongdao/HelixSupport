"""Agent collaboration routes: mentions, notes, threads (Phase 27.2 home).

Extracted from ``app/routers/conversations.py``: the operator-collaboration
surface (@-mention autocomplete, internal notes, discussion threads,
mentions inbox) is one domain, mounted by
``app.routers.conversations.build_router``.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.main import (
    conversation_out,
    require_permission,
)
from app.routers.common import RouteDeps
from app.schemas import (
    TranslateOut,
    TranslateMessageRequest,
    ConversationOut,
    SetConversationLanguageRequest,
    CollaboratorOut,
    MentionOut,
    MentionsOut,
)
from app.security import Principal


def build_collaboration_router(deps: RouteDeps, router: APIRouter) -> None:
    database = deps.database
    orchestrator = deps.orchestrator

    @router.get("/api/collaborators", response_model=list[CollaboratorOut])
    def list_collaborators(
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> list[CollaboratorOut]:
        """Tenant actors for the @-mention autocomplete (backlog M18).

        Read-only roster of member actor ids; restricted to operators (the
        only roles who act in the workspace) so viewer/auditor cannot
        enumerate the roster. The note composer filters out the writer and
        matches the trailing ``@token`` client-side.
        """
        return [CollaboratorOut(**row) for row in database.list_collaborators(principal.tenant_id)]

    @router.patch(
        "/api/conversations/{conversation_id}/language",
        response_model=ConversationOut,
    )
    def update_conversation_language(
        conversation_id: str,
        payload: SetConversationLanguageRequest,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
    ) -> ConversationOut:
        """Set (or clear) the manual language override for a conversation.

        Backlog (多语言客服): ``language`` null clears the override so the
        writer path re-detects automatically. The endpoint is a full upsert —
        it returns the stored row so the frontend can sync its select.
        """
        existing = database.get_conversation(principal.tenant_id, conversation_id)
        if existing is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        database.set_conversation_language(principal.tenant_id, conversation_id, payload.language)
        database.audit(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            "conversation.language_changed",
            {"language": payload.language},
        )
        updated = database.get_conversation(principal.tenant_id, conversation_id)
        if updated is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        return conversation_out(updated)

    @router.post(
        "/api/conversations/{conversation_id}/messages/{message_id}/translate",
        response_model=TranslateOut,
    )
    def translate_message(
        conversation_id: str,
        message_id: str,
        payload: TranslateMessageRequest,
        principal: Annotated[Principal, Depends(require_permission("conversation:write"))],
    ) -> TranslateOut:
        """Translate one customer message into ``target_language``.

        Backlog (多语言客服): never blocks on model availability — without a
        configured provider the original text is returned with
        ``was_translated=False`` and ``source="rule"`` so the frontend can
        degrade gracefully.
        """
        message = database.get_message(principal.tenant_id, conversation_id, message_id)
        if message is None:
            raise HTTPException(status_code=404, detail="message not found")
        if message.get("role") != "customer":
            raise HTTPException(
                status_code=422,
                detail="only customer messages can be translated",
            )
        translated, was_translated, source = orchestrator.languages.translate(
            message.get("content") or "",
            payload.target_language,
            tenant_id=principal.tenant_id,
        )
        database.audit(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            "message.translated",
            {
                "message_id": message_id,
                "target_language": payload.target_language,
                "source": source,
            },
        )
        return TranslateOut(translated=translated, was_translated=was_translated, source=source)

    @router.get("/api/mentions", response_model=MentionsOut)
    def list_mentions(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        unread_only: Annotated[bool, Query()] = False,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> MentionsOut:
        rows = database.list_mentions_for_actor(
            principal.tenant_id,
            principal.actor_id,
            unread_only=unread_only,
            limit=limit,
            offset=offset,
        )
        mentions = [
            MentionOut(
                id=row["id"],
                conversation_id=row["conversation_id"],
                conversation_customer=row["customer_name"] or row["conversation_id"],
                channel=row["channel"] or "web",
                mentioned_by=row["mentioned_by"],
                note_id=row["note_id"],
                note_preview=row.get("note_preview", ""),
                created_at=row["created_at"],
                read_at=row["read_at"],
                unread=bool(row["unread"]),
            )
            for row in rows
        ]
        return MentionsOut(
            mentions=mentions,
            unread_count=database.count_unread_mentions(principal.tenant_id, principal.actor_id),
        )

    @router.post("/api/mentions/{mention_id}/read", response_model=MentionOut)
    def mark_mention_read(
        mention_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> MentionOut:
        row = database.mark_mention_read(principal.tenant_id, principal.actor_id, mention_id)
        if not row:
            raise LookupError("Mention not found")
        database.audit(
            principal.tenant_id,
            row.get("conversation_id"),
            principal.actor_id,
            "conversation.mention_read",
            {"mention_id": mention_id, "note_id": row.get("note_id")},
        )
        return MentionOut(
            id=row["id"],
            conversation_id=row.get("conversation_id") or "",
            conversation_customer="",
            channel="web",
            mentioned_by=row.get("mentioned_by") or "",
            note_id=row.get("note_id") or "",
            note_preview="",
            created_at=row["created_at"],
            read_at=row["read_at"],
            unread=bool(row["unread"]),
        )
