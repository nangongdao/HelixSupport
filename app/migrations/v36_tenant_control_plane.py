"""ROADMAP 43.1: tenant control-plane policy store.

Expand phase: adds the durable, versioned policy table the control plane
writes and the data plane reads as its last-known-good store. One row per
(policy version, tenant); the signed snapshot document is reconstructible
from these columns alone.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    36,
    "tenant control plane policy versions (ROADMAP 43.1)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS tenant_control_policies (
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            version INTEGER NOT NULL,
            plan TEXT NOT NULL,
            region TEXT NOT NULL,
            deployment_cell TEXT NOT NULL,
            features_json TEXT NOT NULL DEFAULT '[]',
            model_policy_json TEXT NOT NULL DEFAULT '{}',
            credential_reference TEXT,
            issued_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            signature TEXT NOT NULL,
            PRIMARY KEY (tenant_id, version)
        )
        """
    )
