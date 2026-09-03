"""Migration 28: OIDC one-time auth transactions (M0 SEC-001) (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(28, "OIDC one-time auth transactions (M0 SEC-001)")
def migration_28(connection: sqlite3.Connection) -> None:
    connection.executescript("""
            CREATE TABLE IF NOT EXISTS auth_transactions (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                nonce TEXT NOT NULL,
                code_verifier TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                tenant_hint TEXT,
                created_at TEXT NOT NULL,
                consumed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_auth_transactions_state
                ON auth_transactions(state);
            """)
