"""Migration 7: tenant model policy and daily usage (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column, migration


@migration(7, "tenant model policy and daily usage")
def migration_7(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "tenants", "allowed_models_json", "TEXT")
    _ensure_column(connection, "tenants", "daily_turn_budget", "INTEGER")
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS tenant_usage_daily (
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, date)
        );
        """
    )
