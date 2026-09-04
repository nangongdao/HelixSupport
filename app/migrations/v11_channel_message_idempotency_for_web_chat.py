"""Migration 11: channel message idempotency for web chat (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(11, "channel message idempotency for web chat")
def migration_11(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "messages", "channel_message_id", "TEXT")
    # A channel message id is unique per (tenant, conversation) so replaying
    # the same widget/webhook message can never create a second turn.
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "messages" in tables:
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_messages_channel_dedup
            ON messages(tenant_id, conversation_id, channel_message_id)
            WHERE channel_message_id IS NOT NULL
            """
        )
