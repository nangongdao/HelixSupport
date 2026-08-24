"""Migration 12: audit hash chain (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(12, "audit hash chain")
def migration_12(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "audit_events", "prev_hash", "TEXT")
    _ensure_column(connection, "audit_events", "event_hash", "TEXT")
    # Backfill the chain for rows written before this migration: compute each
    # event's hash (with the running prev_hash) in seq order, so upgrading a
    # non-empty audit table does not leave the chain permanently "tampered".
    from app.audit_chain import event_hash

    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "audit_events" not in tables:
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()}
    if "event_hash" not in columns:
        return
    prev = ""
    rows = connection.execute(
        "SELECT id, tenant_id, conversation_id, request_id, actor, event_type, "
        "payload_json, created_at, event_hash FROM audit_events "
        "ORDER BY seq ASC, rowid ASC"
    ).fetchall()
    for row in rows:
        if row["event_hash"]:
            # Already chained (either from a prior run or post-upgrade writes);
            # advance prev and keep going.
            prev = row["event_hash"]
            continue
        digest = event_hash(
            prev_hash=prev,
            event_id=row["id"],
            tenant_id=row["tenant_id"],
            conversation_id=row["conversation_id"],
            request_id=row["request_id"],
            actor=row["actor"],
            event_type=row["event_type"],
            payload_json=row["payload_json"],
            created_at=row["created_at"],
        )
        connection.execute(
            "UPDATE audit_events SET prev_hash = ?, event_hash = ? WHERE id = ?",
            (prev, digest, row["id"]),
        )
        prev = digest
