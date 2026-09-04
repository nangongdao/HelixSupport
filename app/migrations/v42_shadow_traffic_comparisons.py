"""ROADMAP 2.1.x: Shadow traffic comparison tracking (expand phase).

Captures side-by-side comparison results when v1 requests are shadowed to v2,
recording status codes, field-level diffs, and latency for automated monitoring
of API v2 correctness and performance.

- ``shadow_traffic_comparisons`` — one row per shadowed request, with matched/
  mismatched field arrays, latencies, and the sampling rate used.

This is expand-only: no existing table is modified, and the shadow mechanism
is opt-in via ``SHADOW_TRAFFIC_ENABLED=true``.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    42,
    "Shadow traffic comparisons: v1/v2 field diff and latency tracking (ROADMAP 2.1.x)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS shadow_traffic_comparisons (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            request_id TEXT NOT NULL,
            route TEXT NOT NULL,
            v1_status_code INTEGER,
            v2_status_code INTEGER,
            fields_matched TEXT NOT NULL DEFAULT '[]',
            fields_mismatched TEXT NOT NULL DEFAULT '[]',
            v1_latency_ms INTEGER,
            v2_latency_ms INTEGER,
            sampling_rate REAL NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_comparisons_tenant_route "
        "ON shadow_traffic_comparisons(tenant_id, route, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_shadow_comparisons_request "
        "ON shadow_traffic_comparisons(request_id)"
    )
