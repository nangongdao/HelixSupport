"""ROADMAP 42.3 (REL-002): cold-tier audit archive payloads move to the
object store.

Expand phase: adds nullable object-store reference columns to
``audit_archives``. New-style rows keep only a slim in-DB manifest
(``object_key``/``object_sha256``/``object_bytes``) while the compressed
JSONL payload lives in the configured :class:`ArchiveObjectStore`; legacy
rows keep their inline ``archive_json`` and are unaffected. No data is
copied or rewritten here — the contract migration happens in a later
release once every deployed version reads both shapes.
"""

from __future__ import annotations

import sqlite3

from app.migrations import migration


@migration(
    33,
    "audit archive object-store references (ROADMAP 42.3)",
    phase="expand",
)
def migrate(connection: sqlite3.Connection) -> None:
    connection.execute("ALTER TABLE audit_archives ADD COLUMN object_key TEXT")
    connection.execute("ALTER TABLE audit_archives ADD COLUMN object_sha256 TEXT")
    connection.execute("ALTER TABLE audit_archives ADD COLUMN object_bytes INTEGER")
