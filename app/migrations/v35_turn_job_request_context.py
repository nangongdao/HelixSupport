"""ROADMAP 42.6: unified trace context on turn jobs.

Expand phase: adds a nullable ``request_id`` column to ``turn_jobs`` so the
correlation chain request → job → audit → webhook is queryable by one id.
The worker re-applies the originating request id into its context when it
claims the job, so every audit event and outbound webhook emitted while
processing carries the same correlation id as the customer-facing request.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    35,
    "turn job trace context column (ROADMAP 42.6)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    # Legacy/partial fixtures may not carry the hot tables at all; the
    # framework's convention (see _ensure_column) is to tolerate their
    # absence rather than fail the whole chain.
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "turn_jobs" not in tables:
        return
    connection.execute("ALTER TABLE turn_jobs ADD COLUMN request_id TEXT")
