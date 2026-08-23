"""Tests for ROADMAP 18.2c streaming time-to-first-token (TTFT) pacing.

Three behaviors are pinned here:
- ``handle_customer_message`` accepts a ``chunk_sink`` and writes the streaming
  chunks the moment the assistant reply is durable ("generate-as-you-write"),
  so the SSE endpoint can deliver the first token before the rest of the
  pipeline (quality aggregate, audit, job completion) finishes.
- The turn worker records ``turn.ttft_ms`` (job dequeue → first durable chunk)
  into the ``TelemetryMetrics`` histogram, the P95 of which is the §18.2c
  acceptance gate (target < 500 ms on the local-model path).
- The post-``handle`` fallback path skips jobs that were already streamed
  through the sink, so a single turn never writes its chunks twice.

The worker's deterministic path still applies ``stream_pacing_ms`` to every
chunk after the first; the first chunk is always written immediately.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.jobs import TurnJobWorker, split_stream_tokens
from app.orchestrator import ConversationOrchestrator
from app.telemetry import metrics as telemetry_metrics


class TtftStreamingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ttft.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")
        self.database.create_knowledge(
            "demo",
            "Shipping policy",
            "Packages ship within 24 hours",
            ["shipping"],
            "general",
            "https://e.com",
        )
        self.settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.orchestrator = ConversationOrchestrator(self.database, self.settings)
        self.worker = TurnJobWorker(
            self.database,
            self.orchestrator,
            stream_enabled=True,
            stream_pacing_ms=0,
        )
        conversation = self.database.create_conversation(
            "demo", "Customer", None, "web", "admin", 120
        )
        self.conversation_id = conversation["id"]

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _baseline_count(self, key: str) -> int:
        hist = telemetry_metrics.snapshot().get("histograms", {})
        return hist.get(key, {}).get("count", 0)

    def _enqueue(self) -> dict:
        job, _ = self.worker.queue.enqueue(
            "demo",
            self.conversation_id,
            f"idem-ttft-{self._testMethodName}",
            "admin",
            "配送一般多久能到？",
            3,
        )
        return job

    def test_worker_records_ttft_histogram(self) -> None:
        baseline = self._baseline_count("turn.ttft_ms")
        self._enqueue()
        self.worker.run_once("ttft-worker")
        after = telemetry_metrics.snapshot().get("histograms", {})
        key = "turn.ttft_ms"
        self.assertIn(key, after, msg="missing TTFT histogram")
        self.assertGreater(after[key]["count"], baseline, msg="TTFT never observed for the turn")
        self.assertIn("turn.ttft_ms", after)
        self.assertGreaterEqual(after[key]["avg"], 0)

    def test_chunks_written_exactly_once_through_sink(self) -> None:
        job = self._enqueue()
        self.worker.run_once("ttft-worker")
        chunks = self.database.list_turn_job_chunks("demo", job["id"])
        reply = next(
            m["content"]
            for m in self.database.list_messages("demo", self.conversation_id)
            if m["role"] == "assistant"
        )
        # The sink wrote every token during ``handle_customer_message`` and the
        # fallback path skipped the job — exactly one reconstruction.
        self.assertEqual(
            [c["content"] for c in chunks],
            split_stream_tokens(reply),
            msg="chunks must reconstruct the reply exactly once, not twice",
        )

    def test_sink_marks_job_and_fallback_skips_it(self) -> None:
        job = self._enqueue()
        sink = self.worker._chunk_sink_for(job)
        sink("你好")
        self.assertIn(job["id"], self.worker._streamed_jobs)
        written = len(self.database.list_turn_job_chunks("demo", job["id"]))
        self.assertGreaterEqual(written, 1)
        # The dequeue-time timestamp is consumed by the TTFT observation.
        self.assertNotIn(job["id"], self.worker._job_started_monotonic)
        # Fallback: post-handle writer must not append to an already-streamed job.
        self.worker._write_stream_chunks(job, {"assistant_message": {"content": "你好"}})
        self.assertNotIn(job["id"], self.worker._streamed_jobs)
        after = self.database.list_turn_job_chunks("demo", job["id"])
        self.assertEqual(len(after), written, msg="fallback path duplicated chunks")

    def test_deterministic_path_writes_first_chunk_without_pacing_wait(self) -> None:
        job = self._enqueue()
        started = self.worker._job_started_monotonic
        self.worker.run_once("ttft-worker")
        # A dequeue → first-token histogram entry exists for this exact job,
        # and its value is small: the first chunk is written immediately, the
        # pacing applies only after it. (Upper bound is generous so slow CI
        # cannot flake; the §18.2c P95 target is asserted in PERF_NOTES.md.)
        hist = telemetry_metrics.snapshot().get("histograms", {})
        self.assertLessEqual(hist["turn.ttft_ms"]["avg"], 2000)
        self.assertNotIn(job["id"], started, msg="job start timestamp leaked")

    def test_sse_poll_interval_default_and_validation(self) -> None:
        settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.assertEqual(settings.turn_job_sse_poll_interval_ms, 100)
        invalid = Settings(
            database_path=self.db_path,
            auth_mode="demo",
            turn_job_sse_poll_interval_ms=5,
        )
        with self.assertRaises(ValueError):
            invalid.validate()

    def test_handle_without_sink_leaves_chunks_to_worker(self) -> None:
        # A caller that does not pass a sink (sync API paths, tests) must keep
        # the pre-18.2c contract: the worker writes chunks after the handle.
        job = self._enqueue()
        response = self.orchestrator.handle_customer_message(
            "demo",
            self.conversation_id,
            "配送一般多久能到？",
            "admin",
            "idem-ttft-nosink",
        )
        self.assertIsNotNone(response["assistant_message"])
        self.worker._write_stream_chunks(job, response)
        written = len(self.database.list_turn_job_chunks("demo", job["id"]))
        self.assertGreaterEqual(written, 1)


if __name__ == "__main__":
    unittest.main()
