from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.database import Database


class DatabaseMigrationTests(unittest.TestCase):
    def test_040_database_backfills_queue_summaries_labels_and_message_fts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "legacy.db"
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
                INSERT INTO tenants VALUES ('demo', 'Legacy Tenant', '2026-01-01T00:00:00+00:00');
                INSERT INTO conversations (
                    id, tenant_id, customer_name, channel, status, priority,
                    created_at, updated_at
                ) VALUES (
                    'conv_legacy_001', 'demo', 'Legacy Customer', 'web', 'open', 'normal',
                    '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00'
                );
                INSERT INTO messages (
                    id, tenant_id, conversation_id, role, author, content,
                    metadata_json, created_at
                ) VALUES
                    ('msg_legacy_001', 'demo', 'conv_legacy_001', 'customer', 'Legacy',
                     'legacyneedle first', '{}', '2026-01-01T00:01:00+00:00'),
                    ('msg_legacy_002', 'demo', 'conv_legacy_001', 'assistant', 'knowledge',
                     'legacy response', '{}', '2026-01-01T00:02:00+00:00');
                """
            )
            connection.commit()
            connection.close()

            database = Database(path)
            database.initialize()
            conversation = database.get_conversation("demo", "conv_legacy_001")
            assert conversation is not None
            self.assertEqual(conversation["message_count"], 2)
            self.assertEqual(conversation["preview"], "legacy response")
            self.assertEqual(conversation["labels"], [])
            self.assertEqual(
                [
                    row["id"]
                    for row in database.list_conversations("demo", search="legacyneedle", limit=10)
                ],
                ["conv_legacy_001"],
            )

            database.add_message(
                "demo",
                "conv_legacy_001",
                "operator",
                "legacy.agent",
                "follow-up response",
            )
            updated = database.get_conversation("demo", "conv_legacy_001")
            assert updated is not None
            self.assertEqual(updated["message_count"], 3)
            self.assertEqual(updated["preview"], "follow-up response")
            database.close()


class MigrationGovernanceTests(unittest.TestCase):
    """Phase 27.4: the registered migration chain must be well-formed."""

    def test_migration_chain_is_contiguous(self) -> None:
        from app.migrations import all_migrations, verify_migration_chain

        problems = verify_migration_chain(all_migrations())
        self.assertEqual(problems, [], f"migration chain problems: {problems}")

    def test_duplicate_versions_detected(self) -> None:
        from app.migrations import Migration, verify_migration_chain

        problems = verify_migration_chain(
            [
                Migration(version=1, description="a", up=lambda c: None),
                Migration(version=1, description="b", up=lambda c: None),
            ]
        )
        self.assertTrue(any("duplicate" in p for p in problems))

    def test_gap_detected(self) -> None:
        from app.migrations import Migration, verify_migration_chain

        problems = verify_migration_chain(
            [
                Migration(version=1, description="a", up=lambda c: None),
                Migration(version=3, description="c", up=lambda c: None),
            ]
        )
        self.assertTrue(any("non-contiguous" in p and "2" in p for p in problems))

    def test_empty_chain_reported(self) -> None:
        from app.migrations import verify_migration_chain

        self.assertTrue(verify_migration_chain([]))

    def test_schema_version_after_apply(self) -> None:
        from app.migrations import (
            all_migrations,
            migration_schema_version,
            run_migrations,
        )

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "v.db"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                self.assertEqual(migration_schema_version(connection), 0)
                run_migrations(connection, all_migrations())
                self.assertEqual(
                    migration_schema_version(connection),
                    max(m.version for m in all_migrations()),
                )
            finally:
                connection.close()

    def test_migration_12_backfills_existing_audit_rows(self) -> None:
        """Upgrading a DB with pre-chain audit rows must backfill hashes so
        the chain verifies (regression: un-backfilled rows made the chain
        permanently 'tampered')."""
        from app.audit_chain import verify_chain
        from app.migrations import all_migrations, run_migrations

        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "upgrade.db"
            connection = sqlite3.connect(path)
            connection.row_factory = sqlite3.Row
            try:
                # Migrate to version 11 (pre audit-chain), insert legacy rows.
                pre = [m for m in all_migrations() if m.version <= 11]
                run_migrations(connection, pre)
                connection.execute(
                    "INSERT INTO audit_events (id, tenant_id, actor, event_type, "
                    "payload_json, created_at) VALUES "
                    "('evt_old1','demo','admin','a','{}','2026-01-01T00:00:00+00:00'),"
                    "('evt_old2','demo','admin','b','{}','2026-01-01T00:00:01+00:00')"
                )
                connection.commit()
                # Upgrade to latest; migration 12 must backfill.
                run_migrations(connection, all_migrations())
                rows = connection.execute(
                    "SELECT id, tenant_id, conversation_id, request_id, actor, "
                    "event_type, payload_json, created_at, prev_hash, event_hash "
                    "FROM audit_events ORDER BY seq"
                ).fetchall()
                data = [dict(r) for r in rows]
                self.assertTrue(all(r["event_hash"] for r in data))
                self.assertEqual(verify_chain(data), [])
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
