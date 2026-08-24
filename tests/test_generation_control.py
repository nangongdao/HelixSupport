"""Phase 19.5: generation control -- provider failover and stream cancellation.

Covers the two contracts the gate depends on:
- ``ChainedModelProvider`` fails over across an ordered provider chain and
  re-raises the last error when every provider fails, so the deterministic
  routing fallback still catches it.
- ``TurnJobWorker.cancel_stream`` stops paced chunk writing once the SSE
  client is gone and records a ``turn_job.stream_cancelled`` audit event.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from app.config import Settings
from app.database import Database
from app.jobs import TurnJobWorker
from app.model_provider import ChainedModelProvider, ModelProviderError
from app.orchestrator import ConversationOrchestrator
from app.queue import SQLiteTaskQueue


class _StaticProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
        self.calls += 1
        return self.text


class _FailingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str, model_ref: str | None = None) -> str:
        self.calls += 1
        raise ModelProviderError("provider failed")


class ChainedModelProviderTests(unittest.TestCase):
    def test_first_succeeds_no_failover(self) -> None:
        first = _StaticProvider("a")
        second = _StaticProvider("b")
        chain = ChainedModelProvider([first, second])
        self.assertEqual(chain.complete("sys", "usr"), "a")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 0)

    def test_failover_to_second_provider(self) -> None:
        first = _FailingProvider()
        second = _StaticProvider("ok")
        chain = ChainedModelProvider([first, second])
        self.assertEqual(chain.complete("sys", "usr"), "ok")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_all_fail_re_raises_last_error(self) -> None:
        first = _FailingProvider()
        second = _FailingProvider()
        chain = ChainedModelProvider([first, second])
        with self.assertRaises(ModelProviderError):
            chain.complete("sys", "usr")
        self.assertEqual(first.calls, 1)
        self.assertEqual(second.calls, 1)

    def test_empty_providers_rejected(self) -> None:
        with self.assertRaises(ValueError):
            ChainedModelProvider([])


class StreamCancellationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        self.database.initialize()
        self.database.seed_demo()
        self.orchestrator = ConversationOrchestrator(
            self.database, Settings(database_path=self.db_path, auth_mode="demo")
        )
        self.worker = TurnJobWorker(
            self.database,
            self.orchestrator,
            queue=SQLiteTaskQueue(self.database),
            stream_enabled=True,
            stream_pacing_ms=0,
        )
        conv = self.database.create_conversation("demo", "Customer", None, "web", "admin", 120)
        self.conv_id = conv["id"]

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def _make_job(self) -> dict:
        job, _ = self.worker.queue.enqueue(
            "demo",
            self.conv_id,
            f"idem-stream-{uuid4().hex[:6]}",
            "admin",
            "配送一般多久能到？",
            3,
        )
        return job

    def _audit_types(self) -> list[str]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id = ?",
                ("demo",),
            ).fetchall()
        return [r["event_type"] for r in rows]

    def test_cancel_stops_chunk_writing_and_audits(self) -> None:
        job = self._make_job()
        response = {"assistant_message": {"content": "一二三四五六七八九十"}}
        self.worker.cancel_stream(job["id"])
        self.worker._write_stream_chunks(job, response)
        chunks = self.database.list_turn_job_chunks("demo", job["id"])
        self.assertEqual(chunks, [])
        self.assertIn("turn_job.stream_cancelled", self._audit_types())

    def test_no_cancel_writes_all_chunks(self) -> None:
        job = self._make_job()
        response = {"assistant_message": {"content": "一二三四五"}}
        self.worker._write_stream_chunks(job, response)
        chunks = self.database.list_turn_job_chunks("demo", job["id"])
        self.assertGreaterEqual(len(chunks), 1)
        self.assertNotIn("turn_job.stream_cancelled", self._audit_types())

    def test_cancel_after_write_is_noop(self) -> None:
        # Cancelling after chunks were written neither adds chunks nor errors.
        job = self._make_job()
        response = {"assistant_message": {"content": "甲乙丙"}}
        self.worker._write_stream_chunks(job, response)
        written = len(self.database.list_turn_job_chunks("demo", job["id"]))
        self.worker.cancel_stream(job["id"])
        self.worker._write_stream_chunks(job, response)
        after = self.database.list_turn_job_chunks("demo", job["id"])
        # Replay guard skips the second write; chunk count is unchanged.
        self.assertEqual(len(after), written)


if __name__ == "__main__":
    unittest.main()
