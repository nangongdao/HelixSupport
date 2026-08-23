"""Migration 18: operator collaboration mentions and threads (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(18, "operator collaboration mentions and threads")
def migration_18(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_mentions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            note_id TEXT NOT NULL,
            mentioned_actor TEXT NOT NULL,
            mentioned_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            read_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_mentions_inbox
            ON conversation_mentions(tenant_id, mentioned_actor, read_at);
        CREATE INDEX IF NOT EXISTS idx_mentions_conversation
            ON conversation_mentions(tenant_id, conversation_id, note_id);
        """
    )
    _ensure_column(connection, "messages", "reply_to", "TEXT")
