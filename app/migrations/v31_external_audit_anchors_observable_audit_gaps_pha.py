"""Migration 31: external audit anchors + observable audit gaps (Phase 41.3 SEC-005) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(31, "external audit anchors + observable audit gaps (Phase 41.3 SEC-005)")
def migration_31(connection: sqlite3.Connection) -> None:
    connection.executescript("""
            CREATE TABLE IF NOT EXISTS audit_anchors (
                anchor_id TEXT PRIMARY KEY,
                seq INTEGER NOT NULL,
                chain_hash TEXT NOT NULL,
                event_type TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_audit_anchors_seq
                ON audit_anchors(seq);
            CREATE INDEX IF NOT EXISTS idx_audit_anchors_event
                ON audit_anchors(event_type, created_at);
    
            CREATE TABLE IF NOT EXISTS audit_gaps (
                event_type TEXT PRIMARY KEY,
                gap_count INTEGER NOT NULL,
                last_seen_at TEXT NOT NULL
            );
            """)
