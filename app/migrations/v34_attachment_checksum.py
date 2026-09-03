"""ROADMAP 42.4 (SEC-006): attachment content checksums.

Expand phase: adds a nullable ``sha256`` column to ``attachments`` so every
stored object's content digest is pinned in the database row and re-verified
at download time (object tampering becomes detectable). Existing rows keep
``NULL`` until rewritten; the read path treats ``NULL`` as "legacy, unverifiable"
rather than failing.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    34,
    "attachment content checksum column (ROADMAP 42.4)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE attachments ADD COLUMN sha256 TEXT")
