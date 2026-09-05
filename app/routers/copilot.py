"""AI-assisted operator copilot routes (backlog: AI 辅助坐席).

Three best-effort operator tools: reply suggestions, knowledge
recommendations, and tone rewrites. All require ``operator:act`` — the
copilot is an operator workspace tool, so read-only roles are denied.
"""

from __future__ import annotations

from typing import Any, Annotated

from fastapi import APIRouter, Depends, HTTPException

from app.copilot import CopilotService
from app.main import require_permission
from app.routers.common import RouteDeps
from app.schemas import (
    CopilotKnowledgeArticleOut,
    CopilotKnowledgeOut,
    CopilotKnowledgeRequest,
    CopilotRewriteOut,
    CopilotRewriteRequest,
    CopilotSuggestionOut,
    CopilotSuggestOut,
    CopilotSuggestRequest,
)
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    copilot: CopilotService = deps.services.copilot
    orchestrator = deps.orchestrator

    @router.post("/api/copilot/suggest", response_model=CopilotSuggestOut)
    def copilot_suggest(
        payload: CopilotSuggestRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> CopilotSuggestOut:
        try:
            suggestions = copilot.suggest_reply(
                principal.tenant_id,
                payload.conversation_id,
                draft=payload.draft,
                limit=payload.limit,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CopilotSuggestOut(suggestions=[CopilotSuggestionOut(**item) for item in suggestions])

    @router.post("/api/copilot/knowledge", response_model=CopilotKnowledgeOut)
    def copilot_knowledge(
        payload: CopilotKnowledgeRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> CopilotKnowledgeOut:
        try:
            articles = copilot.recommend_knowledge(
                principal.tenant_id,
                payload.conversation_id,
                query=payload.query,
                limit=payload.limit,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return CopilotKnowledgeOut(
            articles=[CopilotKnowledgeArticleOut(**item) for item in articles]
        )

    @router.post(
        "/api/copilot/knowledge-draft",
        tags=["copilot"],
        summary="Draft a pending-review knowledge article (governed tool)",
        description=(
            "ROADMAP 2.5.0: the first mutating tool call. The payload runs "
            "through the orchestrator's ToolGateway — governance policy "
            "(mutating side effect), argument schema, and a short-lived "
            "capability token when CAPABILITY_SECRET is configured — and "
            "the draft lands in draft status for human review before it "
            "can ever be retrieved. Requires ``operator:act``."
        ),
    )
    def copilot_knowledge_draft(
        body: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> dict[str, Any]:
        gateway = orchestrator.tools
        arguments = {
            "title": str(body.get("title") or ""),
            "content": str(body.get("content") or ""),
            "tags": list(body.get("tags") or []),
            "category": str(body.get("category") or "general"),
            "source_url": str(body.get("source_url") or ""),
            "language": str(body.get("language") or ""),
        }
        execution = gateway.draft_knowledge(
            principal.tenant_id,
            arguments,
            actor_id=principal.actor_id,
            capability=gateway.mint_capability_token("knowledge.draft", principal.tenant_id),
        )
        if not execution.success:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"tool denied ({execution.output.get('reason')}): "
                    f"{execution.output.get('message')}"
                ),
            )
        return {
            "article_id": execution.output.get("article_id"),
            "status": execution.output.get("status"),
            "tool": execution.tool,
            "duration_ms": execution.duration_ms,
        }

    @router.post("/api/copilot/rewrite", response_model=CopilotRewriteOut)
    def copilot_rewrite(
        payload: CopilotRewriteRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> CopilotRewriteOut:
        return CopilotRewriteOut(
            **copilot.rewrite_tone(payload.text, payload.tone, tenant_id=principal.tenant_id)
        )

    return router
