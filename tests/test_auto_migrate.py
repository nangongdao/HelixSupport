"""Phase 42.2 / REL-001: DATABASE_AUTO_MIGRATE — no DDL at app startup.

Production web/worker processes run with a least-privilege DB role: startup
must not execute DDL. With ``DATABASE_AUTO_MIGRATE=false`` the schema is
provisioned by the standalone release job (``scripts/run_migrations.py``)
and app startup only verifies readiness against ``schema_migrations``,
failing fast on a stale or unmigrated database instead of silently
migrating (or serving from one).
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.migrations import all_migrations, pending_migration_versions


def _settings(db: Path, auto_migrate: bool) -> Settings:
    return Settings(
        database_path=db,
        auth_mode="api_key",
        api_keys_json=json.dumps(
            {"admin-key-0123456789": {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
        ),
        rate_limit_per_minute=20000,
        docs_enabled=False,
        database_auto_migrate=auto_migrate,
    )


class AutoMigrateConfigTests(unittest.TestCase):
    def test_default_is_true(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            settings = Settings(database_path=Path(tmp) / "x.db", auth_mode="api_key")
            self.assertTrue(settings.database_auto_migrate)

    def test_env_disables_auto_migrate(self) -> None:
        os.environ["DATABASE_AUTO_MIGRATE"] = "false"
        try:
            settings = Settings.from_env()
            self.assertFalse(settings.database_auto_migrate)
        finally:
            del os.environ["DATABASE_AUTO_MIGRATE"]


class AutoMigrateAppTests(unittest.TestCase):
    def setUp(self) -> None:
        # Fail-fast tests intentionally leak the constructed Database pool
        # (create_app raises before returning it), which keeps file handles
        # open on Windows; cleanup errors are tolerated.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _db(self) -> Path:
        return Path(self._tmp.name) / f"{self._testMethodName}.db"

    def test_default_initializes_fresh_database(self) -> None:
        db = self._db()
        app = create_app(_settings(db, auto_migrate=True))
        client = TestClient(app)
        services = app.state.services
        try:
            client.__enter__()
            ready = client.get("/health/ready").json()
            self.assertEqual(ready["status"], "ready")
            connection = sqlite3.connect(db)
            try:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    )
                }
            finally:
                connection.close()
            self.assertIn("schema_migrations", tables)
        finally:
            client.__exit__(None, None, None)
            services.database.close()

    def test_auto_migrate_false_boots_on_current_schema(self) -> None:
        db = self._db()
        # The release job provisioned the schema beforehand.
        database = Database(db)
        try:
            database.initialize()
        finally:
            database.close()

        app = create_app(_settings(db, auto_migrate=False))
        client = TestClient(app)
        services = app.state.services
        try:
            client.__enter__()
            ready = client.get("/health/ready").json()
            self.assertEqual(ready["status"], "ready")
        finally:
            client.__exit__(None, None, None)
            services.database.close()

    def test_auto_migrate_false_fails_fast_on_unmigrated_database(self) -> None:
        db = self._db()
        with self.assertRaises(RuntimeError) as ctx:
            create_app(_settings(db, auto_migrate=False))
        message = str(ctx.exception)
        self.assertIn("DATABASE_AUTO_MIGRATE=false", message)
        self.assertIn("run_migrations", message)
        # Every version is pending on a fresh file.
        self.assertIn(str(all_migrations()[-1].version), message)

    def test_auto_migrate_false_fails_fast_on_stale_schema(self) -> None:
        db = self._db()
        database = Database(db)
        try:
            database.initialize()
        finally:
            database.close()
        # Simulate an older release job: forget the newest migration.
        with sqlite3.connect(db) as connection:
            newest = all_migrations()[-1].version
            connection.execute("DELETE FROM schema_migrations WHERE version = ?", (newest,))
            # And drop its table footprint so the state is genuinely stale.
            connection.execute("DROP TABLE IF EXISTS deferred_deletion_jobs")

        with self.assertRaises(RuntimeError) as ctx:
            create_app(_settings(db, auto_migrate=False))
        self.assertIn(str(newest), str(ctx.exception))


class PendingVersionsTests(unittest.TestCase):
    def test_pending_on_fresh_connection_is_all_versions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            connection = sqlite3.connect(Path(tmp) / "fresh.db")
            try:
                connection.row_factory = sqlite3.Row
                pending = pending_migration_versions(connection, all_migrations())
                self.assertEqual(pending, [m.version for m in all_migrations()])
            finally:
                connection.close()

    def test_pending_after_initialize_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "init.db")
            try:
                db.initialize()
                with db.connect() as connection:
                    self.assertEqual(pending_migration_versions(connection), [])
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
