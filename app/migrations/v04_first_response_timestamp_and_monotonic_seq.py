"""Migration 4: first response timestamp and monotonic seq (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column
from app.migrations import _create_seq_trigger_if_table_exists


@migration(4, "first response timestamp and monotonic seq")
def migration_4(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "conversations", "first_response_at", "TEXT")
    _ensure_column(connection, "messages", "seq", "INTEGER NOT NULL DEFAULT 0")
    _ensure_column(connection, "audit_events", "seq", "INTEGER NOT NULL DEFAULT 0")
    # Backfill only when the table exists (legacy fixtures may be partial).
    for table in ("messages", "audit_events"):
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if table not in tables:
            continue
        connection.execute(f"UPDATE {table} SET seq = rowid WHERE seq = 0")
    _create_seq_trigger_if_table_exists(connection, "messages", "messages_seq_fill")
    _create_seq_trigger_if_table_exists(connection, "audit_events", "audit_events_seq_fill")
