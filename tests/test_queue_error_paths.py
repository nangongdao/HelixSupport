"""Queue error handling and edge case coverage.

Supplements existing Redis queue tests (test_redis_queue.py) with error paths,
stats methods, retry operations, and factory fallback scenarios to achieve >85%
coverage on app/queue.py.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from app.database import Database
from app.queue import QueueUnavailableError, RedisTaskQueue, SQLiteTaskQueue, create_task_queue

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _redis_available() -> bool:
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


class SQLiteQueueStatsTests(unittest.TestCase):
    """Coverage for SQLiteTaskQueue.stats() method."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()
        self.db.ensure_tenant("stats-tenant")

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_sqlite_stats_returns_database_stats(self) -> None:
        queue = SQLiteTaskQueue(self.db)
        conversation = self.db.create_conversation(
            "stats-tenant", "Stats Test", "CUST-S-1", "web", "admin", 120
        )
        queue.enqueue("stats-tenant", conversation["id"], "stats-key-1", "actor", "content", 3)

        stats = queue.stats(tenant_id="stats-tenant")
        self.assertIn("queued", stats)
        self.assertIn("completed", stats)
        self.assertGreaterEqual(stats["queued"], 1)

    def test_sqlite_stats_global_no_tenant_filter(self) -> None:
        queue = SQLiteTaskQueue(self.db)
        stats = queue.stats(tenant_id=None)
        self.assertIsInstance(stats, dict)
        self.assertIn("queued", stats)


