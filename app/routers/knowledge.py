"""Canned responses, audit export, and knowledge routes (Phase 27.2)."""

from __future__ import annotations

import logging
import sqlite3
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.schemas import (
    AuditArchiveDetailOut,
    AuditArchiveOut,
    AuditEventOut,
    CannedResponseCreateRequest,
    CannedResponseOut,
    CannedResponseUpdateRequest,
    KnowledgeArticleOut,
    KnowledgeCreateRequest,
    KnowledgeReviewRequest,
    KnowledgeUpdateRequest,
)
from app.main import (
    canned_response_out,
    knowledge_out,
    require_permission,
)
from app.orchestrator import InvalidTransitionError
from app.routers.common import RouteDeps
from app.security import Principal

logger = logging.getLogger("helix")


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database

    @router.get("/api/canned-responses", response_model=list[CannedResponseOut])
    def list_canned_responses(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        search: Annotated[str | None, Query(max_length=120)] = None,
        include_inactive: bool = False,
    ) -> list[CannedResponseOut]:
        if include_inactive and not principal.can("knowledge:write"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return [
            canned_response_out(row)
            for row in database.list_canned_responses(
                principal.tenant_id,
                include_inactive=include_inactive,
                search=search,
            )
        ]

    @router.post("/api/canned-responses", response_model=CannedResponseOut, status_code=201)
    def create_canned_response(
        payload: CannedResponseCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> CannedResponseOut:
        try:
            row = database.create_canned_response(
                principal.tenant_id,
                title=payload.title,
                body=payload.body,
                shortcut=payload.shortcut,
                tags=payload.tags,
                actor_id=principal.actor_id,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Shortcut already exists") from exc
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "canned_response.created",
            {"response_id": row["id"], "shortcut": row.get("shortcut")},
        )
        return canned_response_out(row)

    @router.patch("/api/canned-responses/{response_id}", response_model=CannedResponseOut)
    def update_canned_response(
        response_id: str,
        payload: CannedResponseUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> CannedResponseOut:
        try:
            row = database.update_canned_response(
                principal.tenant_id,
                response_id,
                payload.model_dump(exclude_unset=True),
                principal.actor_id,
            )
        except sqlite3.IntegrityError as exc:
            raise HTTPException(status_code=409, detail="Shortcut already exists") from exc
        if not row:
            raise LookupError("Canned response not found")
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "canned_response.updated",
            {
                "response_id": response_id,
                "fields": sorted(payload.model_dump(exclude_unset=True)),
            },
        )
        return canned_response_out(row)

    @router.post("/api/canned-responses/{response_id}/use", response_model=CannedResponseOut)
    def use_canned_response(
        response_id: str,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> CannedResponseOut:
        row = database.record_canned_response_usage(principal.tenant_id, response_id)
        if not row:
            raise LookupError("Canned response not found")
        return canned_response_out(row)

    @router.get("/api/audit-events", response_model=list[AuditEventOut])
    def export_audit_events(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
        response: Response,
        conversation_id: Annotated[str | None, Query(max_length=160)] = None,
        event_type: Annotated[str | None, Query(max_length=80)] = None,
        since: Annotated[str | None, Query(max_length=40)] = None,
        until: Annotated[str | None, Query(max_length=40)] = None,
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> list[AuditEventOut]:
        rows = database.export_audit_events(
            principal.tenant_id,
            conversation_id=conversation_id,
            event_type=event_type,
            since=since,
            until=until,
            limit=limit + 1,
            offset=offset,
        )
        has_more = len(rows) > limit
        visible = rows[:limit]
        response.headers["X-Has-More"] = str(has_more).lower()
        response.headers["X-Page-Limit"] = str(limit)
        response.headers["X-Page-Offset"] = str(offset)
        return [
            AuditEventOut(
                id=row["id"],
                request_id=row.get("request_id"),
                actor=row["actor"],
                event_type=row["event_type"],
                payload=row["payload"],
                created_at=row["created_at"],
            )
            for row in visible
        ]

    @router.get("/api/audit-archives", response_model=list[AuditArchiveOut])
    def list_audit_archives(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    ) -> list[AuditArchiveOut]:
        return [
            AuditArchiveOut(**row)
            for row in database.list_audit_archives(principal.tenant_id, limit=limit, offset=offset)
        ]

    @router.get("/api/audit-archives/{archive_id}", response_model=AuditArchiveDetailOut)
    def get_audit_archive(
        archive_id: str,
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
    ) -> AuditArchiveDetailOut:
        row = database.get_audit_archive(principal.tenant_id, archive_id)
        if not row:
            raise HTTPException(status_code=404, detail="Audit archive not found")
        return AuditArchiveDetailOut(**row)

    @router.get("/api/knowledge", response_model=list[KnowledgeArticleOut])
    def list_knowledge(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        include_inactive: bool = False,
    ) -> list[KnowledgeArticleOut]:
        if include_inactive and not principal.can("knowledge:write"):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return [
            knowledge_out(row)
            for row in database.list_knowledge(principal.tenant_id, include_inactive)
        ]

    @router.post("/api/knowledge", response_model=KnowledgeArticleOut, status_code=201)
    def create_knowledge(
        payload: KnowledgeCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> KnowledgeArticleOut:
        article = database.create_knowledge(
            principal.tenant_id,
            payload.title,
            payload.content,
            payload.tags,
            payload.category,
            payload.source_url,
            payload.language,
        )
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "knowledge.created",
            {"article_id": article["id"], "version": article["version"]},
        )
        return knowledge_out(article)

    @router.patch("/api/knowledge/{article_id}", response_model=KnowledgeArticleOut)
    def update_knowledge(
        article_id: str,
        payload: KnowledgeUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> KnowledgeArticleOut:
        changes = payload.model_dump(exclude_unset=True)
        article = database.update_knowledge(principal.tenant_id, article_id, changes)
        if not article:
            raise LookupError("Knowledge article not found")
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "knowledge.updated",
            {
                "article_id": article_id,
                "version": article["version"],
                "fields": sorted(changes),
            },
        )
        return knowledge_out(article)

    # ------------------------------------------------------------------
    # Phase 21.3: knowledge lifecycle -- draft creation, approval, and
    # negative-feedback-to-draft reflow.
    # ------------------------------------------------------------------

    @router.post(
        "/api/knowledge/drafts",
        response_model=KnowledgeArticleOut,
        status_code=201,
    )
    def create_knowledge_draft(
        payload: KnowledgeCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> KnowledgeArticleOut:
        """Create a knowledge article in ``draft`` status (Phase 21.3).

        Drafts are invisible to retrieval until explicitly published through
        the review endpoint, so an approval step is mandatory.
        """
        article = database.create_knowledge_draft(
            principal.tenant_id,
            payload.title,
            payload.content,
            payload.tags,
            payload.category,
            payload.source_url,
            principal.actor_id,
            payload.language,
        )
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "knowledge.draft_created",
            {"article_id": article["id"], "version": article["version"]},
        )
        return knowledge_out(article)

    @router.post(
        "/api/knowledge/{article_id}/review",
        response_model=KnowledgeArticleOut,
    )
    def review_knowledge_article(
        article_id: str,
        payload: KnowledgeReviewRequest,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> KnowledgeArticleOut:
        """Approve (publish) or reject (retire) a pending knowledge article.

        The approval cannot be bypassed: only ``draft`` or ``pending_review``
        articles can be published, so a reviewer must act before the article
        becomes retrievable.
        """

        try:
            article = database.review_knowledge(
                principal.tenant_id,
                article_id,
                payload.action,
                principal.actor_id,
            )
        except InvalidTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if not article:
            raise LookupError("Knowledge article not found")
        database.audit(
            principal.tenant_id,
            None,
            principal.actor_id,
            "knowledge.reviewed",
            {
                "article_id": article_id,
                "action": payload.action,
                "status": article["status"],
                "notes": payload.notes,
            },
        )
        return knowledge_out(article)

    @router.post(
        "/api/conversations/{conversation_id}/messages/{message_id}/knowledge-draft",
        response_model=KnowledgeArticleOut,
        status_code=201,
    )
    def create_knowledge_draft_from_feedback(
        conversation_id: str,
        message_id: str,
        principal: Annotated[Principal, Depends(require_permission("knowledge:write"))],
    ) -> KnowledgeArticleOut:
        """Generate a draft knowledge article from a negatively-rated message.

        Reflows negative feedback into the knowledge base (Phase 21.3): the
        assistant message's content seeds a draft the reviewer can edit and
        publish.  Returns 404 if the message or conversation does not exist.
        """
        message = database.get_message(principal.tenant_id, conversation_id, message_id)
        if not message:
            raise LookupError("Message not found")
        conversation = database.get_conversation(principal.tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        intent = (conversation.get("intent") or "unknown")[:80]
        title = f"Draft from feedback: {intent}"
        content = message["content"]
        article = database.create_knowledge_draft(
            tenant_id=principal.tenant_id,
            title=title,
            content=content,
            tags=[intent],
            category="feedback_draft",
            source_url=f"conversation:{conversation_id}",
            actor_id=principal.actor_id,
        )
        database.audit(
            principal.tenant_id,
            conversation_id,
            principal.actor_id,
            "knowledge.draft_from_feedback",
            {
                "article_id": article["id"],
                "message_id": message_id,
                "conversation_id": conversation_id,
            },
        )
        return knowledge_out(article)

    return router
