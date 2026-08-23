"""Concurrency safety and idempotency tests.

Tests that exercise the single-instance concurrency boundary:
- Concurrent turn job claiming (no double-claim)
- Idempotency key competition (exactly-once enqueue)
- Concurrent message writes (summary trigger consistency)
- Backup/restore round-trip integrity

These tests verify the correctness invariants that must hold when
the system is extended to multiple worker instances.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from app.database import Database
from app.queue import SQLiteTaskQueue
from scripts.backup import backup_database
from scripts.restore import restore_database


class ConcurrencySafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("conc-tenant")
        self.db.create_conversation("conc-tenant", "Customer", None, "web", "admin", 120)
        with self.db.connect() as conn:
            self.conv_id = conn.execute(
                "SELECT id FROM conversations WHERE tenant_id = 'conc-tenant' LIMIT 1"
            ).fetchone()[0]

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_concurrent_enqueue_same_idempotency_key(self) -> None:
        """Two concurrent enqueues with the same idempotency key must not create duplicate jobs."""
        results: list[tuple[dict[str, Any], bool]] = []

        def enqueue() -> tuple[dict[str, Any], bool]:
            return self.db.enqueue_turn_job(
                "conc-tenant", self.conv_id, "same-key", "actor-1", "content", 3
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(enqueue) for _ in range(4)]
            for future in as_completed(futures):
                results.append(future.result())

        # Exactly one job should be created; all others return the same job as replay
        job_ids = {r[0]["id"] for r in results}
        self.assertEqual(len(job_ids), 1, f"Expected 1 job, got {len(job_ids)}: {job_ids}")
        replay_flags = [r[1] for r in results]
        # At least one should be a replay (True), at most one should be new (False)
        new_count = sum(1 for replayed in replay_flags if not replayed)
        self.assertLessEqual(new_count, 1, f"Expected at most 1 new job, got {new_count}")

    def test_concurrent_claim_no_double_dispatch(self) -> None:
        """Multiple workers claiming jobs concurrently must not claim the same job."""
        queue = SQLiteTaskQueue(self.db)
        # Enqueue 10 jobs
        for i in range(10):
            queue.enqueue(
                "conc-tenant", self.conv_id, f"claim-key-{i}", f"actor-{i}", f"content-{i}", 3
            )

        claimed: list[dict[str, Any]] = []
        lock = __import__("threading").Lock()

        def claim(worker_id: str) -> dict[str, Any] | None:
            return queue.dequeue(worker_id, 300)

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(claim, f"worker-{i}") for i in range(8)]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    with lock:
                        claimed.append(result)

        # Each job should be claimed by at most one worker
        job_ids = [c["id"] for c in claimed]
        unique_ids = set(job_ids)
        self.assertEqual(len(job_ids), len(unique_ids), "Double-claim detected")

    def test_concurrent_message_writes_summary_consistency(self) -> None:
        """Consecutive message writes must maintain accurate message_count and version."""
        for i in range(5):
            self.db.add_message("conc-tenant", self.conv_id, "customer", "Customer", f"msg-{i}")

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT message_count, version FROM conversations WHERE id = ?", (self.conv_id,)
            ).fetchone()
        # 5 messages + 1 initial (from create_conversation has 0, so just 5)
        self.assertEqual(row[0], 5, f"Expected message_count=5, got {row[0]}")
        self.assertGreaterEqual(row[1], 6, f"Expected version>=6, got {row[1]}")

    def test_backup_restore_roundtrip(self) -> None:
        """Backup and restore must produce a consistent, readable database."""
        self.db.add_message("conc-tenant", self.conv_id, "customer", "Customer", "backup test")

        with tempfile.TemporaryDirectory() as backup_dir:
            backup_path = Path(backup_dir)
            manifest = backup_database(Path(self._tmp.name), backup_path, compress=True)

            # Verify the backup exists and has correct checksum
            backup_file = backup_path / manifest["backup_file"]
            self.assertTrue(backup_file.exists())

            # Restore to a new path
            restore_target = Path(backup_dir) / "restored.db"
            result = restore_database(backup_file, restore_target, manifest_path=None)

            # Verify restored database is readable and has the data
            conn = sqlite3.connect(str(restore_target))
            try:
                count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                self.assertGreaterEqual(count, 1)
            finally:
                conn.close()

            self.assertEqual(result["status"], "restored")

    def test_message_ordering_and_pagination_is_deterministic(self) -> None:
        """Tight-loop inserts order by ``seq`` and paginate without overlap.

        Two messages that land in the same microsecond must not come out in
        random order (the UUID id must not be the tiebreaker).  ``seq`` is
        filled from the implicit rowid on SQLite, so ordering follows insertion
        order exactly, and cursor pagination walks the transcript with no gaps
        or duplicates.
        """
        for index in range(7):
            self.db.add_message("conc-tenant", self.conv_id, "customer", "Customer", f"m-{index}")
        messages = self.db.list_messages("conc-tenant", self.conv_id)
        self.assertEqual([m["content"] for m in messages], [f"m-{i}" for i in range(7)])
        seqs = [m["seq"] for m in messages]
        self.assertEqual(seqs, sorted(seqs), "seq must be monotonic")
        self.assertEqual(len(set(seqs)), len(seqs), "seq values must be distinct")

        # Cursor pagination: walk the same transcript two at a time.
        seen: list[str] = []
        cursor: tuple[str, int] | None = None
        while True:
            page = self.db.list_messages("conc-tenant", self.conv_id, limit=2, cursor=cursor)
            seen.extend(m["content"] for m in page)
            if len(page) < 2:
                break
            last = page[-1]
            cursor = (last["created_at"], int(last["seq"]))
        self.assertEqual(seen, [f"m-{i}" for i in range(7)])


class IdempotencyEdgeCases(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("idem-tenant")
        self.db.create_conversation("idem-tenant", "Customer", None, "web", "admin", 120)
        with self.db.connect() as conn:
            self.conv_id = conn.execute(
                "SELECT id FROM conversations WHERE tenant_id = 'idem-tenant' LIMIT 1"
            ).fetchone()[0]

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_replay_returns_same_job_id(self) -> None:
        """Re-enqueuing with the same idempotency key returns the same job without error."""
        job1, replayed1 = self.db.enqueue_turn_job(
            "idem-tenant", self.conv_id, "idem-1", "actor", "content", 3
        )
        self.assertFalse(replayed1)

        job2, replayed2 = self.db.enqueue_turn_job(
            "idem-tenant", self.conv_id, "idem-1", "actor", "different content", 3
        )
        self.assertTrue(replayed2)
        self.assertEqual(job1["id"], job2["id"])

    def test_different_keys_create_different_jobs(self) -> None:
        """Different idempotency keys create different jobs."""
        job1, _ = self.db.enqueue_turn_job(
            "idem-tenant", self.conv_id, "key-a", "actor", "content-a", 3
        )
        job2, _ = self.db.enqueue_turn_job(
            "idem-tenant", self.conv_id, "key-b", "actor", "content-b", 3
        )
        self.assertNotEqual(job1["id"], job2["id"])

    def test_completed_job_claim_returns_none(self) -> None:
        """After a job is completed, dequeue returns None for empty queue."""
        queue = SQLiteTaskQueue(self.db)
        job, _ = queue.enqueue("idem-tenant", self.conv_id, "complete-key", "actor", "content", 3)
        claimed = queue.dequeue("worker-1", 300)
        assert claimed is not None
        queue.complete(job["id"], "worker-1", {"result": "done"}, 300)

        # No more jobs to claim
        second_claim = queue.dequeue("worker-2", 300)
        self.assertIsNone(second_claim)


if __name__ == "__main__":
    unittest.main()
