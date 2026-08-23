from __future__ import annotations

import logging
import os
import threading
import time
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from app.context import maintenance_scope, request_id_context, tenant_scope
from app.database import Database, utc_after_seconds
from app.orchestrator import (
    ConversationOrchestrator,
    InvalidTransitionError,
    TurnInProgressError,
)
from app.queue import QueueUnavailableError, SQLiteTaskQueue, TaskQueue
from app.telemetry import metrics as telemetry_metrics
from app.webhooks import WebhookService


logger = logging.getLogger("helix")


def _is_cjk(ch: str) -> bool:
    """True for CJK ideographs and CJK punctuation (kept as single tokens)."""
    cp = ord(ch)
    return (
        0x3400 <= cp <= 0x4DBF  # CJK Unified Ideographs Extension A
        or 0x4E00 <= cp <= 0x9FFF  # CJK Unified Ideographs
        or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility Ideographs
        or 0x3000 <= cp <= 0x303F  # CJK Symbols and Punctuation
        or 0xFF00 <= cp <= 0xFFEF  # Fullwidth Forms
    )


def split_stream_tokens(text: str) -> list[str]:
    """Split assistant output into streamable per-token chunks.

    English words (with attached punctuation) stay whole, each CJK character is
    its own token, and whitespace runs collapse to a single space.  The model
    provider is synchronous, so this reconstruction is what lets the SSE
    endpoint deliver "token by token" without re-architecting the provider.
    """
    tokens: list[str] = []
    word: list[str] = []
    for ch in text:
        if ch.isspace():
            if word:
                tokens.append("".join(word))
                word = []
            if tokens and tokens[-1] != " ":
                tokens.append(" ")
        elif _is_cjk(ch):
            if word:
                tokens.append("".join(word))
                word = []
            tokens.append(ch)
        else:
            word.append(ch)
    if word:
        tokens.append("".join(word))
    if tokens and tokens[0] == " ":
        tokens.pop(0)
    if tokens and tokens[-1] == " ":
        tokens.pop()
    return tokens


