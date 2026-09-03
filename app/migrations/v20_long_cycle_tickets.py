"""Migration 20: long-cycle tickets (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(20, "long-cycle tickets")
def migration_20(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tickets (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            subject TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'open',
            priority TEXT NOT NULL DEFAULT 'normal',
            assigned_agent TEXT,
            customer_name TEXT NOT NULL,
            customer_ref TEXT,
            source_conversation_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_tickets_tenant_updated
            ON tickets(tenant_id, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_tickets_tenant_status
            ON tickets(tenant_id, status);
        CREATE TABLE IF NOT EXISTS ticket_conversations (
            ticket_id TEXT NOT NULL REFERENCES tickets(id),
            conversation_id TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (ticket_id, conversation_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ticket_conversations_conv
            ON ticket_conversations(conversation_id);
        """
    )
    _ensure_column(connection, "conversations", "ticket_id", "TEXT")
