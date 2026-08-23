"""ROADMAP 43.4: tenant residency column on the tenants table.

Expand phase: the control plane has carried ``region`` inside signed policy
snapshots since migration v36, but the tenant row itself stayed region-blind,
so provisioning, quota reads and backup manifests could not answer "where
does this tenant's data live" without joining the policy store. A plain
``region`` column on ``tenants`` records the residency fixed at creation
time; it defaults to ``local`` to match :data:`app.control_plane.TenantPolicy`
and existing rows keep that default unchanged.
"""

from __future__ import annotations

import sqlite3

from app.migrations import _ensure_column
from app.migrations import migration


@migration(
    40,
    "tenant residency column (ROADMAP 43.4)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "tenants", "region", "TEXT NOT NULL DEFAULT 'local'")
