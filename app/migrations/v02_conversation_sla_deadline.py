"""Migration 2: conversation sla deadline (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(2, "conversation sla deadline")
def migration_2(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "conversations", "sla_due_at", "TEXT")
