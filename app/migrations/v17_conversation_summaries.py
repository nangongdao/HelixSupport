"""Migration 17: conversation summaries (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(17, "conversation summaries")
def migration_17(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversation_summaries (
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            content TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'rule',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, conversation_id, kind)
        );
        CREATE INDEX IF NOT EXISTS idx_conversation_summaries_lookup
            ON conversation_summaries(tenant_id, conversation_id);
        """
    )
