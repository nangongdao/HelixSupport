"""Tests for ROADMAP 18.3 conversation archiving (cold tier).

Covers migration 24 (archive tables), the transactional move of resolved
conversations out of the hot tables, the transparent read merge (get /
messages / list with ``archived=True``), the hot-only write path, and the
turn worker's archive cadence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database, utc_after_seconds, utc_now
from app.main import create_app
from app.migrations import all_migrations, run_migrations
from app.retention import RetentionService

ADMIN_KEY = "archive-admin-key-0001"


class Migration24Tests(unittest.TestCase):
    def test_archive_tables_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m24.db"
            connection = Database(path)._new_connection()
            try:
                result = run_migrations(connection, all_migrations())
                self.assertIn(24, result.applied)
                tables = {
                    r[0]
                    for r in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                self.assertIn("conversations_archive", tables)
                self.assertIn("messages_archive", tables)
                self.assertIn("conversation_labels_archive", tables)
                self.assertIn("feedback_archive", tables)
            finally:
                connection.close()


class ArchiveDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "archive.db")
        self.db.initialize()
        self.db.ensure_tenant("t1")
        self.old_conv = self.db.create_conversation("t1", "Old Customer", None, "web", "admin", 120)
        self.recent_conv = self.db.create_conversation(
            "t1", "Recent Customer", None, "web", "admin", 120
        )
        self.open_conv = self.db.create_conversation(
            "t1", "Open Customer", None, "web", "admin", 120
        )
        # Resolve old (200 days ago) and recent (now); leave the third open.
        self._resolve(self.old_conv["id"], utc_after_seconds(-200 * 86400))
        self._resolve(self.recent_conv["id"], utc_now())
        self._add_label(self.old_conv["id"], "refund")
        for role, text in (("customer", "你好"), ("assistant", "退款已处理")):
            self.db.add_message(
                "t1",
                self.old_conv["id"],
                role,
                "agent" if role == "assistant" else "Customer",
                text,
                {},
                None,
            )
        self.cutoff = utc_after_seconds(-100 * 86400)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _resolve(self, conversation_id: str, resolved_at: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE conversations SET status = 'resolved', resolved_at = ?, updated_at = ? "
                "WHERE id = ?",
                (resolved_at, resolved_at, conversation_id),
            )

    def _add_label(self, conversation_id: str, label: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO conversation_labels (tenant_id, conversation_id, label, "
                "created_by, created_at) VALUES (?, ?, ?, ?, ?)",
                ("t1", conversation_id, label, "admin", utc_now()),
            )

    def test_archive_moves_only_old_resolved_conversations(self) -> None:
        count = self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        self.assertEqual(count, 1)

        # The row left the hot table; get_conversation now resolves through the
        # archive tier (transparent merge), exposing the archived snapshot.
        with self.db.connect() as connection:
            hot = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (self.old_conv["id"],)
            ).fetchone()
        self.assertIsNone(hot)
        via_merge = self.db.get_conversation("t1", self.old_conv["id"])
        self.assertIsNotNone(via_merge)
        assert via_merge is not None
        self.assertEqual(via_merge["status"], "resolved")
        self.assertEqual(via_merge["labels"], ["refund"])
        self.assertIsNotNone(via_merge.get("archived_at"))

        archived = self.db.get_archived_conversation("t1", self.old_conv["id"])
        self.assertIsNotNone(archived)
        assert archived is not None
        self.assertEqual(archived["customer_name"], "Old Customer")

        # Recent resolved and open conversations must stay hot.
        self.assertIsNotNone(self.db.get_conversation("t1", self.recent_conv["id"]))
        self.assertIsNotNone(self.db.get_conversation("t1", self.open_conv["id"]))

    def test_messages_and_labels_are_moved_with_the_conversation(self) -> None:
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        messages = self.db.list_archived_messages("t1", self.old_conv["id"])
        self.assertEqual([m["content"] for m in messages], ["你好", "退款已处理"])
        with self.db.connect() as connection:
            hot_labels = connection.execute(
                "SELECT COUNT(*) AS n FROM conversation_labels WHERE conversation_id = ?",
                (self.old_conv["id"],),
            ).fetchone()
            self.assertEqual(int(hot_labels["n"]), 0)

    def test_data_subject_requests_cover_cold_conversations(self) -> None:
        conversation = self.db.create_conversation(
            "t1", "Archived DSR", "CUST-ARCHIVE-DSR", "web", "admin", 120
        )
        self._resolve(conversation["id"], utc_after_seconds(-200 * 86400))
        self.db.add_message(
            "t1", conversation["id"], "customer", "Customer", "private archived message"
        )
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)

        service = RetentionService(self.db)
        exported = service.execute_data_subject_export("t1", "CUST-ARCHIVE-DSR")
        self.assertEqual(len(exported["conversations"]), 1)
        self.assertEqual(len(exported["messages"]), 1)
        counts = service.execute_data_subject_deletion("t1", "CUST-ARCHIVE-DSR")
        self.assertEqual(counts["conversations_archive"], 1)
        self.assertEqual(counts["messages_archive"], 1)
        self.assertIsNone(self.db.get_archived_conversation("t1", conversation["id"]))

    def test_read_path_transparent_merge(self) -> None:
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        # get_conversation resolves through the archive tier.
        detail = self.db.get_conversation("t1", self.old_conv["id"])
        self.assertIsNotNone(detail)
        assert detail is not None
        self.assertEqual(detail["customer_name"], "Old Customer")
        # list_messages serves the archived transcript with seq keyset support.
        page = self.db.list_messages("t1", self.old_conv["id"], limit=1)
        self.assertEqual(len(page), 1)
        next_cursor = (page[0]["created_at"], page[0]["seq"])
        page2 = self.db.list_messages("t1", self.old_conv["id"], limit=1, cursor=next_cursor)
        self.assertEqual([m["content"] for m in page2], ["退款已处理"])

    def test_list_conversations_archived_flag(self) -> None:
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        hot = self.db.list_conversations("t1", sort="priority", limit=50)
        self.assertEqual({r["id"] for r in hot}, {self.recent_conv["id"], self.open_conv["id"]})
        archived = self.db.list_conversations("t1", sort="updated", limit=50, archived=True)
        self.assertEqual({r["id"] for r in archived}, {self.old_conv["id"]})

    def test_archived_list_search_falls_back_to_like(self) -> None:
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        rows = self.db.list_conversations("t1", search="Old", limit=50, archived=True)
        self.assertEqual([r["id"] for r in rows], [self.old_conv["id"]])

    def test_write_path_is_hot_only(self) -> None:
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        with self.assertRaises(LookupError):
            self.db.add_message("t1", self.old_conv["id"], "customer", "Customer", "再写", {}, None)

    def test_feedback_moved_with_conversation(self) -> None:
        # Seed a feedback row referencing the old conversation's assistant
        # message so the archive move must handle the message FK.
        msg = self.db.add_message(
            "t1", self.old_conv["id"], "assistant", "agent", "replied", {}, None
        )
        now = utc_now()
        with self.db.connect() as connection:
            connection.execute(
                "INSERT INTO feedback (id, tenant_id, conversation_id, message_id, actor, "
                "rating, reason, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                ("fb-1", "t1", self.old_conv["id"], msg["id"], "agent", 1, "good", now, now),
            )
        self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        with self.db.connect() as connection:
            hot = connection.execute(
                "SELECT COUNT(*) AS n FROM feedback WHERE conversation_id = ?",
                (self.old_conv["id"],),
            ).fetchone()
            cold = connection.execute(
                "SELECT COUNT(*) AS n FROM feedback_archive WHERE conversation_id = ?",
                (self.old_conv["id"],),
            ).fetchone()
        self.assertEqual(int(hot["n"]), 0)
        self.assertEqual(int(cold["n"]), 1)

    def test_archive_skips_conversation_with_active_job(self) -> None:
        self.db.enqueue_turn_job("t1", self.old_conv["id"], "queue-key", "actor", "内容", 3)
        count = self.db.archive_resolved_conversations(self.cutoff, max_count=10)
        self.assertEqual(count, 0)
        self.assertIsNotNone(self.db.get_conversation("t1", self.old_conv["id"]))

    def test_archive_is_bounded_and_idempotent(self) -> None:
        self.assertEqual(self.db.archive_resolved_conversations(self.cutoff, max_count=1), 1)
        # Nothing left to move on a replay, and the batch cap still respected.
        self.assertEqual(self.db.archive_resolved_conversations(self.cutoff, max_count=10), 0)


class ArchiveWorkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        principals = {ADMIN_KEY: {"tenant_id": "t1", "actor_id": "agent.admin", "role": "admin"}}
        self.settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=1000,
            docs_enabled=False,
            turn_worker_enabled=False,
        )
        self.client = TestClient(create_app(self.settings))
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "t1"}
        self.services = cast_services(self.client)

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def test_worker_archives_and_counts(self) -> None:
        database = self.services.database
        database.ensure_tenant("t1")
        conv = database.create_conversation("t1", "Archived Worker", None, "web", "admin", 120)
        with database.connect() as connection:
            connection.execute(
                "UPDATE conversations SET status = 'resolved', resolved_at = ?, updated_at = ? "
                "WHERE id = ?",
                (utc_after_seconds(-365 * 86400), utc_after_seconds(-365 * 86400), conv["id"]),
            )
        worker = self.services.turn_worker
        self.assertEqual(worker.archive_once(), 1)
        with database.connect() as connection:
            hot = connection.execute(
                "SELECT 1 FROM conversations WHERE id = ?", (conv["id"],)
            ).fetchone()
        self.assertIsNone(hot)
        via_merge = database.get_conversation("t1", conv["id"])
        self.assertIsNotNone(via_merge)
        assert via_merge is not None
        self.assertIsNotNone(via_merge.get("archived_at"))
        self.assertIsNotNone(database.get_archived_conversation("t1", conv["id"]))
        # Replaying archives nothing.
        self.assertEqual(worker.archive_once(), 0)
        self.assertEqual(worker.snapshot()["archived_total"], 1)

    def test_archive_can_be_disabled(self) -> None:
        worker = self.services.turn_worker
        worker.archive_enabled = False
        self.assertEqual(worker.archive_once(), 0)


class ArchiveSettingsTests(unittest.TestCase):
    def test_archive_after_days_validated(self) -> None:
        settings = Settings(conversation_archive_after_days=5)
        with self.assertRaises(ValueError):
            settings.validate()


def cast_services(client: TestClient) -> Any:
    app = cast(Any, client.app)
    return app.state.services


if __name__ == "__main__":
    unittest.main()
