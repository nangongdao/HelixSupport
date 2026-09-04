"""Persistence for signed inbound channel threads and message receipts."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
from typing import Any
from uuid import uuid4

from app.db._util import utc_after, utc_now
from app.domain import ConversationStatus


class DatabaseChannelsMixin:
    def get_channel_conversation(
        self, tenant_id: str, account_id: str, external_thread_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT c.* FROM channel_threads ct
                JOIN conversations c
                  ON c.id = ct.conversation_id AND c.tenant_id = ct.tenant_id
                WHERE ct.tenant_id = ? AND ct.account_id = ?
                  AND ct.external_thread_id = ?""",
                (tenant_id, account_id, external_thread_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def get_or_create_channel_conversation(
        self,
        tenant_id: str,
        account_id: str,
        external_thread_id: str,
        customer_name: str,
        customer_ref: str,
        channel: str,
        actor: str,
        sla_minutes: int,
    ) -> tuple[dict[str, Any], bool]:
        """Atomically map one external thread to exactly one conversation."""
        conversation_id = f"conv_{uuid4().hex[:12]}"
        now = utc_now()
        effective_sla = self.resolve_sla_policy(tenant_id, "normal", channel, sla_minutes)
        created = False
        with self.connect() as connection:
            # SQLite takes its write lock before the read; PostgreSQL translates
            # this to the shared transaction advisory lock.
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT c.* FROM channel_threads ct
                JOIN conversations c
                  ON c.id = ct.conversation_id AND c.tenant_id = ct.tenant_id
                WHERE ct.tenant_id = ? AND ct.account_id = ?
                  AND ct.external_thread_id = ?""",
                (tenant_id, account_id, external_thread_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO conversations
                    (id, tenant_id, customer_name, customer_ref, channel, status, priority,
                     sla_due_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)""",
                    (
                        conversation_id,
                        tenant_id,
                        customer_name,
                        customer_ref,
                        channel,
                        ConversationStatus.OPEN,
                        utc_after(effective_sla),
                        now,
                        now,
                    ),
                )
                connection.execute(
                    """INSERT INTO channel_threads
                    (tenant_id, account_id, external_thread_id, conversation_id,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (tenant_id, account_id, external_thread_id, conversation_id, now, now),
                )
                row = connection.execute(
                    "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                    (tenant_id, conversation_id),
                ).fetchone()
                created = True

        if created:
            self._invalidate_dashboard(tenant_id)
            self.increment_tenant_usage_conversations(tenant_id, now[:10])
            self.audit(
                tenant_id,
                conversation_id,
                actor,
                "conversation.created",
                {"channel": channel, "customer_verified": True},
            )
        conversation = self._conversation_from_row(row)
        if conversation is None:
            raise RuntimeError("Channel conversation could not be read back")
        return conversation, created

    def get_channel_webhook_receipt(
        self, tenant_id: str, account_id: str, message_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM channel_webhook_receipts
                WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                (tenant_id, account_id, message_id),
            ).fetchone()
        return dict(row) if row else None

    def claim_channel_webhook_receipt(
        self,
        tenant_id: str,
        account_id: str,
        message_id: str,
        external_thread_id: str,
        conversation_id: str,
        body_sha256: str,
    ) -> tuple[dict[str, Any], bool]:
        """Claim an account-wide message id, returning the winner on replay."""
        created = False
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT * FROM channel_webhook_receipts
                WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                (tenant_id, account_id, message_id),
            ).fetchone()
            if row is None:
                connection.execute(
                    """INSERT INTO channel_webhook_receipts
                    (tenant_id, account_id, message_id, external_thread_id,
                     conversation_id, content_sha256, received_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        tenant_id,
                        account_id,
                        message_id,
                        external_thread_id,
                        conversation_id,
                        body_sha256,
                        utc_now(),
                    ),
                )
                row = connection.execute(
                    """SELECT * FROM channel_webhook_receipts
                    WHERE tenant_id = ? AND account_id = ? AND message_id = ?""",
                    (tenant_id, account_id, message_id),
                ).fetchone()
                created = True
        if row is None:
            raise RuntimeError("Channel webhook receipt could not be read back")
        return dict(row), created