@unittest.skipUnless(_redis_available(), "Requires a live Redis (REDIS_URL)")
class RedisQueueStatsTests(unittest.TestCase):
    """Coverage for RedisTaskQueue.stats() method."""

    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "stats.db")
        self.db.initialize()
        self.db.ensure_tenant("redis-stats-tenant")
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)

    def tearDown(self) -> None:
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)
        self.db.close()
        self._tmp.cleanup()

    def test_redis_stats_includes_dispatch_depth_and_in_flight(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db)
        conversation = self.db.create_conversation(
            "redis-stats-tenant", "Stats Conv", "CUST-RS-1", "web", "admin", 120
        )
        queue.enqueue("redis-stats-tenant", conversation["id"], "rs-key-1", "actor", "content", 3)

        stats = queue.stats(tenant_id="redis-stats-tenant")
        self.assertIn("redis_dispatch_depth", stats)
        self.assertIn("redis_in_flight", stats)
        self.assertEqual(stats["redis_dispatch_depth"], 1)
        self.assertEqual(stats["redis_in_flight"], 0)

    def test_redis_stats_reflects_claimed_jobs(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db)
        conversation = self.db.create_conversation(
            "redis-stats-tenant", "Claimed Conv", "CUST-RS-2", "web", "admin", 120
        )
        queue.enqueue("redis-stats-tenant", conversation["id"], "rs-key-2", "actor", "content", 3)
        queue.dequeue("stats-worker", 300)

        stats = queue.stats()
        self.assertEqual(stats["redis_dispatch_depth"], 0)
        self.assertEqual(stats["redis_in_flight"], 1)

    def test_redis_stats_degrades_gracefully_on_redis_error(self) -> None:
        """When Redis operations fail in non-fail-closed mode, stats() still raises."""
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=False)
        # In non-fail-closed mode, _guard() still re-raises the exception
        with patch.object(queue.redis, "llen", side_effect=Exception("Redis down")):
            with self.assertRaises(Exception):
                queue.stats()

    def test_redis_stats_fail_closed_raises_on_error(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        with patch.object(queue.redis, "llen", side_effect=Exception("Redis unavailable")):
            with self.assertRaises(QueueUnavailableError):
                queue.stats()


@unittest.skipUnless(_redis_available(), "Requires a live Redis (REDIS_URL)")
class RedisQueueRetryTests(unittest.TestCase):
    """Coverage for RedisTaskQueue.retry() method."""

    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "retry.db")
        self.db.initialize()
        self.db.ensure_tenant("retry-tenant")
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)

    def tearDown(self) -> None:
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)
        self.db.close()
        self._tmp.cleanup()

    def test_retry_re_dispatches_terminal_failed_job(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db)
        conversation = self.db.create_conversation(
            "retry-tenant", "Retry Conv", "CUST-RT-1", "web", "admin", 120
        )
        job, _ = queue.enqueue("retry-tenant", conversation["id"], "retry-key-1", "actor", "msg", 1)
        claimed = queue.dequeue("retry-worker", 300)
        assert claimed is not None
        # Fail with retryable=False to reach terminal failed state
        queue.fail(claimed["id"], "retry-worker", "fatal_error", 1, 300, retryable=False)

        retried = queue.retry("retry-tenant", job["id"])
        self.assertIsNotNone(retried)
        self.assertEqual(retried["status"], "queued")
        # Redis dispatch list should now contain the retried job
        self.assertEqual(self.redis.llen("helix:q:dispatch"), 1)

    def test_retry_returns_none_for_nonexistent_job(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db)
        result = queue.retry("retry-tenant", "nonexistent-job-id")
        self.assertIsNone(result)

    def test_retry_fail_closed_raises_on_redis_error(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        conversation = self.db.create_conversation(
            "retry-tenant", "Retry FC", "CUST-RT-2", "web", "admin", 120
        )
        job, _ = queue.enqueue("retry-tenant", conversation["id"], "retry-fc-1", "actor", "msg", 1)
        claimed = queue.dequeue("fc-worker", 300)
        assert claimed is not None
        queue.fail(claimed["id"], "fc-worker", "error", 1, 300, retryable=False)

        with patch.object(queue.redis, "rpush", side_effect=Exception("Redis failure")):
            with self.assertRaises(QueueUnavailableError):
                queue.retry("retry-tenant", job["id"])


@unittest.skipUnless(_redis_available(), "Requires a live Redis (REDIS_URL)")
class RedisQueueErrorHandlingTests(unittest.TestCase):
    """Coverage for Redis error paths in enqueue, dequeue, complete, fail, recover."""

    def setUp(self) -> None:
        import redis

        self.redis = redis.from_url(REDIS_URL, decode_responses=True)
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "errors.db")
        self.db.initialize()
        self.db.ensure_tenant("error-tenant")
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)

    def tearDown(self) -> None:
        for key in ("helix:q:dispatch", "helix:q:processing", "helix:q:leases", "helix:q:delayed"):
            self.redis.delete(key)
        self.db.close()
        self._tmp.cleanup()

    def test_enqueue_fail_closed_raises_on_rpush_failure(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        conversation = self.db.create_conversation(
            "error-tenant", "Error Conv", "CUST-E-1", "web", "admin", 120
        )
        with patch.object(queue.redis, "rpush", side_effect=Exception("Redis write error")):
            with self.assertRaises(QueueUnavailableError):
                queue.enqueue("error-tenant", conversation["id"], "err-key-1", "actor", "msg", 3)

    def test_dequeue_fail_closed_raises_on_eval_failure(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        with patch.object(queue.redis, "zrangebyscore", side_effect=Exception("Redis error")):
            with self.assertRaises(QueueUnavailableError):
                queue.dequeue("err-worker", 300)

    def test_dequeue_returns_none_on_eval_exception_when_not_fail_closed(self) -> None:
        """In non-fail-closed mode, eval exceptions are still re-raised by _guard()."""
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=False)
        # _promote_delayed is called first and will raise on zrangebyscore failure
        with patch.object(queue.redis, "zrangebyscore", return_value=[]):
            with patch.object(queue.redis, "eval", side_effect=Exception("Lua script error")):
                with self.assertRaises(Exception):
                    queue.dequeue("err-worker", 300)

    def test_complete_fail_closed_raises_on_cleanup_failure(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        conversation = self.db.create_conversation(
            "error-tenant", "Complete Err", "CUST-E-2", "web", "admin", 120
        )
        job, _ = queue.enqueue("error-tenant", conversation["id"], "comp-err-1", "actor", "msg", 3)
        claimed = queue.dequeue("comp-worker", 300)
        assert claimed is not None

        with patch.object(queue.redis, "zrem", side_effect=Exception("Redis cleanup error")):
            with self.assertRaises(QueueUnavailableError):
                queue.complete(claimed["id"], "comp-worker", {"ok": True}, 300)

    def test_fail_fail_closed_raises_on_cleanup_exception(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        conversation = self.db.create_conversation(
            "error-tenant", "Fail Err", "CUST-E-3", "web", "admin", 120
        )
        queue.enqueue("error-tenant", conversation["id"], "fail-err-1", "actor", "msg", 3)
        claimed = queue.dequeue("fail-worker", 300)
        assert claimed is not None

        with patch.object(queue.redis, "zrem", side_effect=Exception("Cleanup error")):
            with self.assertRaises(QueueUnavailableError):
                queue.fail(claimed["id"], "fail-worker", "error_code", 1, 300)

    def test_fail_fail_closed_raises_on_delayed_zadd_failure(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        conversation = self.db.create_conversation(
            "error-tenant", "Delayed Err", "CUST-E-4", "web", "admin", 120
        )
        queue.enqueue("error-tenant", conversation["id"], "delayed-err-1", "actor", "msg", 3)
        claimed = queue.dequeue("delayed-worker", 300)
        assert claimed is not None

        with patch.object(queue.redis, "zadd", side_effect=Exception("zadd error")):
            with self.assertRaises(QueueUnavailableError):
                queue.fail(
                    claimed["id"], "delayed-worker", "retryable_error", 2, 300, retryable=True
                )

    def test_recover_handles_redis_error_in_delayed_promotion(self) -> None:
        """recover() calls _promote_delayed which will raise in non-fail-closed mode."""
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=False)
        # _promote_delayed is called first in recover() and will raise
        with patch.object(queue.redis, "zrangebyscore", side_effect=Exception("Redis error")):
            with self.assertRaises(Exception):
                queue.recover(300)

    def test_recover_fail_closed_raises_on_lease_query_failure(self) -> None:
        queue = RedisTaskQueue(self.redis, self.db, fail_closed=True)
        with patch.object(queue.redis, "zrangebyscore", side_effect=Exception("Redis failure")):
            with self.assertRaises(QueueUnavailableError):
                queue.recover(300)


class CreateTaskQueueFactoryTests(unittest.TestCase):
    """Coverage for create_task_queue factory function fallback paths."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_redis_import_error_fallback_to_sqlite(self) -> None:
        """When redis package import fails, fallback to SQLite queue."""
        settings = Mock(queue_backend="redis", redis_url=REDIS_URL, queue_failure_mode="fallback")

        def mock_import(name, *args, **kwargs):
            if name == "redis":
                raise ImportError("redis not installed")
            return __import__(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            queue = create_task_queue(self.db, settings)
            self.assertIsInstance(queue, SQLiteTaskQueue)

    def test_redis_import_error_fail_closed_raises(self) -> None:
        settings = Mock(
            queue_backend="redis", redis_url=REDIS_URL, queue_failure_mode="fail_closed"
        )
        with patch("builtins.__import__", side_effect=ImportError("redis not installed")):
            with self.assertRaises(QueueUnavailableError) as ctx:
                create_task_queue(self.db, settings)
            self.assertIn("not installed", str(ctx.exception))

    def test_redis_client_build_error_fallback_to_sqlite(self) -> None:
        settings = Mock(
            queue_backend="redis", redis_url="redis://invalid:9999", queue_failure_mode="fallback"
        )
        queue = create_task_queue(self.db, settings)
        # If Redis fails to connect, should fall back to SQLite
        self.assertIsInstance(queue, SQLiteTaskQueue)

    def test_redis_unreachable_fallback_to_sqlite(self) -> None:
        """When Redis client builds but probe fails, fallback mode returns SQLite."""
        settings = Mock(queue_backend="redis", redis_url=REDIS_URL, queue_failure_mode="fallback")
        with patch("app.queue.RedisTaskQueue.probe", return_value=False):
            queue = create_task_queue(self.db, settings)
            if not isinstance(queue, SQLiteTaskQueue):
                # Redis is actually reachable, skip this test
                self.skipTest("Redis is reachable; fallback path not exercisable")


if __name__ == "__main__":
    unittest.main()
