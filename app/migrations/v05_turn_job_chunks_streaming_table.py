"""Migration 5: turn_job_chunks streaming table (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(5, "turn_job_chunks streaming table")
def migration_5(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS turn_job_chunks (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            job_id TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_turn_job_chunks_job
            ON turn_job_chunks(tenant_id, job_id, seq);
        CREATE TRIGGER IF NOT EXISTS turn_job_chunks_seq_fill
        AFTER INSERT ON turn_job_chunks
        BEGIN
            UPDATE turn_job_chunks SET seq = rowid WHERE id = NEW.id;
        END;
        """
    )
