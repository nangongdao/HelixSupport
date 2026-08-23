"""Migration 16: sla policy engine (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(16, "sla policy engine")
def migration_16(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS sla_policies (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            priority TEXT,
            channel TEXT,
            first_response_minutes INTEGER NOT NULL,
            resolve_minutes INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_sla_policies_lookup
            ON sla_policies(tenant_id, priority, channel);
        """
    )
