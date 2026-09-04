"""ROADMAP 2.2.2: Cross-region async replication log (expand phase).

Adds a durable replication log backing eventual consistency between regions:

- ``replication_log`` — one row per recorded data change (insert/update/delete),
  with the target region, the source region, and a pending marker
  (``replicated_at IS NULL``) that the replication worker drains.

This is expand-only: no existing table is modified, and replication is opt-in
via the cell-registry configuration.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    43,
    "Replication log: cross-region async replication tracking (ROADMAP 2.2.2)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS replication_log (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            source_region TEXT NOT NULL,
            target_region TEXT NOT NULL,
            table_name TEXT NOT NULL,
            row_id TEXT NOT NULL,
            operation TEXT NOT NULL,
            payload_json TEXT,
            created_at TEXT NOT NULL,
            replicated_at TEXT
        )
        """
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_replication_pending "
        "ON replication_log(target_region, replicated_at) "
        "WHERE replicated_at IS NULL"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_replication_tenant_table "
        "ON replication_log(tenant_id, table_name, created_at)"
    )