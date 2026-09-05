"""Governance registry API (ROADMAP 2.5.0).

Exposes the maker-checker approval registry the 43.5 tool governance
builds on: a ``high_risk``/mutating tool is only operable once an
approved ``tool_enablement`` record exists, and approvals cannot be
self-granted (the requester is refused as the decider inside the
service). Until this router existed the registry was test-only
machinery — production had no way to grant the approvals the gateway
requires. All routes require ``admin:manage``.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from app.ai_governance import GovernanceError
from app.main import require_permission
from app.routers.common import RouteDeps
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter(prefix="/api/admin/governance", tags=["governance"])
    governance = deps.services.ai_governance

    @router.get(
        "/approvals",
        summary="List tool-enablement approval requests",
        description=(
            "List maker-checker approval requests for governance subjects "
            "(tool_enablement, prompt_promotion, feedback_batch), newest "
            "first. Pass ``status=pending`` (the default) for open requests "
            "that gate tool operation. Requires ``admin:manage``."
        ),
    )
    def list_approvals(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        status: Annotated[str, Query()] = "pending",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        if status not in {"pending", "approved", "rejected", "all"}:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422, detail="status must be pending, approved, rejected or all"
            )
        return governance.list_approvals(principal.tenant_id, status=status, limit=limit)

    @router.post(
        "/approvals/request",
        summary="Open a maker-checker approval request",
        description=(
            "Open an approval request for a governance subject. One open "
            "request per subject at a time; the requester cannot also be "
            "the approver. Requires ``admin:manage``."
        ),
    )
    def request_approval(
        body: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        try:
            return governance.request_approval(
                tenant_id=principal.tenant_id,
                subject_kind=str(body.get("subject_kind") or ""),
                subject_id=str(body.get("subject_id") or ""),
                requested_by=principal.actor_id,
                reason=str(body.get("reason") or ""),
            )
        except GovernanceError as exc:
            from fastapi import HTTPException

            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @router.post(
        "/approvals/{approval_id}/decide",
        summary="Approve or reject a pending request",
        description=(
            "Resolve a pending approval. The requester cannot be the "
            "approver (maker-checker, SelfApprovalError → 409). Requires "
            "``admin:manage``."
        ),
    )
    def decide_approval(
        approval_id: str,
        body: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        from fastapi import HTTPException

        try:
            return governance.decide_approval(
                approval_id,
                decided_by=principal.actor_id,
                approve=bool(body.get("approve")),
                reason=str(body.get("reason") or ""),
            )
        except GovernanceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.get(
        "/feedback",
        summary="List staged online feedback for review",
        description=(
            "List the governance registry's staged online feedback (customer "
            "ratings auto-ingested with redaction, pending human review by "
            "default). Requires ``admin:manage``."
        ),
    )
    def list_feedback(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        status: Annotated[str, Query()] = "pending_review",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        if status not in {"pending_review", "accepted", "rejected", "all"}:
            from fastapi import HTTPException

            raise HTTPException(
                status_code=422,
                detail="status must be pending_review, accepted, rejected or all",
            )
        return governance.list_feedback(principal.tenant_id, status=status, limit=limit)

    @router.post(
        "/feedback/{feedback_id}/review",
        summary="Accept or reject staged online feedback",
        description=(
            "Human review of one staged feedback row: only ``accepted`` rows "
            "can later be promoted into an eval dataset. Requires "
            "``admin:manage``."
        ),
    )
    def review_feedback(
        feedback_id: str,
        body: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        from fastapi import HTTPException

        try:
            return governance.review_feedback(
                feedback_id,
                reviewed_by=principal.actor_id,
                accept=bool(body.get("accept")),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @router.post(
        "/datasets/promote-feedback",
        summary="Fold accepted feedback into a new eval dataset version",
        description=(
            "Create the next version of a named dataset from accepted "
            "feedback rows. Any pending/rejected id in the batch aborts the "
            "whole promotion (unreviewed material cannot ride along). "
            "Requires ``admin:manage``."
        ),
    )
    def promote_feedback(
        body: dict[str, Any],
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        from fastapi import HTTPException

        try:
            return governance.promote_feedback_to_dataset(
                feedback_ids=[str(fid) for fid in (body.get("feedback_ids") or [])],
                tenant_id=principal.tenant_id,
                dataset_name=str(body.get("dataset_name") or ""),
                requested_by=principal.actor_id,
            )
        except (LookupError, GovernanceError, ValueError) as exc:
            status_code = 404 if isinstance(exc, LookupError) else 409
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc

    @router.get(
        "/datasets",
        summary="List eval dataset registry rows",
        description="List the tenant's eval datasets, newest version first. Requires ``admin:manage``.",
    )
    def list_datasets(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
    ) -> list[dict[str, Any]]:
        return governance.list_datasets(principal.tenant_id, limit=limit)

    @router.get(
        "/datasets/{dataset_id}/items",
        summary="Load one dataset version's items",
        description="Load the item list of a dataset version. Requires ``admin:manage``.",
    )
    def load_dataset_items(
        dataset_id: str,
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> list[dict[str, Any]]:
        from fastapi import HTTPException

        try:
            return governance.load_dataset_items(dataset_id)
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    return router
