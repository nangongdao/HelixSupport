"""Multi-instance concurrency tests.

The single-instance suite (`tests/test_concurrency.py`) proves the invariants
within one connection pool.  Production runs several application processes
against one shared database (PostgreSQL, or SQLite for a single-writer pilot),
each with its own pool.  These tests exercise that *cross-pool* boundary with
two fully independent database handles over the same store:

- exactly-once enqueue under an idempotency-key race (no duplicate jobs)
- no double-claim of a queued job by concurrent workers
- optimistic-concurrency protection on conversation state transitions
- message-trigger summary consistency across handles

The SQLite cases run everywhere (WAL lets two pools share one file with a
bounded busy timeout); the PostgreSQL cases run only when
``HELIX_PG_INTEGRATION=1`` and ``DATABASE_URL`` point at a disposable database,
because they drop and recreate the ``public`` schema.
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Self

from app.database import Database
from app.domain import ConversationStatus


class _Fixture:
    """A pair of independent database handles over one shared store."""

    def __init__(self, kind: str, tmp_path: Path) -> None:
        self.kind = kind
        self._tmp_path = tmp_path

    def __enter__(self) -> Self:
        if self.kind == "sqlite":
            self.path = self._tmp_path / "multi.db"
            self.a = Database(self.path, pool_size=2, busy_timeout_ms=4000)
            self.b = Database(self.path, pool_size=2, busy_timeout_ms=4000)
        else:
            url = os.environ["DATABASE_URL"]
            import psycopg2

            connection = psycopg2.connect(url)
            connection.autocommit = True
            connection.cursor().execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
            connection.close()

            from app.postgres_db import PostgresDatabase

            self.a = PostgresDatabase(url, pool_size=2)
            self.b = PostgresDatabase(url, pool_size=2)
        self.a.initialize()
        self.b.initialize()
        for db in (self.a, self.b):
            db.ensure_tenant("multi-tenant")
        self.conversation = self.a.create_conversation(
            "multi-tenant", "Multi Instance", "CUST-MI", "web", "admin", 120
        )
        return self

    def __exit__(self, *exc: object) -> None:
        self.a.close()
        self.b.close()

    def claim_with(self, workers: int, queue: Any) -> list[dict[str, Any]]:
        """Claim jobs across both handles, so the two pools interleave."""
        handles = (self.a, self.b)
        claimed: list[dict[str, Any]] = []
        lock = __import__("threading").Lock()

        def claim(index: int) -> dict[str, Any] | None:
            db = handles[index % 2]
            return queue(db).dequeue(f"worker-{index}", 300)

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [executor.submit(claim, i) for i in range(workers)]
            for future in as_completed(futures):
                result = future.result()
                if result:
                    with lock:
                        claimed.append(result)
        return claimed


class MultiInstanceSQLiteTests(unittest.TestCase):
    """Two independent connection pools over one WAL file."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self._tmp = tmp
        self.fixture = _Fixture("sqlite", Path(tmp.name)).__enter__()

    def tearDown(self) -> None:
        with _suppress():
            self.fixture.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_exactly_once_enqueue_cross_pool(self) -> None:
        started = self.fixture.a.enqueue_turn_job(
            "multi-tenant", self.fixture.conversation["id"], "multi-key-a", "a1", "x", 3
        )
        self.assertFalse(started[1])

        race = [0, 1]
        results: list[tuple[dict[str, Any], bool]] = []

        def enqueue(i: int) -> tuple[dict[str, Any], bool]:
            db = (self.fixture.a, self.fixture.b)[i % 2]
            return db.enqueue_turn_job(
                "multi-tenant", self.fixture.conversation["id"], "multi-key-b", f"a{i}", "x", 3
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(enqueue, i) for i in race]
            for future in as_completed(futures):
                results.append(future.result())

        ids = {r[0]["id"] for r in results}
        self.assertEqual(len(ids), 1, f"duplicate job created: {ids}")
        self.assertLessEqual(sum(1 for _, r in results if not r), 1)

    def test_no_double_claim_cross_pool(self) -> None:
        """Eight independent conversations: each worker claims a distinct job.

        ``claim`` serialises turns *within* one conversation, so a single
        conversation cannot exercise a real claim race.  Spreading the jobs
        across conversations lets every worker contend at the head on a true
        cross-pool path.
        """
        from app.queue import SQLiteTaskQueue

        for i in range(8):
            conversation = self.fixture.a.create_conversation(
                "multi-tenant", f"Conv {i}", f"CUST-MI-{i}", "web", "admin", 120
            )
            self.fixture.a.enqueue_turn_job(
                "multi-tenant", conversation["id"], f"claim-{i}", f"a{i}", f"c{i}", 3
            )

        claimed = self.fixture.claim_with(8, SQLiteTaskQueue)
        ids = [c["id"] for c in claimed]
        keys = {c["idempotency_key"] for c in claimed}
        self.assertEqual(len(ids), 8, f"expected 8 claims, got {len(ids)}")
        self.assertEqual(len(set(ids)), len(ids), "a job was claimed twice")
        self.assertEqual(keys, {f"claim-{i}" for i in range(8)})

    def test_optimistic_concurrency_prevents_clobber(self) -> None:
        """A stale version update must be rejected when both pools write."""
        conv_id = self.fixture.conversation["id"]

        pool_a = self.fixture.a
        pool_b = self.fixture.b
        # First move OPEN -> HUMAN_ACTIVE via pool A.
        self.assertTrue(
            pool_a.transition_conversation(
                "multi-tenant", conv_id, [ConversationStatus.OPEN], ConversationStatus.HUMAN_ACTIVE
            )
            is not None
        )
        self.assertIsNone(
            pool_b.transition_conversation(
                "multi-tenant", conv_id, [ConversationStatus.OPEN], ConversationStatus.RESOLVED
            )
        )

    def test_claimed_job_is_invisible_cross_pool(self) -> None:
        from app.queue import SQLiteTaskQueue

        self.fixture.a.enqueue_turn_job(
            "multi-tenant", self.fixture.conversation["id"], "c-key-1", "a1", "c", 3
        )
        first = SQLiteTaskQueue(self.fixture.a).dequeue("worker-a", 300)
        assert first is not None
        # A second claim through the other pool must not hand out the same job.
        second = SQLiteTaskQueue(self.fixture.b).dequeue("worker-b", 300)
        self.assertIsNone(second)


