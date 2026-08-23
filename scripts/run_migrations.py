#!/usr/bin/env python3
"""42.2 HA/PITR: standalone migration release job.

Runs database schema migrations as a dedicated release step so application
processes can start with ``DATABASE_AUTO_MIGRATE=false`` and a least-
privilege DB role (no CREATE/ALTER). Web/worker startup then only verifies
readiness against ``schema_migrations`` and fails fast when the schema is
stale instead of executing DDL on boot.

Apply mode executes the full idempotent initialization (baseline schema,
versioned migrations, FTS indexes); verify-only mode opens the database
without running DDL and reports drift.

Usage:
    python scripts/run_migrations.py                       # apply (sqlite)
    python scripts/run_migrations.py --verify-only         # no-DDL check
    python scripts/run_migrations.py --database PATH       # explicit sqlite db
    python scripts/run_migrations.py --database-url URL    # postgresql backend
    python scripts/run_migrations.py --json                # machine report

Exit codes: 0 = schema current (or applied cleanly), 1 = failure or pending
migrations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("helix")


def _report(
    backend: str,
    target: str,
    verify_only: bool,
    before: int,
    after: int,
    pending: list[int],
) -> dict[str, Any]:
    from app.migrations import all_migrations

    versions = [m.version for m in all_migrations()]
    applied = [v for v in versions if before < v <= after]
    return {
        "backend": backend,
        "target": target,
        "verify_only": verify_only,
        "before_version": before,
        "after_version": after,
        "applied": applied,
        "pending": pending,
        "ok": not pending,
    }


def _sqlite_verify(path: Path) -> dict[str, Any]:
    from app.migrations import (
        all_migrations,
        migration_schema_version,
        pending_migration_versions,
    )

    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        connection.row_factory = sqlite3.Row
        try:
            current = migration_schema_version(connection)
            pending = pending_migration_versions(connection, all_migrations())
        except sqlite3.OperationalError:
            # Unmigrated database: the schema_migrations table itself is absent.
            current = 0
            pending = [m.version for m in all_migrations()]
        return _report("sqlite", str(path), True, current, current, pending)
    finally:
        connection.close()


def _sqlite_apply(path: Path) -> dict[str, Any]:
    from app.database import Database
    from app.migrations import migration_schema_version, pending_migration_versions

    database = Database(path)
    try:
        with database.connect() as connection:
            before = migration_schema_version(connection)
        database.initialize()
        with database.connect() as connection:
            after = migration_schema_version(connection)
            pending = pending_migration_versions(connection)
        return _report("sqlite", str(path), False, before, after, pending)
    finally:
        database.close()


def _pg_state(database: Any) -> tuple[int, list[int]]:
    from app.migrations import migration_schema_version, pending_migration_versions

    with database.connect() as connection:
        pending = pending_migration_versions(connection)
        try:
            current = migration_schema_version(connection)
        except Exception:
            current = 0
    return current, pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--database", help="SQLite database path (default DATABASE_PATH)")
    group.add_argument("--database-url", help="PostgreSQL URL (default DATABASE_URL)")
    parser.add_argument(
        "--verify-only", action="store_true", help="No-DDL readiness check"
    )
    parser.add_argument("--json", action="store_true", help="Print the machine-readable report")
    args = parser.parse_args()

    from app.config import Settings

    settings = Settings.from_env()
    verify_only = args.verify_only

    try:
        if args.database_url or (not args.database and settings.database_backend == "postgresql"):
            from app.postgres_db import create_postgres_database

            url = args.database_url or settings.database_url
            if verify_only:
                database = create_postgres_database(url, auto_migrate=False)
                try:
                    current, pending = _pg_state(database)
                finally:
                    database.close()
                report = _report("postgresql", url, True, current, current, pending)
            else:
                probe = create_postgres_database(url, auto_migrate=False)
                try:
                    before, pending_before = _pg_state(probe)
                finally:
                    probe.close()
                if pending_before:
                    database = create_postgres_database(url)
                    try:
                        after, pending = _pg_state(database)
                    finally:
                        database.close()
                else:
                    after, pending = before, []
                report = _report("postgresql", url, False, before, after, pending)
        else:
            path = Path(args.database or settings.database_path)
            report = _sqlite_verify(path) if verify_only else _sqlite_apply(path)
    except Exception as exc:
        logger.error("migration job failed: %s", exc)
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}))
        return 1

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        status = "OK" if report["ok"] else "PENDING"
        logger.info(
            "migration job %s: backend=%s target=%s verify_only=%s version=%s applied=%s pending=%s",
            status,
            report["backend"],
            report["target"],
            report["verify_only"],
            report["after_version"],
            report["applied"],
            report["pending"],
        )

    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())