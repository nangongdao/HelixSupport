"""Migration 23: message pagination seq index (ROADMAP 18.2d) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(23, "message pagination seq index (ROADMAP 18.2d)")
def migration_23(connection: sqlite3.Connection) -> None:
    # ROADMAP 18.2d hot-path SQL audit: ``list_messages`` paginates by
    # (created_at, seq); the pre-existing ``idx_messages_page`` stopped at
    # ``id`` (a UUID), so every message page ran a temp B-tree over the
    # conversation's rows ("RIGHT PART OF ORDER BY"). ``seq`` is monotonic
    # (rowid on SQLite, sequence on PostgreSQL), so this index lets paging run
    # straight off the index. Same DDL applies on PostgreSQL via the adapted
    # connection surface.
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'messages'"
    ).fetchone()
    if row is None:
        # Legacy databases that predate the baseline messages table (only
        # tenants/conversations) skip every messages-touching migration; this
        # index is likewise a no-op for them.
        return
    connection.execute(
        """CREATE INDEX IF NOT EXISTS idx_messages_page_seq
        ON messages(tenant_id, conversation_id, created_at ASC, seq ASC)"""
    )
