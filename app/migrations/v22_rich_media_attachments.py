"""Migration 22: rich-media attachments (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(22, "rich-media attachments")
def migration_22(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS attachments (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            conversation_id TEXT NOT NULL,
            message_id TEXT,
            filename TEXT NOT NULL,
            content_type TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            storage_key TEXT NOT NULL UNIQUE,
            uploader TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'stored',
            scanned INTEGER NOT NULL DEFAULT 0,
            verdict TEXT NOT NULL DEFAULT 'clean',
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_attachments_conversation
            ON attachments(tenant_id, conversation_id, created_at);
        CREATE INDEX IF NOT EXISTS idx_attachments_tenant_quota
            ON attachments(tenant_id, status, size_bytes);
        """
    )
