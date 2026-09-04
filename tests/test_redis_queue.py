"""Redis durable-queue integration tests.

Runs against a live Redis (skipUnless ``REDIS_URL``) and verifies the
cross-instance invariants the queue abstraction promises: exactly-once
dispatch, no double-claim across independent queue instances, per-conversation
serialization, retry backoff, lease recovery, and cleanup on completion.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.queue import RedisTaskQueue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

_QUEUE_KEYS = (
    "helix:q:dispatch",
    "helix:q:processing",
    "helix:q:leases",
    "helix:q:delayed",
    "helix:turn_jobs:dispatch",
)


def _redis_available() -> bool:
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


@unittest.skipUnless(_redis_available(), "Requires a live Redis (REDIS_URL)")
class RedisQueueIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "redis.db")
        self.db.initialize()
        self.db.ensure_tenant("redis-tenant")
        for key in _QUEUE_KEYS:
            self.redis.delete(key)

    def tearDown(self) -> None:
        for key in _QUEUE_KEYS:
            self.redis.delete(key)
        self.db.close()
        self._tmp.cleanup()

    def _queue(self) -> RedisTaskQueue:
        return RedisTaskQueue(self.redis, self.db)

    def _conversation(self, index: int = 0) -> str:
        conversation = self.db.create_conversation(
            "redis-tenant", f"Redis Conv {index}", f"CUST-R-{index}", "web", "admin", 120
        )
        return conversation["id"]

    def test_exactly_once_dispatch_under_replay(self) -> None:
        queue = self._queue()
        conv = self._conversation()
        queue.enqueue("redis-tenant", conv, "key-1", "a1", "c1", 3)
        # Re-enqueue with the same idempotency key: the database returns a
        # replay and the dispatch list must not get a duplicate entry.
        queue.enqueue("redis-tenant", conv, "key-1", "a1", "c1", 3)
        self.assertEqual(self.redis.llen("helix:q:dispatch"), 1)

    def test_channel_message_id_survives_redis_dispatch(self) -> None:
        queue = self._queue()
        conversation_id = self._conversation()
        queued, replayed = queue.enqueue(
            "redis-tenant",
            conversation_id,
            "channel-key-1",
            "channel:formal-account",
            "channel content",
            3,
            "provider-message-1",
        )
        self.assertFalse(replayed)
        self.assertEqual(queued["channel_message_id"], "provider-message-1")

        claimed = queue.dequeue("channel-worker", 300)
        self.assertIsNotNone(claimed)
        assert claimed is not None
        self.assertEqual(claimed["id"], queued["id"])
        self.assertEqual(claimed["channel_message_id"], "provider-message-1")

    def test_no_double_claim_across_instances(self) -> None:
        """Two independent queue instances over one Redis never double-claim."""
        for index in range(8):
            conv = self._conversation(index)
            self._queue().enqueue("redis-tenant", conv, f"k-{index}", f"a{index}", f"c{index}", 3)
        queues = [self._queue(), self._queue()]
        claimed: list[str] = []
        for i in range(16):
            job = queues[i % 2].dequeue(f"worker-{i}", 300)
            if job:
                claimed.append(job["id"])
        self.assertEqual(len(set(claimed)), len(claimed), "double-claim detected")
        self.assertEqual(len(claimed), 8)

    def test_per_conversation_serialization(self) -> None:
        """Only one job per conversation may be in flight, even across queues."""
        conv = self._conversation()
        queue = self._queue()
        for index in range(3):
            queue.enqueue("redis-tenant", conv, f"ser-{index}", f"a{index}", f"c{index}", 3)
        first = queue.dequeue("w1", 300)
        assert first is not None
        # A second claim for the same conversation must be refused.
        self.assertIsNone(queue.dequeue("w2", 300))
        # Completing the in-flight job frees the conversation for the next.
        self.assertTrue(queue.complete(first["id"], "w1", {"ok": True}, 300))
        second = queue.dequeue("w3", 300)
        assert second is not None
        self.assertIsNone(queue.dequeue("w4", 300))

    def test_retry_backoff_reschedules(self) -> None:
        conv = self._conversation()
        queue = self._queue()
        queue.enqueue("redis-tenant", conv, "r-1", "a1", "c1", 3)
        job = queue.dequeue("w1", 300)
        assert job is not None
        result = queue.fail(job["id"], "w1", "boom", 1, 300, retryable=True)
        assert result is not None
        self.assertEqual(result["status"], "queued")
        # Not yet due for the backoff window.
        self.assertIsNone(queue.dequeue("w2", 300))
        time.sleep(1.2)
        retried = queue.dequeue("w2", 300)
        assert retried is not None
        self.assertEqual(retried["attempts"], 2)

    def test_lease_expiry_recovers_and_reclaims(self) -> None:
        conv = self._conversation()
        queue = self._queue()
        queue.enqueue("redis-tenant", conv, "l-1", "a1", "c1", 3)
        job = queue.dequeue("w1", 1)  # very short lease
        assert job is not None
        time.sleep(1.2)
        recovered = queue.recover(1)
        self.assertEqual(recovered["redis_requeued"], 1)
        re_claimed = queue.dequeue("w2", 300)
        assert re_claimed is not None
        self.assertEqual(re_claimed["id"], job["id"])

    def test_complete_cleans_up_redis_state(self) -> None:
        conv = self._conversation()
        queue = self._queue()
        queue.enqueue("redis-tenant", conv, "cc-1", "a1", "c1", 3)
        job = queue.dequeue("w1", 300)
        assert job is not None
        self.assertEqual(self.redis.hlen("helix:q:processing"), 1)
        self.assertTrue(queue.complete(job["id"], "w1", {"ok": True}, 300))
        self.assertEqual(self.redis.hlen("helix:q:processing"), 0)
        self.assertEqual(self.redis.zcard("helix:q:leases"), 0)

    def test_flush_rebuilds_from_database_exactly_once(self) -> None:
        """42.2: a Redis failover/flush loses nothing and duplicates nothing.

        PostgreSQL is the source of truth; Redis only holds rebuildable
        dispatch state. After a full flush (worst-case failover), one
        ``recover`` pass must re-push every non-terminal DB job exactly once,
        and each job is claimable exactly once afterwards.
        """
        queue = self._queue()
        conversations = [self._conversation(index) for index in range(3)]
        enqueued_ids = set()
        for index, conversation in enumerate(conversations):
            queued, _replayed = queue.enqueue(
                "redis-tenant", conversation, f"flush-{index}", f"a{index}", f"c{index}", 3
            )
            enqueued_ids.add(queued["id"])

        # Worst-case failover: every Redis key is gone.
        self.redis.flushdb()

        recovered = queue.recover(300)
        self.assertGreaterEqual(recovered["reconciled"], 3)

        claimed: list[tuple[str, str]] = []
        for index in range(6):
            job = queue.dequeue(f"worker-{index}", 300)
            if job is not None:
                claimed.append((job["id"], f"worker-{index}"))
        self.assertEqual(len(claimed), 3, "every flushed job must be re-dispatched")
        self.assertEqual(
            {job_id for job_id, _ in claimed}, enqueued_ids, "no job lost or duplicated"
        )
        self.assertEqual(
            len({job_id for job_id, _ in claimed}), len(claimed), "double-claim detected"
        )

        # A second recover pass after everything is completed must not
        # resurrect finished work or create phantom dispatches.
        for job_id, worker in claimed:
            self.assertTrue(queue.complete(job_id, worker, {"ok": True}, 300))
        stable = queue.recover(300)
        self.assertEqual(stable["reconciled"], 0)


REDIS_ADMIN_KEY = "redis-admin-key-0001"


@unittest.skipUnless(_redis_available(), "Requires a live Redis (REDIS_URL)")
class RedisBackendAppEndToEndTests(unittest.TestCase):
    """Full HTTP + worker path against the Redis queue backend.

    Proves the roadmap claim that the durable Redis queue is usable as the
    production dispatch layer: a turn job submitted over HTTP is enqueued into
    Redis, claimed by the worker through the queue abstraction, executed by the
    orchestrator, and reconciled with the database — including a concurrent
    worker pair that must never double-claim.
    """

    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "e2e.db"
        principals = {
            REDIS_ADMIN_KEY: {
                "tenant_id": "redis-tenant",
                "actor_id": "agent.admin",
                "role": "admin",
            }
        }
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=1000,
            docs_enabled=False,
            queue_backend="redis",
            redis_url=REDIS_URL,
            turn_worker_enabled=True,
            turn_job_stream_pacing_ms=0,
        )
        self.client = TestClient(create_app(settings))
        self.headers = {"X-API-Key": REDIS_ADMIN_KEY, "X-Tenant-Id": "redis-tenant"}
        self.services = cast(Any, cast(Any, self.client.app).state).services
        for key in _QUEUE_KEYS:
            self.redis.delete(key)

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        for key in _QUEUE_KEYS:
            self.redis.delete(key)
        self._tmp.cleanup()

    def test_http_turn_job_flows_through_redis_backend(self) -> None:
        created = self.client.post(
            "/api/conversations",
            json={"customer_name": "Redis E2E", "channel": "web"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["id"]

        job = self.client.post(
            f"/api/conversations/{conversation_id}/turn-jobs",
            json={"content": "帮我查一下订单"},
            headers={**self.headers, "Idempotency-Key": "redis-e2e-key"},
        )
        self.assertEqual(job.status_code, 202, job.text)
        self.assertEqual(job.headers["X-Idempotent-Replay"], "false")

        # The new job is waiting in the Redis dispatch list, not just SQLite.
        self.assertEqual(self.redis.llen("helix:q:dispatch"), 1)

        self.services.turn_worker.run_once("redis-e2e-worker")
        fetched = self.services.database.get_turn_job("redis-tenant", job.json()["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["status"], "completed")

        # Completing the job cleans up all Redis claim state.
        self.assertEqual(self.redis.llen("helix:q:dispatch"), 0)
        self.assertEqual(self.redis.hlen("helix:q:processing"), 0)
        self.assertEqual(self.redis.zcard("helix:q:leases"), 0)

    def test_concurrent_workers_dispatch_exactly_once(self) -> None:
        created = self.client.post(
            "/api/conversations",
            json={"customer_name": "Redis Race", "channel": "web"},
            headers=self.headers,
        )
        conversation_id = created.json()["id"]
        job_ids: list[str] = []
        for index in range(6):
            response = self.client.post(
                f"/api/conversations/{conversation_id}/turn-jobs",
                json={"content": f"并发任务 {index}"},
                headers={**self.headers, "Idempotency-Key": f"redis-race-{index}"},
            )
            self.assertEqual(response.status_code, 202, response.text)
            job_ids.append(response.json()["id"])

        # Two worker identities interleave over the same Redis.  Because the
        # conversation is shared, the database serializes in-flight turns: a
        # second claim for the same conversation is refused and released back to
        # the dispatch list.  After enough processing passes every job must be
        # completed exactly once (attempts == 1, status completed).
        for _ in range(4):
            for index in range(2):
                self.services.turn_worker.run_once(f"redis-worker-{index}")
        for job_id in job_ids:
            job = self.services.database.get_turn_job("redis-tenant", job_id)
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "completed", f"job {job_id} not completed")
            self.assertEqual(job["attempts"], 1, f"job {job_id} processed more than once")
        self.assertEqual(self.redis.hlen("helix:q:processing"), 0)
        self.assertEqual(self.redis.llen("helix:q:dispatch"), 0)


if __name__ == "__main__":
    unittest.main()
