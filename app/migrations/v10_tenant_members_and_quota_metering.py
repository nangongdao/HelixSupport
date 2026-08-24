"""Migration 10: tenant members and quota metering (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(10, "tenant members and quota metering")
def migration_10(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "tenants", "conversation_quota", "INTEGER")
    _ensure_column(connection, "tenants", "storage_quota_bytes", "INTEGER")
    _ensure_column(
        connection, "tenant_usage_daily", "conversation_count", "INTEGER NOT NULL DEFAULT 0"
    )
    _ensure_column(connection, "tenant_usage_daily", "message_count", "INTEGER NOT NULL DEFAULT 0")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenant_members (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            actor_id TEXT NOT NULL,
            role TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            invited_by TEXT,
            invited_at TEXT,
            updated_at TEXT NOT NULL,
            last_login_at TEXT,
            UNIQUE (tenant_id, actor_id)
        );
        CREATE INDEX IF NOT EXISTS idx_tenant_members_tenant
            ON tenant_members(tenant_id, status);
        """
    )
