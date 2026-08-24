"""Tests for the ROADMAP 18.2d message-pagination seq index (M23).

The hot-path audit found ``list_messages`` paginating by (created_at, seq)
while the pre-existing ``idx_messages_page`` index ended at ``id`` (a UUID),
forcing a temp B-tree sort on every page ("RIGHT PART OF ORDER BY"). M23 adds
``idx_messages_page_seq (tenant_id, conversation_id, created_at ASC, seq ASC)``
so pagination runs straight off the index, forward and backward.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database


class PaginationSeqIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "paging.db")
        self.database.initialize()
        self.database.ensure_tenant("demo")
        self.conversation = self.database.create_conversation(
            "demo", "Customer", None, "web", "admin", 120
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_m23_index_present(self) -> None:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_messages_page_seq'"
            ).fetchone()
        self.assertIsNotNone(row, msg="M23 index idx_messages_page_seq missing")

    def test_forward_paging_plan_uses_index_without_temp_sort(self) -> None:
        self.database.add_message(
            "demo", self.conversation["id"], "customer", "Customer", "hello", {}, None
        )
        with self.database.connect() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM messages WHERE tenant_id = ? "
                "AND conversation_id = ? ORDER BY created_at ASC, seq ASC LIMIT 50",
                ("demo", self.conversation["id"]),
            ).fetchall()
        details = " ".join(row["detail"] for row in plan)
        self.assertIn("idx_messages_page_seq", details)
        self.assertNotIn("TEMP B-TREE", details)
        self.assertIn("SEARCH", details)

    def test_backward_keyset_paging_plan_uses_index(self) -> None:
        with self.database.connect() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT * FROM messages WHERE tenant_id = ? "
                "AND conversation_id = ? AND (created_at < ? OR (created_at = ? AND seq < ?)) "
                "ORDER BY created_at DESC, seq DESC LIMIT 50",
                (
                    "demo",
                    self.conversation["id"],
                    "2026-08-16T00:00:00+00:00",
                    "2026-08-16T00:00:00+00:00",
                    1,
                ),
            ).fetchall()
        details = " ".join(row["detail"] for row in plan)
        self.assertIn("idx_messages_page_seq", details)
        self.assertNotIn("TEMP B-TREE", details)


if __name__ == "__main__":
    unittest.main()
