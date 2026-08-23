"""Migration 30: unified credential lifecycle registry (Phase 41 SEC-004) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(30, "unified credential lifecycle registry (Phase 41 SEC-004)")
def migration_30(connection: sqlite3.Connection) -> None:
    connection.executescript("""
            CREATE TABLE IF NOT EXISTS credential_registry (
                credential_id TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                status TEXT NOT NULL,
                key_ref TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                not_before TEXT NOT NULL,
                expires_at TEXT,
                last_used_at TEXT,
                retired_at TEXT,
                revoked_at TEXT,
                revoked_by TEXT,
                rotation_of TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_credential_registry_tenant
                ON credential_registry(tenant_id, type, status);
            CREATE INDEX IF NOT EXISTS idx_credential_registry_key_ref
                ON credential_registry(key_ref);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_credential_registry_type_ref
                ON credential_registry(type, key_ref);
            """)
    return
