"""Database tickets mixin (backlog: 工单化, Phase 27.1 style).

Long-cycle work items decoupled from conversation state. A conversation is
converted into a ticket (snapshotting the customer identity and the latest
customer message as the description) or linked to an existing ticket, so the
same issue can be tracked across several conversations. The ticket carries
its own ``open -> in_progress -> closed`` lifecycle; every transition is
audited, and invalid transitions raise :class:`ValueError` (mapped to 409 by
the router).
"""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
from typing import Any
from uuid import uuid4

from app.db._util import utc_now

TICKET_STATUSES = ("open", "in_progress", "closed")

# Valid status transitions for the ticket state machine.
_TICKET_TRANSITIONS: dict[str, frozenset[str]] = {
    "open": frozenset({"in_progress", "closed"}),
    "in_progress": frozenset({"closed"}),
    "closed": frozenset({"open"}),
}

_TICKET_UPDATE_FIELDS = frozenset({"subject", "description", "priority", "assigned_agent"})


class DatabaseTicketsMixin:
    # ------------------------------------------------------------------- read

    def get_ticket(self, tenant_id: str, ticket_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tickets WHERE tenant_id = ? AND id = ?",
                (tenant_id, ticket_id),
            ).fetchone()
        return dict(row) if row else None

    def list_tickets(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        customer_ref: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if status:
            clauses.append("status = ?")
            values.append(status)
        if customer_ref:
            clauses.append("customer_ref = ?")
            values.append(customer_ref)
        values.append(max(1, min(limit, 200)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM tickets WHERE {" AND ".join(clauses)}
                ORDER BY updated_at DESC, id DESC LIMIT ?""",
                values,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_ticket_conversations(self, tenant_id: str, ticket_id: str) -> list[dict[str, Any]]:
        """Linked conversations (id/customer/status/updated_at only).

        Internal notes and full transcripts are never surfaced here; the
        operator opens each linked conversation for details.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT c.id, c.customer_name, c.status, c.updated_at
                FROM ticket_conversations tc
                JOIN conversations c
                  ON c.id = tc.conversation_id AND c.tenant_id = tc.tenant_id
                WHERE tc.ticket_id = ? AND tc.tenant_id = ?
                ORDER BY c.updated_at DESC""",
                (ticket_id, tenant_id),
            ).fetchall()
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- create

    def create_ticket(
        self,
        tenant_id: str,
        *,
        conversation_id: str,
        subject: str,
        description: str | None = None,
        priority: str = "normal",
        actor_id: str,
    ) -> dict[str, Any]:
        """Convert a conversation into a ticket (idempotent per conversation).

        When the conversation already belongs to a ticket, that ticket is
        returned unchanged — converting twice never forks the issue. The
        conversation keeps its own status; only ``conversations.ticket_id``
        is set.
        """
        conversation = self.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        if conversation.get("ticket_id"):
            existing = self.get_ticket(tenant_id, conversation["ticket_id"])
            if existing is not None:
                return existing
        ticket_id = f"tkt_{uuid4().hex[:12]}"
        now = utc_now()
        if description is None or not description.strip():
            description = self._latest_customer_message(tenant_id, conversation_id)
        priority = "high" if priority == "high" else "normal"
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO tickets
                (id, tenant_id, subject, description, status, priority, assigned_agent,
                 customer_name, customer_ref, source_conversation_id, created_at,
                 updated_at, closed_at)
                VALUES (?, ?, ?, ?, 'open', ?, NULL, ?, ?, ?, ?, ?, NULL)""",
                (
                    ticket_id,
                    tenant_id,
                    subject.strip(),
                    description,
                    priority,
                    conversation["customer_name"],
                    conversation.get("customer_ref"),
                    conversation_id,
                    now,
                    now,
                ),
            )
            connection.execute(
                """INSERT INTO ticket_conversations
                (ticket_id, conversation_id, tenant_id, created_at)
                VALUES (?, ?, ?, ?)""",
                (ticket_id, conversation_id, tenant_id, now),
            )
            connection.execute(
                "UPDATE conversations SET ticket_id = ? WHERE tenant_id = ? AND id = ?",
                (ticket_id, tenant_id, conversation_id),
            )
        self.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "ticket.created",
            {
                "ticket_id": ticket_id,
                "subject": subject.strip(),
                "priority": priority,
            },
        )
        return self.get_ticket(tenant_id, ticket_id) or {}

    # -------------------------------------------------------------- lifecycle

    def transition_ticket(
        self,
        tenant_id: str,
        ticket_id: str,
        actor_id: str,
        new_status: str,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Move the ticket through its state machine; invalid moves raise."""
        ticket = self.get_ticket(tenant_id, ticket_id)
        if ticket is None:
            raise LookupError("Ticket not found")
        allowed = _TICKET_TRANSITIONS.get(ticket["status"], frozenset())
        if new_status not in TICKET_STATUSES:
            raise ValueError(f"Unknown ticket status: {new_status}")
        if new_status not in allowed:
            raise ValueError(f"Invalid ticket transition: {ticket['status']} -> {new_status}")
        now = utc_now()
        closed_at = now if new_status == "closed" else None
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE tickets SET status = ?, updated_at = ?, closed_at = ?
                WHERE tenant_id = ? AND id = ? AND status = ?""",
                (new_status, now, closed_at, tenant_id, ticket_id, ticket["status"]),
            )
            if cursor.rowcount != 1:
                raise ValueError("Ticket state changed before transition")
        self.audit(
            tenant_id,
            ticket["source_conversation_id"],
            actor_id,
            "ticket.transitioned",
            {
                "ticket_id": ticket_id,
                "from": ticket["status"],
                "to": new_status,
                "reason": reason,
            },
        )
        return self.get_ticket(tenant_id, ticket_id) or {}

    def update_ticket(
        self,
        tenant_id: str,
        ticket_id: str,
        fields: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any]:
        changes = {key: value for key, value in fields.items() if key in _TICKET_UPDATE_FIELDS}
        if changes:
            changes["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            with self.connect() as connection:
                cursor = connection.execute(
                    f"""UPDATE tickets SET {assignments}
                    WHERE tenant_id = ? AND id = ?""",
                    [*changes.values(), tenant_id, ticket_id],
                )
                if cursor.rowcount != 1:
                    raise LookupError("Ticket not found")
        ticket = self.get_ticket(tenant_id, ticket_id)
        if ticket is None:
            raise LookupError("Ticket not found")
        self.audit(
            tenant_id,
            ticket["source_conversation_id"],
            actor_id,
            "ticket.updated",
            {"ticket_id": ticket_id, "fields": sorted(changes)},
        )
        return ticket

    # ------------------------------------------------------------------ link

    def link_ticket_conversation(
        self,
        tenant_id: str,
        ticket_id: str,
        conversation_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        """Link another conversation to an existing ticket (cross-conversation
        tracking); the conversation's ``ticket_id`` is updated to the ticket."""
        ticket = self.get_ticket(tenant_id, ticket_id)
        if ticket is None:
            raise LookupError("Ticket not found")
        conversation = self.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT OR IGNORE INTO ticket_conversations
                (ticket_id, conversation_id, tenant_id, created_at)
                VALUES (?, ?, ?, ?)""",
                (ticket_id, conversation_id, tenant_id, now),
            )
            connection.execute(
                "UPDATE conversations SET ticket_id = ? WHERE tenant_id = ? AND id = ?",
                (ticket_id, tenant_id, conversation_id),
            )
        self.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "ticket.conversation_linked",
            {"ticket_id": ticket_id, "conversation_id": conversation_id},
        )
        return self.get_ticket(tenant_id, ticket_id) or {}

    # -------------------------------------------------------------- helpers

    def _latest_customer_message(self, tenant_id: str, conversation_id: str) -> str | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT content FROM messages
                WHERE tenant_id = ? AND conversation_id = ? AND role = 'customer'
                ORDER BY seq DESC, created_at DESC LIMIT 1""",
                (tenant_id, conversation_id),
            ).fetchone()
        return str(row["content"]) if row else None