class TurnJobWorker:
    def __init__(
        self,
        database: Database,
        orchestrator: ConversationOrchestrator,
        *,
        queue: TaskQueue | None = None,
        enabled: bool = True,
        concurrency: int = 1,
        poll_interval_ms: int = 200,
        lease_seconds: int = 300,
        retry_base_seconds: int = 2,
        retention_days: int = 30,
        stream_enabled: bool = True,
        stream_pacing_ms: int = 10,
        webhook_service: WebhookService | None = None,
        webhook_interval_seconds: int = 30,
        recover_cadence_seconds: int = 15,
        archive_enabled: bool = True,
        archive_after_days: int = 180,
        archive_batch: int = 100,
        archive_cadence_hours: int = 6,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be positive")
        if poll_interval_ms < 10:
            raise ValueError("poll_interval_ms must be at least 10")
        if lease_seconds < 30:
            raise ValueError("lease_seconds must be at least 30")
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        if stream_pacing_ms < 0:
            raise ValueError("stream_pacing_ms cannot be negative")
        if webhook_interval_seconds < 5:
            raise ValueError("webhook_interval_seconds must be at least 5")
        if recover_cadence_seconds < 5:
            raise ValueError("recover_cadence_seconds must be at least 5")
        if archive_after_days < 7:
            raise ValueError("archive_after_days must be at least 7")
        if archive_batch < 1:
            raise ValueError("archive_batch must be positive")
        if archive_cadence_hours < 1:
            raise ValueError("archive_cadence_hours must be at least 1")
        self.database = database
        self.orchestrator = orchestrator
        self.queue = queue or SQLiteTaskQueue(database)
        self.enabled = enabled
        self.concurrency = concurrency
        self.poll_interval_seconds = poll_interval_ms / 1000
        self.lease_seconds = lease_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retention_days = retention_days
        self.stream_enabled = stream_enabled
        self.stream_pacing_seconds = stream_pacing_ms / 1000
        self.webhook_service = webhook_service
        self.webhook_interval_seconds = webhook_interval_seconds
        # Phase 29.1: after the initial ``recover()`` (start) we keep re-claiming
        # jobs whose leases expired while a peer worker/instance went away.  Only
        # doing it once at start would strand jobs when a whole instance is
        # killed during a rolling restart and nothing new starts within the
        # lease window.
        self.recover_cadence_seconds = recover_cadence_seconds
        # ROADMAP 18.3: resolved conversations older than ``archive_after_days``
        # are moved to the archive tier on a fixed cadence in bounded batches.
        self.archive_enabled = archive_enabled
        self.archive_after_days = archive_after_days
        self.archive_batch = archive_batch
        self.archive_cadence_seconds = archive_cadence_hours * 3600
        # Backlog (报表导出与订阅): set by ``create_app``; None keeps the
        # worker loop safe when constructed directly (tests, drills).
        self.report_service: Any | None = None
        # M0 SEC-002/SEC-001: hourly housekeeping callables (DSR export-object
        # pruning, OIDC transaction pruning) registered by ``create_app``;
        # failures are logged and never stop the worker loop.
        self.housekeeping: list[Callable[[], None]] = []
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._processed = 0
        self._completed = 0
        self._retried = 0
        self._failed = 0
        self._pruned = 0
        self._next_prune_at = 0.0
        self._next_recover_at = 0.0
        self._next_webhook_at = 0.0
        self._next_archive_at = 0.0
        self._archived = 0
        self._recovered = {"queued": 0, "failed": 0}
        self._cancelled_streams: set[str] = set()
        # ROADMAP 18.2c: job-start timestamps feed the TTFT metric; the
        # streamed-jobs set records turns that wrote their chunks through the
        # generate-as-you-write sink so the post-``handle`` path skips them.
        self._job_started_monotonic: dict[str, float] = {}
        self._streamed_jobs: set[str] = set()

    def start(self) -> None:
        with self._lock:
            if any(thread.is_alive() for thread in self._threads):
                return
            self._stop.clear()
            self._wake.clear()
            # 43.2: startup recovery/pruning is cross-tenant system work.
            with maintenance_scope("turn-worker-start"):
                try:
                    self._recovered = self.queue.recover(self.lease_seconds)
                except Exception as exc:
                    # M0 REL-001: a fail-closed queue that died between the
                    # readiness check and this startup must not spawn workers.
                    logger.error("turn_worker.queue_unavailable_on_start: %s", exc)
                    self._threads = []
                    return
                self._pruned += self.database.prune_turn_jobs(self.retention_days)
            self._next_prune_at = monotonic() + 3600
            self._next_archive_at = monotonic() + max(60, self.archive_cadence_seconds)
            if not self.enabled:
                self._threads = []
                return
            instance = f"{os.getpid()}-{uuid4().hex[:8]}"
            self._threads = [
                threading.Thread(
                    target=self._run,
                    args=(f"turn-worker-{instance}-{index + 1}",),
                    name=f"helix-turn-worker-{index + 1}",
                    daemon=True,
                )
                for index in range(self.concurrency)
            ]
            for thread in self._threads:
                thread.start()

    def stop(self, timeout_seconds: float = 30) -> None:
        self._stop.set()
        self._wake.set()
        deadline = monotonic() + max(0.0, timeout_seconds)
        with self._lock:
            threads = list(self._threads)
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - monotonic()))
        with self._lock:
            self._threads = [thread for thread in threads if thread.is_alive()]
        if self._threads:
            logger.warning(
                "turn_worker.shutdown_timeout",
                extra={"active_workers": len(self._threads)},
            )

    @property
    def is_stopping(self) -> bool:
        """True while the worker is shutting down (Phase 29.1).

        SSE endpoints check this and emit a reconnect hint so clients drain
        before the process exits, instead of hitting a dropped connection.
        """
        return self._stop.is_set()

    def notify(self) -> None:
        self._wake.set()

    def cancel_stream(self, job_id: str) -> None:
        """Mark a turn's stream as cancelled (Phase 19.5).

        Called when the SSE client disconnects: the turn itself has already
        completed durably, but remaining paced chunks are not written and the
        cancellation is audited by ``_write_stream_chunks``. Safe to call
        before, during, or after chunk writing.
        """
        with self._lock:
            self._cancelled_streams.add(job_id)

    def _run(self, worker_id: str) -> None:
        while not self._stop.is_set():
            # 43.2: everything in a worker cycle except the per-job business
            # logic is cross-tenant scheduling (claim/recover/prune/deliver/
            # archive); run it under an explicit maintenance scope so RLS
            # enforcement sees system bookkeeping, not tenant data access.
            with maintenance_scope("turn-worker-loop"):
                try:
                    if self.run_once(worker_id):
                        continue
                except QueueUnavailableError as exc:
                    # M0 REL-001: fail-closed backoff — retry promptly (≤1 s) so
                    # the worker resumes as soon as the backend is reachable.
                    logger.error("turn_worker.queue_unavailable: %s", exc)
                    self._wake.wait(min(self.poll_interval_seconds, 1.0))
                    self._wake.clear()
                    continue
                self._recover_if_due()
                self._prune_if_due()
                self._archive_if_due()
                self._webhook_housekeeping()
            self._wake.wait(self.poll_interval_seconds)
            self._wake.clear()

    def _webhook_housekeeping(self) -> None:
        """Emit SLA-breach events and deliver due webhook payloads (Phase 20.5).

        Runs from the worker loop on a fixed cadence; failures are logged and
        never crash the worker. Multi-instance safe: SLA breaches are
        deduplicated by their per-conversation event id and deliveries are
        claimed atomically before POSTing.
        """
        if self.webhook_service is None:
            return
        now = monotonic()
        with self._lock:
            if now < self._next_webhook_at:
                return
            self._next_webhook_at = now + self.webhook_interval_seconds
        try:
            self.webhook_service.check_sla_breaches()
        except Exception:
            logger.exception("webhook.sla_check_failed")
        try:
            # Backlog: pre-breach warning scan (sla_due_at within window).
            self.webhook_service.check_sla_impending()
        except Exception:
            logger.exception("webhook.sla_impending_failed")
        try:
            # Backlog: scheduled report generation (报表导出与订阅).
            if self.report_service is not None:
                self.report_service.run_due_subscriptions()
        except Exception:
            logger.exception("report.scan_failed")
        try:
            self.webhook_service.deliver_pending()
        except Exception:
            logger.exception("webhook.deliver_failed")

    def _recover_if_due(self) -> None:
        """Re-queue jobs whose leases expired since the last pass (29.1).

        Runs every ``recover_cadence_seconds`` from the worker loop; after a
        peer instance is killed, its claimed-but-unfinished jobs become
        recoverable once their lease expires and any surviving worker re-queues
        them. Failures are logged and never crash the worker.
        """
        now = monotonic()
        with self._lock:
            if now < self._next_recover_at:
                return
            self._next_recover_at = now + self.recover_cadence_seconds
        try:
            recovered = self.queue.recover(self.lease_seconds)
        except Exception:
            logger.exception("turn_worker.recover_failed")
            return
        with self._lock:
            self._recovered = recovered

    def _prune_if_due(self) -> None:
        now = monotonic()
        with self._lock:
            if now < self._next_prune_at:
                return
            self._next_prune_at = now + 3600
        try:
            pruned = self.database.prune_turn_jobs(self.retention_days)
        except Exception:
            logger.exception("turn_job.prune_failed")
            return
        with self._lock:
            self._pruned += pruned
        for callback in self.housekeeping:
            try:
                callback()
            except Exception:
                logger.exception("turn_worker.housekeeping_failed")

    def archive_once(self) -> int:
        """Archive one bounded batch of old resolved conversations (ROADMAP 18.3).

        Returns the number of conversations moved to the cold tier and adds
        them to ``snapshot()['archived_total']``.  Safe to call directly
        (tests, drills) and idempotent across replays.
        """
        if not self.archive_enabled:
            return 0
        # 43.2: the retention sweep scans every tenant's resolved rows.
        with maintenance_scope("conversation-archive"):
            cutoff = utc_after_seconds(-self.archive_after_days * 86400)
            archived = self.database.archive_resolved_conversations(
                cutoff, self.archive_batch
            )
        with self._lock:
            self._archived += archived
        return archived

    def _archive_if_due(self) -> None:
        now = monotonic()
        with self._lock:
            if now < self._next_archive_at:
                return
            self._next_archive_at = now + max(60, self.archive_cadence_seconds)
        try:
            self.archive_once()
        except Exception:
            logger.exception("archive.run_failed")

    def run_once(self, worker_id: str = "turn-worker-manual") -> bool:
        # 43.2: claiming/committing a job is cross-tenant queue bookkeeping;
        # the orchestrator call below narrows to the job's own tenant.
        with maintenance_scope("turn-job-scheduling"):
            return self._run_once_scoped(worker_id)

    def _run_once_scoped(self, worker_id: str) -> bool:
        job = self.queue.dequeue(worker_id, self.lease_seconds)
        if job is None:
            return False
        with self._lock:
            self._processed += 1
        if job["attempts"] > 1:
            # Re-claimed after an abandoned run: the previous worker may have
            # written partial chunks before it died. Clear them so the re-run
            # produces exactly one chunk set; otherwise the SSE client would
            # see the reply twice. (Phase 29.x)
            self.database.delete_turn_job_chunks(job["id"])
        self._job_started_monotonic[job["id"]] = monotonic()
        # 42.6: re-apply the originating request's trace context so every
        # audit event and outbound webhook emitted while processing this job
        # carries the same correlation id the customer-facing request used.
        request_id = job.get("request_id")
        context_token = request_id_context.set(request_id) if request_id else None
        try:
            try:
                # 43.2: the turn itself is single-tenant business logic —
                # messages/conversations/knowledge access runs inside the
                # job row's tenant scope (the durable source of identity,
                # not caller-supplied input).
                with tenant_scope(job["tenant_id"]):
                    response = self.orchestrator.handle_customer_message(
                        job["tenant_id"],
                        job["conversation_id"],
                        job["content"],
                        job["actor_id"],
                        job["idempotency_key"],
                        channel_message_id=job.get("channel_message_id"),
                        chunk_sink=self._chunk_sink_for(job),
                    )
            except Exception as exc:
                self._job_started_monotonic.pop(job["id"], None)
                self._streamed_jobs.discard(job["id"])
                retryable = isinstance(exc, TurnInProgressError) or not isinstance(
                    exc, (InvalidTransitionError, LookupError, ValueError)
                )
                failed_job = self.queue.fail(
                    job["id"],
                    worker_id,
                    type(exc).__name__,
                    self.retry_base_seconds,
                    self.lease_seconds,
                    retryable=retryable,
                )
                if failed_job:
                    terminal = failed_job["status"] == "failed"
                    with self._lock:
                        if terminal:
                            self._failed += 1
                        else:
                            self._retried += 1
                    event_type = "turn_job.failed" if terminal else "turn_job.retry_scheduled"
                    self._safe_audit(
                        failed_job,
                        event_type,
                        {"job_id": job["id"], "error_code": type(exc).__name__},
                    )
                logger.warning(
                    "turn_job.execution_failed",
                    extra={"job_id": job["id"], "error_code": type(exc).__name__},
                )
                return True

            self._write_stream_chunks(job, response)
            self._job_started_monotonic.pop(job["id"], None)
            completed = self.queue.complete(job["id"], worker_id, response, self.lease_seconds)
            if completed:
                with self._lock:
                    self._completed += 1
                self._safe_audit(
                    job,
                    "turn_job.completed",
                    {"job_id": job["id"], "attempts": job["attempts"]},
                )
            else:
                logger.warning("turn_job.lease_lost", extra={"job_id": job["id"]})
            return True
        finally:
            if context_token is not None:
                request_id_context.reset(context_token)

    def _chunk_sink_for(self, job: dict[str, Any]) -> Callable[[str], None]:
        """Return the orchestrator callback for generate-as-you-write (18.2c).

        ``handle_customer_message`` calls it the moment the assistant reply is
        durable, so the first token is written before the rest of the pipeline
        (quality aggregate, job completion) finishes and the SSE endpoint can
        deliver it immediately.
        """

        def sink(content: str) -> None:
            if self._write_chunks(job, content):
                with self._lock:
                    self._streamed_jobs.add(job["id"])

        return sink

    def _write_chunks(self, job: dict[str, Any], content: str) -> bool:
        """Persist one assistant reply as ordered streaming chunks.

        Shared by the generate-as-you-write sink (called from inside
        ``handle_customer_message``) and the post-``handle`` fallback path, so
        both keep an identical tokenization, cancellation contract, and pacing
        behavior. The first chunk is written immediately; pacing applies to the
        remainder only on deterministic (non-streaming) paths — a real provider
        stream would hand each token straight here with no pacing. Returns True
        when at least one chunk was written.
        """
        if not self.stream_enabled:
            return False
        tokens = split_stream_tokens(content or "")
        if not tokens:
            return False
        for index, token in enumerate(tokens):
            if job["id"] in self._cancelled_streams:
                # Client disconnected mid-stream: stop writing paced chunks.
                # The reply is already durable in messages; only the streamed
                # reconstruction is cut short. (Phase 19.5)
                self._safe_audit(
                    job,
                    "turn_job.stream_cancelled",
                    {"job_id": job["id"], "written_chunks": index},
                )
                return index > 0
            try:
                self.database.append_turn_job_chunk(
                    job["tenant_id"],
                    job["conversation_id"],
                    job["id"],
                    token,
                )
            except Exception:
                logger.exception("turn_job.chunk_write_failed", extra={"job_id": job["id"]})
                return index > 0
            if index == 0:
                # ROADMAP 18.2c: time to first token, from job dequeue to the
                # first durable chunk. P95 drives the §18.2c acceptance gate.
                started = self._job_started_monotonic.pop(job["id"], None)
                if started is not None:
                    telemetry_metrics.observe(
                        "turn.ttft_ms",
                        max(0, int((monotonic() - started) * 1000)),
                    )
            if index < len(tokens) - 1 and self.stream_pacing_seconds > 0:
                time.sleep(self.stream_pacing_seconds)
        return True

    def _write_stream_chunks(self, job: dict[str, Any], response: dict[str, Any]) -> None:
        """Persist the assistant reply as ordered, paced streaming chunks.

        Post-``handle_customer_message`` fallback path (ROADMAP 18.2c): when the
        reply was already streamed through the generate-as-you-write sink during
        ``handle_customer_message``, this is a no-op. Otherwise it runs after the
        turn succeeded and before the job is marked completed, so the SSE endpoint
        observes tokens arriving one by one and only then the terminal ``job``
        event.  The full reply stays in ``response_json`` for the non-streaming
        path and for idempotent replays. A chunk-write failure is logged and
        ignored: the reply itself is already durable in ``messages``, so the job
        still completes normally.
        """
        if not self.stream_enabled:
            return
        if response.get("idempotent_replay"):
            # The job was already processed; its chunks were written on the
            # first run and must not be duplicated.
            return
        if job["id"] in self._streamed_jobs:
            # Streamed during ``handle_customer_message``; nothing left to
            # write (the sink marked the job). Discard the marker now that this
            # is the only time we look at it.
            self._streamed_jobs.discard(job["id"])
            return
        assistant = response.get("assistant_message")
        if not assistant:
            return
        self._write_chunks(job, assistant.get("content") or "")

    def _safe_audit(
        self,
        job: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        try:
            self.database.audit(
                job["tenant_id"],
                job["conversation_id"],
                "turn-worker",
                event_type,
                payload,
            )
        except Exception:
            logger.exception("turn_job.audit_failed", extra={"job_id": job["id"]})

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "enabled": self.enabled,
                "configured_workers": self.concurrency,
                "active_workers": sum(thread.is_alive() for thread in self._threads),
                "processed_total": self._processed,
                "completed_total": self._completed,
                "retried_total": self._retried,
                "failed_total": self._failed,
                "pruned_total": self._pruned,
                "archived_total": self._archived,
                "recovered": dict(self._recovered),
            }
