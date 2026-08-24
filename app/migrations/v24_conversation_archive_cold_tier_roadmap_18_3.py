"""Migration 24: conversation archive cold tier (ROADMAP 18.3) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(24, "conversation archive cold tier (ROADMAP 18.3)")
def migration_24(connection: sqlite3.Connection) -> None:
    """Create the read-only archive tables for resolved conversations.

    ROADMAP 18.3: resolved conversations closed more than N days ago are moved
    out of the hot tables into ``conversations_archive`` /
    ``messages_archive`` / ``conversation_labels_archive`` by the turn worker,
    so the queue indexes and the message FTS mirror stay bounded.  The archive
    side carries no foreign keys, triggers, or FTS mirror: it is a cold,
    immutable snapshot, and the write path stays hot-only.  Same DDL applies
    on PostgreSQL via the adapted connection surface.
    """
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS conversations_archive (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            customer_name TEXT NOT NULL,
            customer_ref TEXT,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            intent TEXT,
            assigned_agent TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            handoff_reason TEXT,
            sla_due_at TEXT,
            last_confidence REAL,
            version INTEGER NOT NULL DEFAULT 1,
            preview TEXT,
            message_count INTEGER NOT NULL DEFAULT 0,
            last_message_at TEXT,
            labels_json TEXT NOT NULL DEFAULT '[]',
            claimed_by TEXT,
            claimed_at TEXT,
            claim_expires_at TEXT,
            needs_response INTEGER NOT NULL DEFAULT 0,
            waiting_since TEXT,
            first_response_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT,
            archived_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_conversations_archive_tenant_updated
            ON conversations_archive(tenant_id, updated_at DESC);
        CREATE TABLE IF NOT EXISTS messages_archive (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            turn_id TEXT,
            role TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            seq INTEGER NOT NULL DEFAULT 0,
            channel_message_id TEXT,
            reply_to TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_messages_archive_conversation
            ON messages_archive(tenant_id, conversation_id, created_at);
        CREATE TABLE IF NOT EXISTS conversation_labels_archive (
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            label TEXT NOT NULL,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (tenant_id, conversation_id, label)
        );
        CREATE INDEX IF NOT EXISTS idx_conversation_labels_archive_lookup
            ON conversation_labels_archive(tenant_id, label, conversation_id);
        CREATE TABLE IF NOT EXISTS feedback_archive (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            actor TEXT NOT NULL,
            rating INTEGER NOT NULL,
            reason TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (tenant_id, message_id, actor)
        );
        """
    )
