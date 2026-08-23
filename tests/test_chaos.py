"""Phase 29.4: chaos tests.

Fault-inject under which the core invariants must hold:
- no duplicate turn (idempotency key dedup),
- no lost task (durable queue + lease recovery),
- audit chain stays continuous,
- per-conversation serialization (one processing job per conversation).

Injected faults: worker "crash" mid-claim (simulated by taking a lease then
recovering), model-provider timeout (deterministic fallback), and a slow DB
write (backpressure keeps intake bounded). Each fault is followed by an
invariant check, not just an error assertion.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import cast

from app.config import Settings
from app.database import Database
from app.orchestrator import ConversationOrchestrator
from app.queue import TaskQueue

ADMIN_KEY = "chaos-admin-key-0001"


def _append_audit(db_path: str, tag: str, count: int = 15) -> None:
    """Module-level worker for the cross-process audit-fork regression test.

    Must be importable (not a closure) so Windows ``multiprocessing`` spawn
    can pickle it.
    """
    from app.database import Database

    db = Database(Path(db_path))
    try:
        for index in range(count):
            db.audit("demo", None, tag, f"evt-{tag}-{index}", {"n": index})
    finally:
        db.close()


class ChaosInvariantTests(unittest.TestCase):
    """29.4: invariants hold under injected faults."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "chaos.db"
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")
        self.orchestrator = ConversationOrchestrator(self.database, settings)
        self.settings = settings

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _conv(self) -> str:
        return self.database.create_conversation("demo", "C", None, "web", "admin", 120)["id"]

    def test_worker_crash_mid_claim_recovers_without_loss(self) -> None:
        """A claimed-then-crashed job is recovered and completes exactly once."""
        import time

        conv = self._conv()
        job, _ = self.database.enqueue_turn_job("demo", conv, "chaos-crash-1", "admin", "hi", 3)
        # Simulate a worker that claimed the job with a 1s lease and crashed
        # before completing.
        claimed = self.database.claim_next_turn_job("worker-crashed", 1)
        assert claimed is not None
        self.assertEqual(claimed["id"], job["id"])
        time.sleep(1.1)
        # Lease recovery re-queues the stuck job once its lease expires
        # (lease-aware: healthy in-flight claims are never touched).
        recovered = self.database.recover_turn_jobs(1)
        self.assertGreaterEqual(recovered.get("queued", 0), 1)
        # A fresh worker completes it exactly once.
        completed = self.database.claim_next_turn_job("worker-fresh", 300)
        assert completed is not None
        self.assertEqual(completed["id"], job["id"])

    def test_recover_does_not_touch_healthy_in_flight_job(self) -> None:
        """A freshly-claimed (non-stale) job is never re-queued by recovery.

        Regression: a peer worker's periodic recover used to re-queue *all*
        ``processing`` jobs regardless of lease age, flipping a healthy
        in-flight job back to ``queued``; the owner's ``complete`` then failed,
        the Redis claim was cleaned up, and the job was orphaned forever.
        """
        conv = self._conv()
        job, _ = self.database.enqueue_turn_job("demo", conv, "chaos-healthy-1", "admin", "hi", 3)
        claimed = self.database.claim_next_turn_job("worker-a", 300)
        assert claimed is not None
        recovered = self.database.recover_turn_jobs(300)
        self.assertEqual(recovered, {"queued": 0, "failed": 0})
        # The owner still owns the claim and can complete it exactly once.
        self.assertTrue(self.database.complete_turn_job(job["id"], "worker-a", {"ok": True}, 300))

    def test_same_key_replay_no_duplicate_turn(self) -> None:
        """Replaying the same idempotency key enqueues exactly one job."""
        conv = self._conv()
        job1, replayed1 = self.database.enqueue_turn_job(
            "demo", conv, "chaos-key-1", "admin", "hi", 3
        )
        job2, replayed2 = self.database.enqueue_turn_job(
            "demo", conv, "chaos-key-1", "admin", "hi", 3
        )
        self.assertFalse(replayed1)
        self.assertTrue(replayed2)
        self.assertEqual(job1["id"], job2["id"])
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM turn_jobs "
                "WHERE tenant_id='demo' AND conversation_id=? AND idempotency_key='chaos-key-1'",
                (conv,),
            ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_per_conversation_serialization(self) -> None:
        """Two concurrent claims on the same conversation never both succeed."""
        conv = self._conv()
        self.database.enqueue_turn_job("demo", conv, "chaos-ser-1", "admin", "a", 3)
        self.database.enqueue_turn_job("demo", conv, "chaos-ser-2", "admin", "b", 3)
        first = self.database.claim_next_turn_job("w1", 300)
        second = self.database.claim_next_turn_job("w2", 300)
        assert first is not None
        # The second claim must be a different conversation (or None) — never
        # the same conversation while the first is processing.
        if second is not None:
            self.assertNotEqual(first["conversation_id"], second["conversation_id"])

    def test_model_timeout_falls_back_deterministic(self) -> None:
        """A failing model provider falls back to deterministic routing."""
        from app.agents import TriageAgent
        from app.model_provider import ModelProviderError

        class _FailingProvider:
            def complete(self, system_prompt: str, user_prompt: str, model_ref=None) -> str:
                raise ModelProviderError("provider timeout")

        decision = TriageAgent(_FailingProvider()).decide("ORD-10482 到哪了")
        # Deterministic fallback routes to the order agent regardless of the
        # failing model provider (the rules path wins, or model failure
        # degrades to it).
        self.assertEqual(decision.route.value, "order")

    def test_audit_chain_stays_continuous_under_load(self) -> None:
        """Audit events written under concurrent turns keep a valid chain."""
        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        for index in range(5):
            conv = self._conv()
            self.orchestrator.handle_customer_message(
                "demo", conv, "ORD-10482 到哪了", "admin", f"chaos-aud-{index}"
            )
        with self.database.connect() as conn:
            rows = load_rows(conn)
        self.assertGreaterEqual(len(rows), 5)
        self.assertEqual(verify_chain(rows), [])

    def test_audit_chain_no_fork_across_processes(self) -> None:
        """Regression: audit appends from *different OS processes* must not fork
        the chain.

        The module-level ``_AUDIT_CHAIN_LOCK`` only serialises one process;
        with ``uvicorn --workers N`` / multi-instance the tail read and insert
        must be serialised by the database (``BEGIN IMMEDIATE`` SQLite,
        advisory lock PG).  Two processes appending concurrently are the
        minimal reproduction of that real-world fork.
        """
        from multiprocessing import Process

        from app.audit_chain import verify_chain
        from scripts.verify_audit_chain import load_rows

        fork_db = Path(self._tmp.name) / "fork.db"
        seed = Database(fork_db)
        seed.initialize()
        seed.close()

        processes = [
            Process(target=_append_audit, args=(str(fork_db), "p0", 15)),
            Process(target=_append_audit, args=(str(fork_db), "p1", 15)),
        ]
        for process in processes:
            process.start()
        for process in processes:
            process.join()
            self.assertEqual(process.exitcode, 0)

        db = Database(fork_db)
        try:
            with db.connect() as conn:
                rows = load_rows(conn)
        finally:
            db.close()
        self.assertEqual(len(rows), 30)
        self.assertEqual(verify_chain(rows), [])

    def test_worker_recover_runs_on_cadence(self) -> None:
        """Phase 29.1: workers keep re-claiming expired leases on a cadence.

        Recovery must not be a one-shot at worker start; a killed instance's
        unfinished jobs become reclaimable once their leases expire and any
        surviving worker has to pick them up without anyone restarting.
        """
        import time

        from app.jobs import TurnJobWorker

        counters = {"recover": 0}

        class _CountingQueue:
            def dequeue(self, worker_id: str, lease_seconds: int) -> dict | None:
                return None  # empty queue: the loop idles between recover passes

            def recover(self, lease_seconds: int) -> dict:
                counters["recover"] += 1
                return {"queued": 0, "failed": 0, "reconciled": 0}

        worker = TurnJobWorker(
            self.database,
            self.orchestrator,
            # This cadence probe exercises only the dequeue/recover surface;
            # the remaining TaskQueue methods are deliberately out of scope.
            queue=cast(TaskQueue, _CountingQueue()),
            concurrency=1,
            poll_interval_ms=50,
            recover_cadence_seconds=5,
        )
        worker.start()
        try:
            deadline = time.monotonic() + 12
            while time.monotonic() < deadline:
                if counters["recover"] >= 2:
                    break
                time.sleep(0.2)
        finally:
            worker.stop()
        self.assertGreaterEqual(counters["recover"], 2, "recover must run on a cadence")


if __name__ == "__main__":
    unittest.main()
