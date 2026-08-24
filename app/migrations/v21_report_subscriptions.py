"""Migration 21: report subscriptions (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(21, "report subscriptions")
def migration_21(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS report_subscriptions (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            report_type TEXT NOT NULL,
            schedule TEXT NOT NULL DEFAULT 'daily',
            window_days INTEGER NOT NULL DEFAULT 7,
            webhook_endpoint_id TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_run_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_report_subscriptions_due
            ON report_subscriptions(active, schedule, last_run_at);
        CREATE INDEX IF NOT EXISTS idx_report_subscriptions_tenant
            ON report_subscriptions(tenant_id, report_type);
        """
    )
