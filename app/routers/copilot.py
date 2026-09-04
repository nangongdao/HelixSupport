"""AI-assisted operator copilot routes (backlog: AI 辅助坐席).

Three best-effort operator tools: reply suggestions, knowledge
recommendations, and tone rewrites. All require ``operator:act`` — the
copilot is an operator workspace tool, so read-only roles are denied.
"""

from __future__ import annotations

from typing import Annotated

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

    @router.post("/api/copilot/rewrite", response_model=CopilotRewriteOut)
    def copilot_rewrite(
        payload: CopilotRewriteRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> CopilotRewriteOut:
        return CopilotRewriteOut(
            **copilot.rewrite_tone(payload.text, payload.tone, tenant_id=principal.tenant_id)
        )

    return router
