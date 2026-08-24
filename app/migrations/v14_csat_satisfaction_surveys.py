"""Migration 14: csat satisfaction surveys (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(14, "csat satisfaction surveys")
def migration_14(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS csat_surveys (
            token TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            rating INTEGER,
            created_at TEXT NOT NULL,
            responded_at TEXT,
            expires_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_csat_conversation
            ON csat_surveys(tenant_id, conversation_id);
        """
    )
