"""Database jobs mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
import sqlite3
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.db._util import (
    utc_after_seconds,
    utc_now,
)


class DatabaseJobsMixin:
    def claim_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        processing_timeout_seconds: int,
    ) -> tuple[str, dict[str, Any] | None]:
        now = datetime.now(UTC)
        now_text = now.isoformat(timespec="seconds")
        with self.connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO turn_requests
                    (tenant_id, conversation_id, idempotency_key, status, created_at, updated_at)
                    VALUES (?, ?, ?, 'processing', ?, ?)""",
                    (tenant_id, conversation_id, idempotency_key, now_text, now_text),
                )
                return "new", None
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT * FROM turn_requests
                    WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                    (tenant_id, conversation_id, idempotency_key),
                ).fetchone()
                if row is None:
                    raise
                if row["status"] == "completed" and row["response_json"]:
                    return "completed", json.loads(row["response_json"])
                updated_at = datetime.fromisoformat(row["updated_at"])
                stale = (now - updated_at).total_seconds() >= processing_timeout_seconds
                if row["status"] == "failed" or stale:
                    connection.execute(
                        """UPDATE turn_requests
                        SET status = 'processing', response_json = NULL, error_code = NULL,
                            updated_at = ?
                        WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                        (now_text, tenant_id, conversation_id, idempotency_key),
                    )
                    return "new", None
                return "processing", None

    def get_turn_by_message_id(
        self, tenant_id: str, conversation_id: str, message_id: str
    ) -> dict[str, Any] | None:
        """Return a completed turn whose response contains ``message_id``.

        Used by channel-level idempotency (Phase 23.2): when a channel message
        id is replayed, the original turn response (customer message +
        assistant reply) is returned so the client sees the same result
        without a duplicate turn being processed.
        """
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT response_json FROM turn_requests
                WHERE tenant_id = ? AND conversation_id = ? AND status = 'completed'
                  AND response_json LIKE ?""",
                (tenant_id, conversation_id, f"%{message_id}%"),
            ).fetchall()
        for row in rows:
            try:
                response = json.loads(row["response_json"])
            except (TypeError, ValueError):
                continue
            customer = response.get("customer_message") or {}
            if customer.get("id") == message_id:
                return response
        return None

    def complete_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        response: dict[str, Any],
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE turn_requests
                SET status = 'completed', response_json = ?, error_code = NULL, updated_at = ?
                WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                (
                    json.dumps(response, ensure_ascii=False),
                    utc_now(),
                    tenant_id,
                    conversation_id,
                    idempotency_key,
                ),
            )

    def fail_turn(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        error_code: str,
    ) -> None:
        with self.connect() as connection:
            connection.execute(
                """UPDATE turn_requests
                SET status = 'failed', error_code = ?, updated_at = ?
                WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                (error_code[:80], utc_now(), tenant_id, conversation_id, idempotency_key),
            )

    def enqueue_turn_job(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        actor_id: str,
        content: str,
        max_attempts: int = 3,
        channel_message_id: str | None = None,
        request_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        job_id = f"job_{uuid4().hex[:12]}"
        now = utc_now()
        if request_id is None:
            # 42.6: default to the caller's trace context so the correlation
            # chain request → job → audit → webhook holds without every
            # call site having to thread the id through.
            from app.context import current_request_id

            request_id = current_request_id()
        with self.connect() as connection:
            try:
                connection.execute(
                    """INSERT INTO turn_jobs
                    (id, tenant_id, conversation_id, idempotency_key, actor_id, content,
                     channel_message_id, request_id,
                     status, attempts, max_attempts, available_at, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 0, ?, ?, ?, ?)""",
                    (
                        job_id,
                        tenant_id,
                        conversation_id,
                        idempotency_key,
                        actor_id,
                        content,
                        channel_message_id,
                        request_id,
                        max_attempts,
                        now,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM turn_jobs WHERE id = ?", (job_id,)
                ).fetchone()
                if row is None:
                    raise RuntimeError("Queued turn job could not be read back")
                return dict(row), False
            except sqlite3.IntegrityError:
                row = connection.execute(
                    """SELECT * FROM turn_jobs
                    WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                    (tenant_id, conversation_id, idempotency_key),
                ).fetchone()
                if row is None:
                    raise
                return dict(row), True

    def get_turn_job_by_idempotency(
        self, tenant_id: str, conversation_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM turn_jobs
                WHERE tenant_id = ? AND conversation_id = ? AND idempotency_key = ?""",
                (tenant_id, conversation_id, idempotency_key),
            ).fetchone()
        return dict(row) if row else None

    def get_turn_job(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turn_jobs WHERE tenant_id = ? AND id = ?",
                (tenant_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def get_latest_turn_job(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None:
        """Return the most recently created turn job for a conversation (23.1)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM turn_jobs WHERE tenant_id = ? AND conversation_id = ? "
                "ORDER BY created_at DESC, id DESC LIMIT 1",
                (tenant_id, conversation_id),
            ).fetchone()
        return dict(row) if row else None

    def list_turn_jobs(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        limit = max(1, limit)
        offset = max(0, offset)
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if status:
            clauses.append("status = ?")
            values.append(status)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id, tenant_id, conversation_id, status, attempts, max_attempts,
                    available_at, locked_at, error_code, created_at, updated_at, completed_at
                FROM turn_jobs
                WHERE {" AND ".join(clauses)}
                ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?""",
                [*values, limit, offset],
            ).fetchall()
        return [dict(row) for row in rows]

    def append_turn_job_chunk(
        self,
        tenant_id: str,
        conversation_id: str,
        job_id: str,
        content: str,
    ) -> dict[str, Any]:
        """Persist one streaming output chunk for a turn job.

        ``seq`` is filled by the ``turn_job_chunks_seq_fill`` trigger from the
        insertion rowid (SQLite) or the ``turn_job_chunks_seq`` default
        (PostgreSQL), so chunks read back in exactly the order they were
        written, across every backend.  The INSERT deliberately omits ``seq``:
        passing an explicit ``0`` (as the schema default reads) would bypass
        the PG ``nextval`` default and leave every chunk at ``seq=0``.
        """
        if not content:
            raise ValueError("chunk content cannot be empty")
        chunk_id = f"chk_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO turn_job_chunks
                (id, tenant_id, conversation_id, job_id, content, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (chunk_id, tenant_id, conversation_id, job_id, content, now),
            )
            row = connection.execute(
                "SELECT * FROM turn_job_chunks WHERE id = ?", (chunk_id,)
            ).fetchone()
        if row is None:
            raise RuntimeError("Appended turn job chunk could not be read back")
        return dict(row)

    def delete_turn_job_chunks(self, job_id: str) -> int:
        """Remove a job's streamed chunks (re-run cleanup).

        A job re-claimed after an abandoned run may carry partial chunks from
        the worker that died mid-stream; the re-run must not double the SSE
        output, so the worker clears them before re-executing the turn.
        """
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM turn_job_chunks WHERE job_id = ?", (job_id,))
        return cursor.rowcount

    def list_turn_job_chunks(
        self,
        tenant_id: str,
        job_id: str,
        after_seq: int = 0,
    ) -> list[dict[str, Any]]:
        """Return streamed chunks for a job, oldest first.

        ``after_seq`` enables resumable polling: the SSE endpoint passes the
        last ``seq`` it emitted and only receives newer chunks.
        """
        after_seq = max(0, after_seq)
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, tenant_id, conversation_id, job_id, seq, content, created_at
                FROM turn_job_chunks
                WHERE tenant_id = ? AND job_id = ? AND seq > ?
                ORDER BY seq""",
                (tenant_id, job_id, after_seq),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim_next_turn_job(
        self,
        worker_id: str,
        lease_seconds: int,
    ) -> dict[str, Any] | None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        stale_at = utc_after_seconds(-lease_seconds)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE turn_jobs
                SET status = 'failed', error_code = 'lease_expired',
                    locked_at = NULL, locked_by = NULL, updated_at = ?, completed_at = ?
                WHERE status = 'processing' AND attempts >= max_attempts
                  AND (locked_at IS NULL OR locked_at <= ?)""",
                (now, now, stale_at),
            )
            row = connection.execute(
                """SELECT j.*
                FROM turn_jobs j
                JOIN conversations c
                  ON c.id = j.conversation_id AND c.tenant_id = j.tenant_id
                WHERE j.attempts < j.max_attempts
                  AND (
                    (j.status = 'queued' AND j.available_at <= ?)
                    OR (j.status = 'processing' AND (j.locked_at IS NULL OR j.locked_at <= ?))
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM turn_jobs active
                    WHERE active.tenant_id = j.tenant_id
                      AND active.conversation_id = j.conversation_id
                      AND active.id != j.id
                      AND active.status = 'processing'
                      AND active.locked_at > ?
                  )
                ORDER BY CASE c.priority WHEN 'high' THEN 0 ELSE 1 END,
                         j.available_at, j.created_at, j.id
                LIMIT 1""",
                (now, stale_at, stale_at),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """UPDATE turn_jobs
                SET status = 'processing', attempts = attempts + 1,
                    locked_at = ?, locked_by = ?, updated_at = ?
                WHERE id = ?""",
                (now, worker_id, now, row["id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM turn_jobs WHERE id = ?", (row["id"],)
            ).fetchone()
        return dict(claimed) if claimed else None

    def claim_turn_job_by_id(
        self, job_id: str, worker_id: str, lease_seconds: int
    ) -> dict[str, Any] | None:
        """Atomically claim a specific turn job by id.

        Keeps the same invariants as :meth:`claim_next_turn_job` — per-
        conversation serialization, lease expiry, attempts ceiling — but targets
        a single job, for the Redis queue to reconcile a job that was atomically
        handed out from its dispatch list.
        """
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        stale_at = utc_after_seconds(-lease_seconds)
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE turn_jobs
                SET status = 'processing', attempts = attempts + 1,
                    locked_at = ?, locked_by = ?, updated_at = ?
                WHERE id = ?
                  AND attempts < max_attempts
                  AND (
                    (status = 'queued' AND available_at <= ?)
                    OR (status = 'processing' AND (locked_at IS NULL OR locked_at <= ?))
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM turn_jobs active
                    WHERE active.tenant_id = turn_jobs.tenant_id
                      AND active.conversation_id = turn_jobs.conversation_id
                      AND active.id != turn_jobs.id
                      AND active.status = 'processing'
                      AND active.locked_at > ?
                  )""",
                (now, worker_id, now, job_id, now, stale_at, stale_at),
            )
            claimed = connection.execute(
                "SELECT * FROM turn_jobs WHERE id = ? AND status = 'processing' AND locked_by = ?",
                (job_id, worker_id),
            ).fetchone()
        return dict(claimed) if claimed else None

    def complete_turn_job(
        self,
        job_id: str,
        worker_id: str,
        response: dict[str, Any],
        lease_seconds: int,
    ) -> bool:
        """Mark a claimed job completed under fresh-lease fencing (REL-001).

        The ``WHERE`` clause combines three guards: the row is still
        ``processing``, it belongs to ``worker_id`` (ownership), and its lease
        is still fresh (``locked_at > now - lease``). A worker whose lease
        expired and whose job was never re-claimed by another worker cannot
        complete it: a stale lease means the job may have already been
        re-dispatched, so accepting a late completion would clobber the
        re-run. ``cursor.rowcount`` distinguishes a clean completion (1) from a
        lost lease (0) without an extra read.
        """
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        stale_at = utc_after_seconds(-lease_seconds)
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE turn_jobs
                SET status = 'completed', response_json = ?, error_code = NULL,
                    locked_at = NULL, locked_by = NULL, updated_at = ?, completed_at = ?
                WHERE id = ? AND status = 'processing' AND locked_by = ?
                  AND locked_at > ?""",
                (json.dumps(response, ensure_ascii=False), now, now, job_id, worker_id, stale_at),
            )
        return cursor.rowcount == 1

    def fail_turn_job(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        retry_base_seconds: int,
        retryable: bool = True,
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None:
        """Mark a claimed job failed/re-queued under fresh-lease fencing (REL-001).

        Mirrors :meth:`complete_turn_job`: the SELECT and UPDATE both require a
        fresh lease (``locked_at > now - lease``) alongside the ownership guard
        (``locked_by = worker_id``). A worker past its lease cannot mutate a job
        its expired claim no longer owns, even when no peer has re-claimed it
        yet — the recovery sweep or a peer claim will. Returns ``None`` when the
        claim is no longer valid (stale lease, wrong owner, or already
        completed/failed).
        """
        if retry_base_seconds < 0:
            raise ValueError("retry_base_seconds cannot be negative")
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        stale_at = utc_after_seconds(-lease_seconds)
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM turn_jobs
                WHERE id = ? AND status = 'processing' AND locked_by = ?
                  AND locked_at > ?""",
                (job_id, worker_id, stale_at),
            ).fetchone()
            if row is None:
                return None
            attempts = int(row["attempts"])
            terminal = not retryable or attempts >= int(row["max_attempts"])
            status = "failed" if terminal else "queued"
            delay = min(300, retry_base_seconds * (2 ** max(0, attempts - 1)))
            updated = utc_after_seconds(delay)
            connection.execute(
                """UPDATE turn_jobs
                SET status = ?, available_at = ?, error_code = ?, locked_at = NULL,
                    locked_by = NULL, updated_at = ?, completed_at = CASE WHEN ? THEN ? ELSE NULL END
                WHERE id = ? AND status = 'processing' AND locked_by = ?
                  AND locked_at > ?""",
                (
                    status,
                    updated,
                    error_code[:80],
                    now,
                    int(terminal),
                    now if terminal else None,
                    job_id,
                    worker_id,
                    stale_at,
                ),
            )
            result = connection.execute(
                "SELECT * FROM turn_jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return dict(result) if result else None

    def retry_turn_job(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE turn_jobs
                SET status = 'queued', attempts = 0, available_at = ?, locked_at = NULL,
                    locked_by = NULL, response_json = NULL, error_code = NULL,
                    updated_at = ?, completed_at = NULL
                WHERE tenant_id = ? AND id = ? AND status = 'failed'""",
                (now, now, tenant_id, job_id),
            )
            if cursor.rowcount != 1:
                return None
            row = connection.execute(
                "SELECT * FROM turn_jobs WHERE tenant_id = ? AND id = ?",
                (tenant_id, job_id),
            ).fetchone()
        return dict(row) if row else None

    def recover_turn_jobs(self, lease_seconds: int) -> dict[str, int]:
        """Re-queue (or fail) jobs whose claims are stale.

        ``lease_seconds`` gates the recovery: only ``processing`` rows whose
        ``locked_at`` is older than the lease (or missing) are touched, so a
        peer worker's periodic recovery can never re-queue a healthy in-flight
        job.  The Redis queue re-dispatches the same stale jobs from its lease
        set; this is the database side of that handshake.
        """
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        now = utc_now()
        stale_at = utc_after_seconds(-lease_seconds)
        with self.connect() as connection:
            failed = connection.execute(
                """UPDATE turn_jobs
                SET status = 'failed', error_code = 'worker_restarted',
                    locked_at = NULL, locked_by = NULL, updated_at = ?, completed_at = ?
                WHERE status = 'processing' AND attempts >= max_attempts
                  AND (locked_at IS NULL OR locked_at <= ?)""",
                (now, now, stale_at),
            ).rowcount
            queued = connection.execute(
                """UPDATE turn_jobs
                SET status = 'queued', error_code = 'worker_restarted',
                    locked_at = NULL, locked_by = NULL, updated_at = ?
                WHERE status = 'processing' AND attempts < max_attempts
                  AND (locked_at IS NULL OR locked_at <= ?)""",
                (now, stale_at),
            ).rowcount
        return {"queued": queued, "failed": failed}

    def list_stale_queued_turn_jobs(self, limit: int = 500) -> list[dict[str, Any]]:
        """Return queued jobs past ``available_at`` for orphan reconciliation.

        The Redis queue only learns about jobs through its own dispatch list;
        a process killed between the DB ``enqueue_turn_job`` insert and the
        dispatch push strands a ``queued`` row forever.  Recovery compensates
        by re-pushing any such row that exists nowhere in Redis.  ``limit``
        bounds the scan; normal operation returns an empty list.
        """
        limit = max(1, limit)
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, tenant_id, conversation_id FROM turn_jobs
                WHERE status = 'queued' AND attempts < max_attempts
                  AND available_at <= ?
                ORDER BY available_at LIMIT ?""",
                (now, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def prune_turn_jobs(self, retention_days: int, batch_size: int = 5000) -> int:
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        if batch_size < 1:
            raise ValueError("batch_size must be positive")
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(timespec="seconds")
        with self.connect() as connection:
            cursor = connection.execute(
                """DELETE FROM turn_jobs WHERE id IN (
                    SELECT id FROM turn_jobs
                    WHERE status IN ('completed', 'failed')
                      AND completed_at IS NOT NULL AND completed_at < ?
                    ORDER BY completed_at LIMIT ?
                )""",
                (cutoff, batch_size),
            )
        return cursor.rowcount

    def turn_job_stats(self, tenant_id: str | None = None) -> dict[str, Any]:
        clauses = ""
        values: list[Any] = []
        if tenant_id is not None:
            clauses = " WHERE tenant_id = ?"
            values.append(tenant_id)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT status, COUNT(*) AS count
                FROM turn_jobs{clauses} GROUP BY status""",
                values,
            ).fetchall()
            oldest = connection.execute(
                f"""SELECT MIN(created_at) AS oldest_queued
                FROM turn_jobs{clauses}{" AND" if clauses else " WHERE"} status = 'queued'""",
                values,
            ).fetchone()["oldest_queued"]
        counts = {str(row["status"]): int(row["count"]) for row in rows}
        oldest_age_seconds = 0
        if oldest:
            oldest_age_seconds = max(
                0, int((datetime.now(UTC) - datetime.fromisoformat(oldest)).total_seconds())
            )
        return {
            "queued": counts.get("queued", 0),
            "processing": counts.get("processing", 0),
            "completed": counts.get("completed", 0),
            "failed": counts.get("failed", 0),
            "total": sum(counts.values()),
            "oldest_queued_age_seconds": oldest_age_seconds,
        }
