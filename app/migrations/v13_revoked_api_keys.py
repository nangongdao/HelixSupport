"""Migration 13: revoked api keys (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(13, "revoked api keys")
def migration_13(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS revoked_api_keys (
            credential_id TEXT PRIMARY KEY,
            revoked_at TEXT NOT NULL,
            revoked_by TEXT NOT NULL
        );
        """
    )
