"""Migration 25: csat summary partial index (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(25, "csat summary partial index")
def migration_25(connection: sqlite3.Connection) -> None:
    """Partial index backing the admin CSAT summary card.

    ``summarize_csat`` filters ``rating IS NOT NULL`` on every read; with the
    base table growing from unresolved survey tokens, that scan degrades as
    the tenant accumulates history. This partial index covers both the
    overall aggregate and the per-day trend (tenant + responded_at date +
    rating), so the summary card stays constant-time as unresolved tokens
    accumulate. Idempotent — ``CREATE INDEX IF NOT EXISTS``.
    """
    connection.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_csat_summary_tenant_responded
            ON csat_surveys(tenant_id, substr(responded_at, 1, 10))
            WHERE rating IS NOT NULL;
        """
    )
