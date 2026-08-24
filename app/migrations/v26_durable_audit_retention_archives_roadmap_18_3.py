"""Migration 26: durable audit retention archives (ROADMAP 18.3) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(26, "durable audit retention archives (ROADMAP 18.3)")
def migration_26(connection: sqlite3.Connection) -> None:
    """Create durable, hash-addressed audit export manifests.

    Retention enforcement writes expired audit rows into this immutable JSON
    archive before deleting the hot rows.  The archive keeps the original
    monotonic sequence and hash-chain fields so verification can merge cold
    and hot rows back into one chain.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS audit_archives (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            cutoff TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            first_seq INTEGER NOT NULL,
            last_seq INTEGER NOT NULL,
            first_event_hash TEXT NOT NULL,
            last_event_hash TEXT NOT NULL,
            archive_json TEXT NOT NULL,
            content_sha256 TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_archives_tenant_created
            ON audit_archives(tenant_id, created_at DESC, id DESC);
        CREATE INDEX IF NOT EXISTS idx_audit_archives_seq
            ON audit_archives(first_seq, last_seq);
        """
    )