@unittest.skipUnless(
    os.getenv("HELIX_PG_INTEGRATION") and os.getenv("DATABASE_URL"),
    "Requires PostgreSQL instance and HELIX_PG_INTEGRATION=1",
)
class MultiInstancePostgresTests(unittest.TestCase):
    """The same invariants against a shared PostgreSQL database."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self._tmp = tmp
        self.fixture = _Fixture("postgresql", Path(tmp.name)).__enter__()

    def tearDown(self) -> None:
        with _suppress():
            self.fixture.__exit__(None, None, None)
        self._tmp.cleanup()

    def test_exactly_once_enqueue_cross_pool(self) -> None:
        """Two pools racing on one idempotency key must produce one job."""
        started = self.fixture.a.enqueue_turn_job(
            "multi-tenant", self.fixture.conversation["id"], "pg-key-a", "a1", "x", 3
        )
        self.assertFalse(started[1])

        results: list[tuple[dict[str, Any], bool]] = []

        def enqueue(i: int) -> tuple[dict[str, Any], bool]:
            db = (self.fixture.a, self.fixture.b)[i % 2]
            return db.enqueue_turn_job(
                "multi-tenant", self.fixture.conversation["id"], "pg-key-race", f"a{i}", "x", 3
            )

        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(enqueue, i) for i in range(4)]
            for future in as_completed(futures):
                results.append(future.result())

        ids = {r[0]["id"] for r in results}
        self.assertEqual(len(ids), 1, f"duplicate job created: {ids}")
        self.assertLessEqual(sum(1 for _, replayed in results if not replayed), 1)

    def test_no_double_claim_cross_pool(self) -> None:
        """Ten independent conversations, one job each, all claimed exactly once."""
        from app.queue import SQLiteTaskQueue

        for i in range(10):
            conversation = self.fixture.a.create_conversation(
                "multi-tenant", f"PG Conv {i}", f"CUST-MI-{i}", "web", "admin", 120
            )
            self.fixture.a.enqueue_turn_job(
                "multi-tenant",
                conversation["id"],
                f"pg-claim-{i}",
                f"a{i}",
                f"c{i}",
                3,
            )

        claimed = self.fixture.claim_with(10, SQLiteTaskQueue)
        ids = [c["id"] for c in claimed]
        keys = {c["idempotency_key"] for c in claimed}
        self.assertEqual(len(ids), 10, f"expected 10 claims, got {len(ids)}")
        self.assertEqual(len(set(ids)), len(ids), "a job was claimed twice")
        self.assertEqual(keys, {f"pg-claim-{i}" for i in range(10)})

    def test_turns_are_serialised_per_conversation(self) -> None:
        """Only one job per conversation may be in flight, even across pools.

        This is the analogue of the redelivery guard the orchestrator relies on:
        concurrent claims from both pools must hand out at most one job for the
        shared conversation.
        """
        from app.queue import SQLiteTaskQueue

        for i in range(3):
            self.fixture.a.enqueue_turn_job(
                "multi-tenant", self.fixture.conversation["id"], f"serial-{i}", f"a{i}", f"c{i}", 3
            )
        claimed = self.fixture.claim_with(6, SQLiteTaskQueue)
        keys = {c["idempotency_key"] for c in claimed}
        self.assertLessEqual(len(keys), 1, f"turns not serialised: {keys}")

    def test_optimistic_concurrency_prevents_clobber(self) -> None:
        conv_id = self.fixture.conversation["id"]
        self.assertTrue(
            self.fixture.a.transition_conversation(
                "multi-tenant",
                conv_id,
                [ConversationStatus.OPEN],
                ConversationStatus.HUMAN_ACTIVE,
            )
            is not None
        )
        self.assertIsNone(
            self.fixture.b.transition_conversation(
                "multi-tenant", conv_id, [ConversationStatus.OPEN], ConversationStatus.RESOLVED
            )
        )

    def test_summary_maintained_across_pools(self) -> None:
        """Message-trigger summaries must stay correct with writes from two pools."""
        conv_id = self.fixture.conversation["id"]
        for i in range(5):
            db = (self.fixture.a, self.fixture.b)[i % 2]
            db.add_message("multi-tenant", conv_id, "customer", "Customer", f"msg-{i}")
        updated = self.fixture.a.get_conversation("multi-tenant", conv_id)
        assert updated is not None
        self.assertEqual(updated["message_count"], 5)


@contextlib.contextmanager
def _suppress() -> Any:
    try:
        yield
    except Exception:
        pass


if __name__ == "__main__":
    unittest.main()
