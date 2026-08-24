"""Migration 9: quality aggregates and knowledge lifecycle (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(9, "quality aggregates and knowledge lifecycle")
def migration_9(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS quality_daily (
            tenant_id TEXT NOT NULL,
            date TEXT NOT NULL,
            intent TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            turn_count INTEGER NOT NULL DEFAULT 0,
            escalation_count INTEGER NOT NULL DEFAULT 0,
            negative_feedback_count INTEGER NOT NULL DEFAULT 0,
            first_response_sum_seconds REAL NOT NULL DEFAULT 0,
            first_response_samples INTEGER NOT NULL DEFAULT 0,
            latency_sum_ms INTEGER NOT NULL DEFAULT 0,
            estimated_tokens INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, date, intent, prompt_version)
        );
        CREATE INDEX IF NOT EXISTS idx_quality_daily_tenant_date
            ON quality_daily(tenant_id, date);
        """
    )
    # Knowledge lifecycle status columns (Phase 21.3).
    _ensure_column(connection, "knowledge_articles", "status", "TEXT NOT NULL DEFAULT 'published'")
    _ensure_column(connection, "knowledge_articles", "reviewed_by", "TEXT")
    _ensure_column(connection, "knowledge_articles", "reviewed_at", "TEXT")
