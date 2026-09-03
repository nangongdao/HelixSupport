"""Migration drill (Phase 30.6): migrate a legacy database snapshot forward.

Simulates a release upgrade: build a database with the schema state of a
previous version (via ``Database.initialize`` legacy path or a minimal legacy
schema), run the full migration chain, and verify the schema version advances
and the migration chain stays contiguous.

Usage:
    python scripts/migration_drill.py [--db PATH]

Exit 0 on success; non-zero if any migration fails or the chain is broken.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

# Same bootstrap as the other repo-root importers (frontend_gate, visual_gate,
# readme_screenshots, …). Without it a direct `python scripts/migration_drill.py` — the
# invocation the docs and runbooks document — dies on `No module named 'app'`
# unless the package happens to be installed editable, which only CI does.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.migrations import (
    all_migrations,
    migration_schema_version,
    run_migrations,
    verify_migration_chain,
)
from scripts._console import use_utf8_console


def build_legacy_snapshot(path: Path) -> None:
    """Create a minimal legacy database (pre-migration framework schema)."""
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE tenants (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE conversations (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            customer_name TEXT NOT NULL,
            customer_ref TEXT,
            channel TEXT NOT NULL,
            status TEXT NOT NULL,
            intent TEXT,
            assigned_agent TEXT,
            priority TEXT NOT NULL DEFAULT 'normal',
            handoff_reason TEXT,
            sla_due_at TEXT,
            last_confidence REAL,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            resolved_at TEXT
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            tenant_id TEXT NOT NULL REFERENCES tenants(id),
            conversation_id TEXT NOT NULL REFERENCES conversations(id),
            turn_id TEXT,
            role TEXT NOT NULL,
            author TEXT NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL
        );
        INSERT INTO tenants VALUES ('demo', 'Legacy', '2026-01-01T00:00:00+00:00');
        """
    )
    connection.commit()
    connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="data/migration-drill.db")
    parser.add_argument(
        "--force",
        action="store_true",
        help="allow overwriting an existing database at --db (data-loss guard)",
    )
    args = parser.parse_args()
    path = Path(args.db)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Phase 30.6 guard: never destroy an existing database by default —
        # pointing the drill at a real/backup DB is a data-loss footgun.
        if not args.force:
            print(
                f"refusing to overwrite existing database {path} (pass --force to "
                "acknowledge data loss, or use a scratch path)",
                file=sys.stderr,
            )
            return 2
        path.unlink()

    chain_problems = verify_migration_chain(all_migrations())
    if chain_problems:
        print(f"FAIL: migration chain problems: {chain_problems}", file=sys.stderr)
        return 1

    build_legacy_snapshot(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        before = migration_schema_version(connection)
        result = run_migrations(connection, all_migrations())
        after = migration_schema_version(connection)
        connection.commit()
    finally:
        connection.close()

    expected = max(m.version for m in all_migrations())
    print(
        f"migration drill: legacy schema version {before} -> {after} "
        f"(applied {len(result.applied)}, skipped {len(result.skipped)})"
    )
    if after != expected:
        print(f"FAIL: schema version {after} != expected {expected}", file=sys.stderr)
        return 1
    print("migration drill passed")
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
