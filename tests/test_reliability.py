"""Phase 29: reliability & overload tests.

Covers the Phase 29 acceptance criteria:
- 29.1 SSE endpoints advertise a reconnect interval and emit a shutdown event
  when the worker is stopping (drain signal);
- 29.2 backpressure: turn-job intake returns 429 + Retry-After when the queue
  depth or per-tenant concurrent cap is exceeded;
- graceful shutdown: TurnJobWorker.stop() completes in-flight turns without
  losing queued work (the queue is durable, so restart recovers).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "rel-admin-key-0001"


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"}}
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
        "turn_worker_enabled": True,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class BackpressureTests(unittest.TestCase):
    """29.2: intake is refused with 429 when the queue is overloaded."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "bp.db"
        # Disable the worker so queued jobs stay queued (deterministic depth).
        self.client = TestClient(
            create_app(
                _settings(
                    self.db_path,
                    tenant_concurrent_turn_cap=1,
                    turn_worker_enabled=False,
                )
            )
        )
        self.services = cast(Any, self.client.app).state.services
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_queue_depth_threshold_returns_429(self) -> None:
        db = self.services.database
        conv = db.create_conversation("demo", "C", None, "web", "admin", 120)
        # Cap=1: two queued jobs exceed the per-tenant concurrent cap.
        db.enqueue_turn_job("demo", conv["id"], "k-1", "admin", "hello", 3)
        db.enqueue_turn_job("demo", conv["id"], "k-2", "admin", "hello2", 3)
        response = self.client.post(
            f"/api/conversations/{conv['id']}/turn-jobs",
            json={"content": "third"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 429)
        self.assertIn("Retry-After", response.headers)

    def test_within_cap_is_accepted(self) -> None:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": "C"}, headers=self.headers
        ).json()
        response = self.client.post(
            f"/api/conversations/{conv['id']}/turn-jobs",
            json={"content": "hello"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 202, response.text)


class GracefulShutdownTests(unittest.TestCase):
    """29.1: worker stop is non-lossy; SSE advertises reconnect."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gs.db"
        self.client = TestClient(create_app(_settings(self.db_path, turn_worker_concurrency=2)))
        self.services = cast(Any, self.client.app).state.services
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_worker_stop_keeps_queued_jobs_durable(self) -> None:
        db = self.services.database
        conv = db.create_conversation("demo", "C", None, "web", "admin", 120)
        job, replayed = db.enqueue_turn_job("demo", conv["id"], "k-stop-1", "admin", "hi", 3)
        self.assertFalse(replayed)
        worker = self.services.turn_worker
        worker.stop()
        # The job is still queued in the DB (durable, not lost).
        stored = db.get_turn_job("demo", job["id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored["status"], "queued")

    def test_worker_is_stopping_flag(self) -> None:
        worker = self.services.turn_worker
        self.assertFalse(worker.is_stopping)
        worker.stop()
        self.assertTrue(worker.is_stopping)

    def test_sse_queue_stream_emits_reconnect_hint(self) -> None:
        # The queue SSE stream starts with a retry: hint line.
        conv = self.client.post(
            "/api/conversations", json={"customer_name": "C"}, headers=self.headers
        ).json()
        # Trigger at least one queue revision so a snapshot is emitted.
        self.client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "ORD-10482 到哪了"},
            headers={**self.headers, "Idempotency-Key": "rel-sse-1"},
        )
        with self.client.stream("GET", "/api/events/queue", headers=self.headers) as stream:
            first = stream.iter_lines().__next__()
            self.assertIn("retry: 2000", first)

    def test_turn_job_sse_emits_shutdown_event_when_stopping(self) -> None:
        db = self.services.database
        conv = db.create_conversation("demo", "C", None, "web", "admin", 120)
        job, _ = db.enqueue_turn_job("demo", conv["id"], "k-shut-1", "admin", "hi", 3)
        worker = self.services.turn_worker
        worker.stop()
        with self.client.stream(
            "GET", f"/api/turn-jobs/{job['id']}/events", headers=self.headers
        ) as stream:
            body = "".join(stream.iter_text())
        self.assertIn("shutdown", body)


if __name__ == "__main__":
    unittest.main()
