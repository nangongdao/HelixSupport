"""M0 REL-001: fail-closed task queue behavior.

Covers the deployment-profile/failure-mode configuration contract, the
probe/readiness surface on both queue backends, the 503 + Retry-After API
behavior when a fail-closed queue is down, and the worker refusing to start
(or back off) while the queue is unavailable.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.main import create_app
from app.queue import (
    QueueUnavailableError,
    RedisTaskQueue,
    SQLiteTaskQueue,
    create_task_queue,
)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")


def _redis_reachable() -> bool:
    """Return True when a real Redis answers on REDIS_URL.

    Defined at module scope, *before* the ``@skipUnless`` decorator on
    :class:`FailClosedApiTests` evaluates at import time.  Those tests require
    Redis to be DOWN (point REDIS_URL at a dead port); when a real Redis is
    reachable they are skipped.
    """
    try:
        import redis

        client = redis.from_url(REDIS_URL, decode_responses=True, socket_connect_timeout=1)
        client.ping()
        return True
    except Exception:
        return False


def _psycopg2_importable() -> bool:
    """True when the PostgreSQL driver is installed in this environment.

    :class:`FailClosedApiTests` builds an app whose ``DATABASE_URL`` points at a
    nonexistent host: with the driver present, ``create_app`` fails fast on DNS
    resolution instead of exercising the queue contract.  The tests are
    meaningful in driver-less environments (the historical CI posture) and skip
    elsewhere; the live-PG equivalent runs in ``tests/test_postgres.py``.
    """
    try:
        import psycopg2  # noqa: F401

        return True
    except ImportError:
        return False


class DeploymentProfileTests(unittest.TestCase):
    """40.3: DEPLOYMENT_PROFILE / QUEUE_FAILURE_MODE validation."""

    def test_defaults_are_single_and_fallback(self) -> None:
        settings = Settings()
        self.assertEqual(settings.deployment_profile, "single")
        self.assertEqual(settings.queue_failure_mode, "fallback")

    def test_multi_defaults_to_fail_closed(self) -> None:
        # from_env derives the failure mode from the profile when unset.
        original = dict(os.environ)
        try:
            os.environ["DEPLOYMENT_PROFILE"] = "multi"
            os.environ["DATABASE_BACKEND"] = "postgresql"
            os.environ["DATABASE_URL"] = "postgresql://x"
            os.environ["QUEUE_BACKEND"] = "redis"
            os.environ["REDIS_URL"] = REDIS_URL
            os.environ.pop("QUEUE_FAILURE_MODE", None)
            settings = Settings.from_env()
            self.assertEqual(settings.deployment_profile, "multi")
            self.assertEqual(settings.queue_failure_mode, "fail_closed")
        finally:
            os.environ.clear()
            os.environ.update(original)

    def test_multi_requires_postgres_redis_and_fail_closed(self) -> None:
        # Validation lives in Settings.validate() (called by from_env and
        # create_app); assert the multi-profile contract there directly.
        with self.assertRaises(ValueError):
            Settings(deployment_profile="multi", database_backend="sqlite").validate()
        with self.assertRaises(ValueError):
            Settings(
                deployment_profile="multi",
                database_backend="postgresql",
                database_url="postgresql://x",
                queue_backend="sqlite",
            ).validate()
        with self.assertRaises(ValueError):
            Settings(
                deployment_profile="multi",
                database_backend="postgresql",
                database_url="postgresql://x",
                queue_backend="redis",
                redis_url=REDIS_URL,
                queue_failure_mode="fallback",
            ).validate()

    def test_invalid_profile_and_failure_mode_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Settings(deployment_profile="cluster").validate()
        with self.assertRaises(ValueError):
            Settings(queue_failure_mode="silent").validate()


class FailClosedQueueTests(unittest.TestCase):
    """40.3: probe/readiness surface and fail-closed semantics."""

    def setUp(self) -> None:
        self._tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self._tmp.close()
        self.db = Database(Path(self._tmp.name))
        self.db.initialize()

    def tearDown(self) -> None:
        self.db.close()
        Path(self._tmp.name).unlink(missing_ok=True)

    def test_sqlite_queue_is_always_ready(self) -> None:
        queue = SQLiteTaskQueue(self.db)
        self.assertEqual(queue.backend_name, "sqlite")
        self.assertTrue(queue.is_ready())
        self.assertIsNone(queue.degraded_reason)
        self.assertGreater(queue.last_success_epoch, 0)

    def test_create_task_queue_defaults_to_sqlite(self) -> None:
        queue = create_task_queue(self.db, Settings())
        self.assertIsInstance(queue, SQLiteTaskQueue)

    def test_create_task_queue_redis_fail_closed_keeps_app_bootable(self) -> None:
        """A down Redis must not crash app startup; the queue reports itself."""
        queue = create_task_queue(
            self.db,
            Settings(queue_backend="redis", redis_url=REDIS_URL, queue_failure_mode="fail_closed"),
        )
        self.assertIsInstance(queue, RedisTaskQueue)
        if not queue.is_ready():
            # Redis down: degraded but not raised at construction.
            self.assertIsNotNone(queue.degraded_reason)

    def test_fail_closed_redis_raises_on_operations_when_down(self) -> None:
        queue = create_task_queue(
            self.db,
            Settings(queue_backend="redis", redis_url=REDIS_URL, queue_failure_mode="fail_closed"),
        )
        if queue.is_ready():
            self.skipTest("Redis is up; failure path not exercisable")
        # Seed a real tenant/conversation so the DB leg of enqueue succeeds and
        # the failure surfaces where REL-001 expects it: the Redis dispatch
        # push must fail closed, not the schema's conversation tenant guard.
        self.db.ensure_tenant("tenant-1", "Tenant One")
        conv = self.db.create_conversation("tenant-1", "Queue Down", None, "web_chat", "admin", 120)
        with self.assertRaises(QueueUnavailableError):
            queue.enqueue("tenant-1", conv["id"], "key-1", "a1", "msg", 3)

    def test_fallback_mode_degrades_to_sqlite_when_redis_down(self) -> None:
        queue = create_task_queue(self.db, Settings(queue_backend="redis", redis_url=REDIS_URL))
        if queue.is_ready():
            self.skipTest("Redis is up; fallback path not exercisable")
        self.assertIsInstance(queue, SQLiteTaskQueue)


@unittest.skipUnless(
    not _redis_reachable(), "Requires Redis to be DOWN (set REDIS_URL to a dead port)"
)
@unittest.skipIf(
    _psycopg2_importable(),
    "Requires psycopg2 ABSENT so the PostgreSQL pool constructs without connecting; "
    "the fail-closed API contract is exercised against the queue layer, not a live PG",
)
class FailClosedApiTests(unittest.TestCase):
    """40.3: API answers 503 + Retry-After while the queue is down."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "failclosed.db"
        self.app = create_app(
            Settings(
                database_path=self.db_path,
                deployment_profile="multi",
                database_backend="postgresql",
                database_url="postgresql://unused-for-sqlite-tests",
                queue_backend="redis",
                redis_url=REDIS_URL,
                queue_failure_mode="fail_closed",
                enable_session_auth=False,
                turn_worker_enabled=True,
            )
        )
        self.client = TestClient(self.app)
        self.services = cast(Any, self.app.state).services

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_readiness_reports_degraded_queue(self) -> None:
        response = self.client.get("/health/ready")
        body = response.json()
        self.assertEqual(body["queue"]["ready"], False)
        self.assertIsNotNone(body["queue"]["degraded_reason"])

    def test_message_post_returns_503_with_retry_after(self) -> None:
        headers = {"X-API-Key": "helix-demo-key", "X-Tenant-Id": "demo"}
        conv = self.services.database.create_conversation(
            "demo", "Fail Closed", "CUST-FC-1", "web", "admin", 120
        )
        response = self.client.post(
            f"/api/conversations/{conv['id']}/messages",
            headers=headers,
            json={"content": "hello"},
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.headers["Retry-After"], "30")
        self.assertEqual(response.json()["code"], "queue_unavailable")

    def test_diagnostics_reports_queue_failure_mode(self) -> None:
        response = self.client.get(
            "/api/admin/diagnostics",
            headers={"X-API-Key": "helix-demo-key", "X-Tenant-Id": "demo"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["config"]["deployment_profile"], "multi")
        self.assertEqual(body["config"]["queue_failure_mode"], "fail_closed")
        self.assertEqual(body["queue"]["backend"], "redis")
        self.assertEqual(body["queue"]["failure_mode"], "fail_closed")
