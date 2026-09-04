"""ROADMAP 43.3: transactional outbox and API idempotency store.

Expand phase: two additive tables.

``domain_events`` is the transactional outbox — business writes and their
versioned domain events commit in the same transaction; a dispatcher later
marks rows published. Consumers deduplicate on ``event_id``.

``api_idempotency`` stores request-key → resource mappings so ``/api/v2``
writes honour ``Idempotency-Key`` replays without duplicating resources.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    37,
    "domain event outbox + api idempotency keys (ROADMAP 43.3)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS domain_events (
            event_id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            published_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_domain_events_unpublished "
        "ON domain_events (created_at) WHERE published_at IS NULL"
    )
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS api_idempotency (
            scope TEXT NOT NULL,
            tenant_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            resource_id TEXT NOT NULL,
            status_code INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (scope, tenant_id, idempotency_key)
        )
        """
    )
