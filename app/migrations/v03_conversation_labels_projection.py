"""Migration 3: conversation labels projection (Phase 41.6 / ARC-001 deep-module split)."""

from __future__ import annotations

import sqlite3

from app.migrations import migration
from app.migrations import _ensure_column


@migration(3, "conversation labels projection")
def migration_3(connection: sqlite3.Connection) -> None:
    _ensure_column(connection, "conversations", "labels_json", "TEXT NOT NULL DEFAULT '[]'")
