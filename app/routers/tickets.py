"""Long-cycle ticket routes (backlog: 工单化).

Tickets track issues across conversations: an operator converts a
conversation into a ticket (or links more conversations to an existing one),
and the ticket carries its own open/in_progress/closed lifecycle, decoupled
from conversation state. Writes require ``operator:act``; reads require
``conversation:read``. Invalid state-machine transitions return 409.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db.tickets import TICKET_STATUSES
from app.main import require_permission
from app.routers.common import RouteDeps
from app.schemas import (
    TicketConversationOut,
    TicketCreateRequest,
    TicketDetail,
    TicketLinkRequest,
    TicketOut,
    TicketTransitionRequest,
    TicketUpdateRequest,
)
from app.security import Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    database = deps.database

    @router.post("/api/tickets", response_model=TicketOut, status_code=201)
    def create_ticket(
        payload: TicketCreateRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.create_ticket(
                principal.tenant_id,
                conversation_id=payload.conversation_id,
                subject=payload.subject,
                description=payload.description,
                priority=payload.priority,
                actor_id=principal.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.get("/api/tickets", response_model=list[TicketOut])
    def list_tickets(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
        status: Annotated[str | None, Query(max_length=20)] = None,
        customer_ref: Annotated[str | None, Query(max_length=80)] = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> list[TicketOut]:
        if status and status not in TICKET_STATUSES:
            raise HTTPException(status_code=422, detail=f"status must be one of {TICKET_STATUSES}")
        tickets = database.list_tickets(
            principal.tenant_id, status=status, customer_ref=customer_ref, limit=limit
        )
        return [TicketOut(**ticket) for ticket in tickets]

    @router.get("/api/tickets/{ticket_id}", response_model=TicketDetail)
    def get_ticket(
        ticket_id: str,
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> TicketDetail:
        ticket = database.get_ticket(principal.tenant_id, ticket_id)
        if ticket is None:
            raise HTTPException(status_code=404, detail="Ticket not found")
        conversations = database.list_ticket_conversations(principal.tenant_id, ticket_id)
        return TicketDetail(
            **ticket, conversations=[TicketConversationOut(**item) for item in conversations]
        )

    @router.patch("/api/tickets/{ticket_id}", response_model=TicketOut)
    def update_ticket(
        ticket_id: str,
        payload: TicketUpdateRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        changes = payload.model_dump(exclude_unset=True)
        try:
            ticket = database.update_ticket(
                principal.tenant_id, ticket_id, changes, principal.actor_id
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.post("/api/tickets/{ticket_id}/transition", response_model=TicketOut)
    def transition_ticket(
        ticket_id: str,
        payload: TicketTransitionRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.transition_ticket(
                principal.tenant_id,
                ticket_id,
                principal.actor_id,
                payload.status,
                reason=payload.reason,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return TicketOut(**ticket)

    @router.post("/api/tickets/{ticket_id}/link", response_model=TicketOut)
    def link_ticket_conversation(
        ticket_id: str,
        payload: TicketLinkRequest,
        principal: Annotated[Principal, Depends(require_permission("operator:act"))],
    ) -> TicketOut:
        try:
            ticket = database.link_ticket_conversation(
                principal.tenant_id,
                ticket_id,
                payload.conversation_id,
                principal.actor_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return TicketOut(**ticket)

    return router
