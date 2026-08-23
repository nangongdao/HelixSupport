"""Migration 6: prompt version registry (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(6, "prompt version registry")
def migration_6(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS prompt_versions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT,
            name TEXT NOT NULL,
            version TEXT NOT NULL,
            body TEXT NOT NULL,
            model_ref TEXT,
            status TEXT NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            activated_at TEXT,
            UNIQUE (tenant_id, name, version)
        );
        CREATE INDEX IF NOT EXISTS idx_prompt_versions_lookup
            ON prompt_versions(tenant_id, name, status);
        """
    )
