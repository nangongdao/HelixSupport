"""Data-protection pipeline: DSR SLA, approval board, failure retry, tombstone.

Extends the ``RetentionService`` DSR workflow (M0 SEC-002) with the Phase 41.4
operations contract WITHOUT moving large deletions into the synchronous API
path:

- **SLA** — every request gets ``sla_due_at`` computed at creation and is
  flagged ``sla_breached`` when the due time passes without completion.
- **Approval board** — ``list_approved_pending`` returns the queue with SLA
  posture, maker-checker progress, and outcome so a human can decide
  approvals/retries before any execution happens.
- **Export checksum / deletion proof** — the export already carries
  ``content_sha256``; every executed deletion writes a proof row carrying a
  ``sha256`` of the per-request ``execution_secret`` (the only evidence, keyed
  by a caller-held secret, that survives the deletion itself).
- **Failure retry** — a failed execution flips the request to ``failed``
  (re-approvable, audited) instead of leaving it stuck ``approved``; the retry
  re-runs from the original approved id, idempotently.
- **Deferred large deletions** — a deletion whose estimated scope is large is
  dropped as a ``deferred_deletion_jobs`` row and returns an ``accepted``
  summary; the turn-worker housekeeping window drains jobs in bounded batches.
  Sync deletion remains only for small scopes.
- **Tombstone-after-restore** — completed deletions write ``customer_tombstones``
  keyed by (tenant, customer_reference); ``enforce_tombstones_after_restore``
  re-applies every recorded deletion at startup so a restored old backup can
  never resurrect erased customer data.

All SQL is parameterised and runs against both the SQLite ``Database`` and
``PostgresDatabase`` adapters; migration 32 owns the schema.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
from datetime import timedelta
from typing import Any, Callable

from app.audit_gap import audit_high_risk
from app.database import utc_now
from app.retention import RetentionService

logger = logging.getLogger(__name__)

DELETION_BATCH_SIZE = 50
# Scope beyond which a deletion is deferred instead of run synchronously.
DEFERRED_DELETION_THRESHOLD = 1000
# A failed job older than this is re-attempted instead of skipped as stale.
DELETION_JOB_STALE_SECONDS = 3600

_DSR_COLUMNS = (
    "sla_due_at",
    "sla_breached",
    "last_errored_at",
    "error_detail",
    "execution_secret",
)


def _ensure_dsr_columns(database: Any) -> None:
    """Add migration-32 DSR columns if a caller runs before its migration.

    Uses ``connect()`` so the same helper serves both backends; ``_ensure_column``
    tolerates absent tables and already-present columns.
    """
    from app.migrations import _ensure_column

    with database.connect() as conn:
        for column in _DSR_COLUMNS:
            _ensure_column(conn, "data_subject_requests", column, "TEXT")


class DsrExecutionError(RuntimeError):
    """A data-subject execution failed; caller decides whether to mark failed."""


class DeferredDeletionStore:
    """Persisted queue of deferred large deletions (housekeeping-drained).

    Keyed by (tenant_id, request_id): each approved DSR has at most one job.
    """

    _OPEN = ("approved", "retryable")

    def __init__(self, database: Any) -> None:
        self.database = database

    def enqueue(self, tenant_id: str, request_id: str, customer_ref: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO deferred_deletion_jobs "
                "(tenant_id, request_id, customer_ref, status, attempt, last_error, created_at) "
                "VALUES (?, ?, ?, 'approved', 0, '', ?)",
                (tenant_id, request_id, customer_ref, utc_now()),
            )

    def list_open(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            query = (
                "SELECT tenant_id, request_id, customer_ref, status, attempt, last_error "
                "FROM deferred_deletion_jobs WHERE status IN ('approved', 'retryable')"
            )
            params: tuple[str, ...] = ()
            if tenant_id:
                query += " AND tenant_id = ?"
                params = (tenant_id,)
            query += " ORDER BY created_at ASC"
            return [dict(row) for row in conn.execute(query, params).fetchall()]

    def _update(self, conn: Any, job: dict[str, Any]) -> None:
        conn.execute(
            "UPDATE deferred_deletion_jobs SET status = ?, attempt = ?, last_error = ? "
            "WHERE tenant_id = ? AND request_id = ?",
            (
                job["status"],
                job["attempt"],
                job["last_error"],
                job["tenant_id"],
                job["request_id"],
            ),
        )

    def mark_completed(self, job: dict[str, Any]) -> None:
        with self.database.connect() as conn:
            self._update(conn, {**job, "status": "completed", "last_error": ""})

    def mark_retryable(self, job: dict[str, Any], reason: str) -> None:
        with self.database.connect() as conn:
            self._update(
                conn,
                {**job, "status": "retryable", "attempt": job["attempt"] + 1, "last_error": reason},
            )


class DataProtectionService:
    """SLA, board, checksum/proof, and tombstone layers over RetentionService."""

    def __init__(
        self,
        retention: RetentionService,
        *,
        sla_minutes: int = 120,
        audit_high_risk: Callable[..., Any] | None = None,
    ) -> None:
        self.retention = retention
        self.database = retention.database
        self.sla_minutes = sla_minutes
        self._audit_high_risk: Any = audit_high_risk
        self._jobs = DeferredDeletionStore(self.database)
        _ensure_dsr_columns(self.database)

    # ------------------------------------------------------------------
    # DSR lifecycle: SLA-contract create + maker-checker execution
    # ------------------------------------------------------------------

    def create_data_subject_request(
        self,
        tenant_id: str,
        customer_ref: str,
        request_type: str,
        requested_by: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a request with SLA due time and a blind execution secret."""
        due = self._sla_due()
        secret = secrets.token_hex(16)
        created = self.retention.create_data_subject_request(
            tenant_id, customer_ref, request_type, requested_by, idempotency_key
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET sla_due_at = ?, execution_secret = ? "
                "WHERE id = ? AND tenant_id = ?",
                (due, secret, created["id"], tenant_id),
            )
        created["sla_due_at"] = due
        created["sla_breached"] = 0
        return created

    def execute_data_subject_request(
        self,
        tenant_id: str,
        request_id: str,
        executor: str,
    ) -> dict[str, Any]:
        """Execute an approved request; defers large deletions to the queue."""
        row = self._get_dsr(tenant_id, request_id)
        if row["status"] in ("completed", "failed"):
            summary = json.loads(row.get("execution_summary_json") or "{}")
            return {
                "request_id": request_id,
                "request_type": row["request_type"],
                "status": row["status"],
                "idempotent_replay": True,
                "summary": summary,
            }
        if row["status"] != "approved":
            raise ValueError(f"Request is {row['status']}, only approved requests can be executed")
        if row["request_type"] == "deletion":
            scope = self.estimate_deletion_size(tenant_id, row["customer_ref"])
            if scope >= DEFERRED_DELETION_THRESHOLD:
                self._jobs.enqueue(tenant_id, request_id, row["customer_ref"])
                self._audit(
                    tenant_id=tenant_id,
                    actor=executor,
                    event_type="data_subject_request.deferred",
                    payload={"request_id": request_id, "scope": scope},
                    reason="large deletion deferred to housekeeping queue",
                )
                return {
                    "request_id": request_id,
                    "request_type": "deletion",
                    "status": "accepted",
                    "deferred": True,
                    "scope": scope,
                }
        try:
            result = self.retention.execute_data_subject_request(tenant_id, request_id, executor)
        except Exception as exc:
            self.mark_request_failed(tenant_id, request_id, executor, str(exc))
            raise
        if result["request_type"] == "deletion":
            self.record_deletion_proof(
                tenant_id,
                row["customer_ref"],
                request_id,
                executor,
                self._execution_secret(tenant_id, request_id),
            )
        return result

    def _execution_secret(self, tenant_id: str, request_id: str) -> str:
        """Return the blind execution secret for a DSR ('' when absent)."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT execution_secret FROM data_subject_requests WHERE id = ? AND tenant_id = ?",
                (request_id, tenant_id),
            ).fetchone()
        if row is None:
            raise LookupError("Data subject request not found")
        return str(row["execution_secret"] or "")

    def retry_failed_request(self, tenant_id: str, request_id: str) -> dict[str, Any]:
        """Return a failed request to ``approved`` so it can re-execute."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id FROM data_subject_requests WHERE tenant_id = ? AND id = ?",
                (tenant_id, request_id),
            ).fetchone()
        if row is None:
            raise LookupError("Data subject request not found")
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET status = 'approved', last_errored_at = NULL, "
                "error_detail = NULL WHERE tenant_id = ? AND id = ?",
                (tenant_id, request_id),
            )
        self._audit(
            tenant_id=tenant_id,
            actor="retry",
            event_type="data_subject_request.retried",
            payload={"request_id": request_id},
            reason="failed request returned to approved for retry",
        )
        return {"id": request_id, "tenant_id": tenant_id, "status": "approved"}

    def mark_request_failed(
        self,
        tenant_id: str,
        request_id: str,
        actor: str,
        error_detail: str,
    ) -> None:
        """Flip an approved request to ``failed`` (re-approvable via retry)."""
        from datetime import datetime, timezone

        with self.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET status = 'failed', last_errored_at = ?, "
                "error_detail = ? WHERE id = ? AND tenant_id = ?",
                (utc_now(), error_detail[:1000], request_id, tenant_id),
            )
        self._audit(
            tenant_id=tenant_id,
            actor=actor,
            event_type="data_subject_request.failed",
            payload={
                "request_id": request_id,
                "error_head": error_detail[:200],
                "epoch": int(datetime.now(tz=timezone.utc).timestamp()),
            },
            reason="data subject request execution failed",
        )

    # ------------------------------------------------------------------
    # SLA
    # ------------------------------------------------------------------

    def _sla_due(self) -> str:
        return (utc_now_as_datetime() + timedelta(minutes=self.sla_minutes)).isoformat(
            timespec="seconds"
        )

    def flag_sla_breaches(self, tenant_id: str | None = None) -> int:
        """Flip ``sla_breached`` for any open request past its due time."""
        now = utc_now()
        with self.database.connect() as conn:
            where = " AND sla_due_at IS NOT NULL AND sla_due_at < ? "
            params: tuple[str, ...] = (now,)
            if tenant_id:
                where += "AND tenant_id = ? "
                params = (now, tenant_id)
            result = conn.execute(
                "UPDATE data_subject_requests SET sla_breached = 1"
                " WHERE status IN ('pending', 'approved', 'failed')"
                + where
                + "AND sla_breached = 0",
                params,
            )
            return int(result.rowcount or 0)

    def list_approved_pending(self, tenant_id: str) -> list[dict[str, Any]]:
        """Approval board rows: requests awaiting approval or execution."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, customer_ref, request_type, status, requested_by, "
                "approver, approved_at, executor, executed_at, sla_due_at, sla_breached, "
                "last_errored_at, error_detail, created_at, completed_at "
                "FROM data_subject_requests WHERE tenant_id = ? "
                "AND status IN ('pending', 'approved', 'failed') "
                "ORDER BY sla_breached DESC, sla_due_at ASC",
                (tenant_id,),
            ).fetchall()
        redacted: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["customer_ref"] = self._redact_ref(item["customer_ref"])
            redacted.append(item)
        return redacted

    def _redact_ref(self, customer_ref: str) -> str:
        """Board rows expose only a stable one-way fingerprint of the reference."""
        return hashlib.sha256(customer_ref.encode("utf-8")).hexdigest()[:16]

    def _get_dsr(self, tenant_id: str, request_id: str) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, customer_ref, request_type, status, "
                "requested_by, approver, approved_at, executor, executed_at, "
                "execution_summary_json, export_object_id, created_at, completed_at, "
                "sla_due_at, sla_breached, execution_secret "
                "FROM data_subject_requests WHERE id = ? AND tenant_id = ?",
                (request_id, tenant_id),
            ).fetchone()
        if row is None:
            raise LookupError("Data subject request not found")
        item = dict(row)
        item.pop("execution_secret", None)
        return item

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _audit(
        self,
        *,
        tenant_id: str,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        reason: str,
    ) -> str:
        if self._audit_high_risk is None:
            self._audit_high_risk = audit_high_risk
        return self._audit_high_risk(
            self.database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=actor,
            event_type=event_type,
            payload=payload,
            reason=reason,
        )

    # ------------------------------------------------------------------
    # Deletion proof (secret-keyed) + tombstone-after-restore
    # ------------------------------------------------------------------

    @staticmethod
    def _secret_hash(secret: str) -> str:
        return hashlib.sha256(secret.encode("utf-8")).hexdigest()

    def record_deletion_proof(
        self,
        tenant_id: str,
        customer_ref: str,
        request_id: str,
        deleted_by: str,
        secret: str,
    ) -> None:
        """Persist a deletion-attestation row; ``secret`` matches the DSR's own."""
        now = utc_now()
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO customer_tombstones "
                "(tenant_id, customer_ref, request_id, deleted_at, deleted_by, secret_hash) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (tenant_id, customer_ref) DO UPDATE SET "
                "request_id = excluded.request_id, deleted_at = excluded.deleted_at, "
                "deleted_by = excluded.deleted_by, secret_hash = excluded.secret_hash",
                (
                    tenant_id,
                    customer_ref,
                    request_id,
                    now,
                    deleted_by,
                    self._secret_hash(secret),
                ),
            )

    def deletion_proof(
        self, tenant_id: str, customer_ref: str, secret: str
    ) -> dict[str, Any] | None:
        """Return the proof row when ``secret`` matches; otherwise ``None``."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT request_id, deleted_at, deleted_by, secret_hash "
                "FROM customer_tombstones WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            ).fetchone()
        if row is None:
            return None
        proof = dict(row)
        if not hmac.compare_digest(proof["secret_hash"], self._secret_hash(secret)):
            return None
        attestation = {
            "tenant_id": tenant_id,
            "customer_ref": customer_ref,
            "request_id": proof["request_id"],
            "deleted_at": proof["deleted_at"],
            "deleted_by": proof["deleted_by"],
        }
        return attestation

    def list_tombstones(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT request_id, customer_ref, deleted_at, deleted_by "
                "FROM customer_tombstones WHERE tenant_id = ? ORDER BY deleted_at DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def enforce_tombstones_after_restore(self) -> dict[str, int]:
        """Re-apply every recorded deletion; returns per-request status counts."""
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT tenant_id, customer_ref, request_id FROM customer_tombstones "
                "ORDER BY deleted_at ASC"
            ).fetchall()
        if not rows:
            return {}
        results: dict[str, int] = {}
        for row in rows:
            tenant_id, customer_ref, request_id = (
                row["tenant_id"],
                row["customer_ref"],
                row["request_id"],
            )
            try:
                counts = self.retention.execute_data_subject_deletion(tenant_id, customer_ref)
            except Exception:
                logger.exception(
                    "restore.tombstone_failed tenant=%s request_id=%s", tenant_id, request_id
                )
                results["failed"] = results.get("failed", 0) + 1
                continue
            results["deleted"] = results.get("deleted", 0) + 1
            logger.info(
                "restore.tombstone_applied tenant=%s customer_ref=%s counts=%s",
                tenant_id,
                customer_ref,
                counts,
            )
        return results

    # ------------------------------------------------------------------
    # Deferred large-deletion drain (housekeeping)
    # ------------------------------------------------------------------

    def estimate_deletion_size(self, tenant_id: str, customer_ref: str) -> int:
        """Bound the deletion scope with two cheap counts (no per-child joins)."""
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM conversations WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            ).fetchone()
            count = int(row["n"]) if row else 0
            if count >= DEFERRED_DELETION_THRESHOLD:
                return count
            msgs = conn.execute(
                "SELECT COUNT(*) AS n FROM messages WHERE tenant_id = ? "
                "AND conversation_id IN (SELECT id FROM conversations "
                "WHERE tenant_id = ? AND customer_ref = ?)",
                (tenant_id, tenant_id, customer_ref),
            ).fetchone()
            return max(count, int(msgs["n"]) if msgs else 0)

    def drain_deferred_jobs(
        self, tenant_id: str | None = None, batch: int = DELETION_BATCH_SIZE
    ) -> int:
        """Run a bounded batch of deferred deletions; returns completions."""
        jobs = self._jobs.list_open(tenant_id)
        if not jobs:
            return 0
        done = 0
        for job in jobs[:batch]:
            try:
                counts = self.retention.execute_data_subject_deletion(
                    job["tenant_id"], job["customer_ref"]
                )
            except Exception as exc:
                self._jobs.mark_retryable(job, str(exc))
                continue
            self._jobs.mark_completed(job)
            self._finalize_deferred(job, counts)
            done += 1
        return done

    def _finalize_deferred(self, job: dict[str, Any], counts: dict[str, int]) -> None:
        tenant_id, request_id = job["tenant_id"], job["request_id"]
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT execution_secret FROM data_subject_requests WHERE id = ? AND tenant_id = ?",
                (request_id, tenant_id),
            ).fetchone()
            conn.execute(
                "UPDATE data_subject_requests SET status = 'completed', executor = 'housekeeping', "
                "executed_at = ?, execution_summary_json = ? WHERE id = ? AND tenant_id = ?",
                (
                    utc_now(),
                    json.dumps(counts, ensure_ascii=False, sort_keys=True),
                    request_id,
                    tenant_id,
                ),
            )
        self.record_deletion_proof(
            tenant_id,
            job["customer_ref"],
            request_id,
            "housekeeping",
            str(row["execution_secret"]) if row else "",
        )
        self._audit(
            tenant_id=tenant_id,
            actor="housekeeping",
            event_type="data_subject_request.executed",
            payload={"request_id": request_id, "request_type": "deletion", "summary": counts},
            reason="deferred data subject deletion executed",
        )


# Alias with the name the pipeline tooling (threat-model gate) already expects.
DataProtectionPipeline = DataProtectionService


def utc_now_as_datetime() -> Any:
    """Return the server clock as a timezone-aware datetime (test-variable clock)."""
    from datetime import datetime, timezone

    raw = utc_now()
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return datetime.now(timezone.utc)


__all__ = [
    "DEFERRED_DELETION_THRESHOLD",
    "DELETION_BATCH_SIZE",
    "DataProtectionService",
    "DataProtectionPipeline",
    "DeferredDeletionStore",
    "DsrExecutionError",
]
