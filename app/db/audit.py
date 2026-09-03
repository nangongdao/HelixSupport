"""Database audit mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import hashlib
import json
import sqlite3
from contextlib import contextmanager, nullcontext
from threading import Lock
from typing import Any, Iterator, Sequence
from uuid import uuid4

from app.context import current_request_id, current_scope_mode, tenant_scope
from app.db._util import utc_now
from app.security import sanitize_for_audit

# Phase 28.3/29: serialize chain appends so two concurrent audit() calls can
# never link to the same prev_hash (which would fork the chain and make it
# permanently "tampered" under the verifier).
_AUDIT_CHAIN_LOCK = Lock()

# Phase 41.3 / SEC-005: high-risk families.  A mutation in one of these must
# record its audit tip atomically (see audit_high_risk) or fail closed; an
# ordinary telemetry append that fails leaves an observable audit_gap instead.
HIGH_RISK_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "api_key.issued",
        "api_key.revoked",
        "data_subject_request.created",
        "data_subject_request.approved",
        "data_subject_request.executed",
        "member.invited",
        "member.role_updated",
        "member.deactivated",
        "retention.policy_updated",
        "sla_policy.set",
        "webhook.registered",
        "webhook.deleted",
    }
)


class DatabaseAuditMixin:
    def _acquire_audit_chain_write_lock(self, connection: Any) -> None:
        """Serialise chain-tail reads across processes (Phase 28.3/29).

        The module-level ``_AUDIT_CHAIN_LOCK`` above only serialises threads in
        the current process; once more than one process writes the audit chain
        (``uvicorn --workers N``, or multiple instances sharing one database)
        two workers can read the same tail and fork the chain, which then
        verifies as permanently "tampered". ``BEGIN IMMEDIATE`` takes SQLite's
        single-writer lock before the tail read, so read+insert are one
        serialised transaction. ``PostgresDatabase`` overrides this with an
        advisory transaction lock; anything else is left untouched.
        """
        if isinstance(connection, sqlite3.Connection) and not connection.in_transaction:
            connection.execute("BEGIN IMMEDIATE")

    @staticmethod
    def _audit_chain_tail(connection: Any) -> tuple[str, int]:
        """Return the newest hash and sequence across hot and archived rows."""
        try:
            row = connection.execute(
                """SELECT event_hash, seq FROM (
                       SELECT event_hash, seq FROM audit_events
                       UNION ALL
                       SELECT last_event_hash AS event_hash, last_seq AS seq
                       FROM audit_archives
                   ) AS audit_chain
                   ORDER BY seq DESC LIMIT 1"""
            ).fetchone()
        except sqlite3.OperationalError:
            # Databases being upgraded from a pre-retention schema do not have
            # ``audit_archives`` until migration 26 completes.
            row = connection.execute(
                "SELECT event_hash, seq FROM audit_events ORDER BY seq DESC, rowid DESC LIMIT 1"
            ).fetchone()
        if not row:
            return "", 0
        return str(row["event_hash"] or ""), int(row["seq"] or 0)

    def audit_chain_head(self) -> str | None:
        """Return the current cold/hot audit-chain head."""
        with self.connect() as connection:
            event_hash_value, _ = self._audit_chain_tail(connection)
        return event_hash_value or None

    @contextmanager
    def audit_transaction(self) -> Iterator[Any]:
        """Yield a transaction holding the process and database chain locks."""
        with _AUDIT_CHAIN_LOCK:
            with self.connect() as connection:
                self._acquire_audit_chain_write_lock(connection)
                yield connection

    def _append_audit_event(
        self,
        connection: Any,
        tenant_id: str,
        conversation_id: str | None,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: str | None = None,
    ) -> str:
        """Append one hashed event using an existing locked transaction."""
        from app.audit_chain import event_hash

        now = created_at or utc_now()
        event_id = f"evt_{uuid4().hex[:12]}"
        payload_json = json.dumps(sanitize_for_audit(payload), ensure_ascii=False)
        request_id = current_request_id()
        prev_hash, tail_seq = self._audit_chain_tail(connection)
        seq = tail_seq + 1
        event_hash_value = event_hash(
            prev_hash=prev_hash,
            event_id=event_id,
            tenant_id=tenant_id,
            conversation_id=conversation_id,
            request_id=request_id,
            actor=str(actor),
            event_type=event_type,
            payload_json=payload_json,
            created_at=now,
        )
        connection.execute(
            """INSERT INTO audit_events
            (id, tenant_id, conversation_id, request_id, actor, event_type,
             payload_json, created_at, seq, prev_hash, event_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event_id,
                tenant_id,
                conversation_id,
                request_id,
                str(actor),
                event_type,
                payload_json,
                now,
                seq,
                prev_hash,
                event_hash_value,
            ),
        )
        # SQLite's legacy AFTER INSERT trigger fills ``seq`` from rowid even
        # when a value was supplied. Rewrite it to the archive-aware sequence;
        # PostgreSQL preserves the supplied value and this is a harmless no-op.
        connection.execute("UPDATE audit_events SET seq = ? WHERE id = ?", (seq, event_id))
        return event_id

    def audit_in_transaction(
        self,
        connection: Any,
        tenant_id: str,
        conversation_id: str | None,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        created_at: str | None = None,
    ) -> str:
        """Append an event inside :meth:`audit_transaction`."""
        return self._append_audit_event(
            connection,
            tenant_id,
            conversation_id,
            actor,
            event_type,
            payload,
            created_at=created_at,
        )

    def record_feedback(
        self,
        tenant_id: str,
        conversation_id: str,
        message_id: str,
        actor: str,
        rating: int,
        reason: str | None,
    ) -> dict[str, Any]:
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                """SELECT id FROM feedback
                WHERE tenant_id = ? AND message_id = ? AND actor = ?""",
                (tenant_id, message_id, actor),
            ).fetchone()
            feedback_id = existing["id"] if existing else f"fb_{uuid4().hex[:12]}"
            connection.execute(
                """INSERT INTO feedback
                (id, tenant_id, conversation_id, message_id, actor, rating, reason,
                 created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, message_id, actor) DO UPDATE SET
                    rating = excluded.rating,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at""",
                (
                    feedback_id,
                    tenant_id,
                    conversation_id,
                    message_id,
                    actor,
                    rating,
                    reason,
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM feedback WHERE id = ?", (feedback_id,)
            ).fetchone()
        self._invalidate_dashboard(tenant_id)
        return dict(row)

    def _audit_scope(self, tenant_id: str) -> Any:
        """Bind the event's own tenant as the RLS scope when none is active.

        Phase 43.2 contract (a): audit writes happen on paths that legitimately
        run outside a request scope — worker scheduling outcomes, startup
        sweeps, pre-authentication login events. Each call carries its tenant
        explicitly, so the write binds exactly that row's tenant for its own
        transaction instead of failing WITH CHECK under enforced RLS. An
        existing *tenant* scope is left untouched: a mismatch then fails the
        policy check, which is the correct loud outcome.
        """
        if current_scope_mode() == "tenant":
            return nullcontext()
        return tenant_scope(tenant_id)

    def audit(
        self,
        tenant_id: str,
        conversation_id: str | None,
        actor: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        # Phase 28.3/29: serialize chain appends so two concurrent calls can
        # never both link to the same prev_hash (chain fork).
        with self._audit_scope(tenant_id):
            with self.audit_transaction() as connection:
                self._append_audit_event(
                    connection,
                    tenant_id,
                    conversation_id,
                    actor,
                    event_type,
                    payload,
                )

    def audit_many(
        self,
        tenant_id: str,
        actor: str,
        events: Sequence[tuple[str, str, dict[str, Any]]],
    ) -> None:
        if not events:
            return
        from app.audit_chain import event_hash

        request_id = current_request_id()
        now = utc_now()
        with self._audit_scope(tenant_id):
            with self.audit_transaction() as connection:
                prev_hash, tail_seq = self._audit_chain_tail(connection)
                rows: list[tuple[str, str, str, str | None, str, str, str, str, int, str, str]] = []
                sequences: list[tuple[int, str]] = []
                for conversation_id, event_type, payload in events:
                    event_id = f"evt_{uuid4().hex[:12]}"
                    payload_json = json.dumps(sanitize_for_audit(payload), ensure_ascii=False)
                    tail_seq += 1
                    event_hash_value = event_hash(
                        prev_hash=prev_hash,
                        event_id=event_id,
                        tenant_id=tenant_id,
                        conversation_id=conversation_id,
                        request_id=request_id,
                        actor=str(actor),
                        event_type=event_type,
                        payload_json=payload_json,
                        created_at=now,
                    )
                    rows.append(
                        (
                            event_id,
                            tenant_id,
                            conversation_id,
                            request_id,
                            str(actor),
                            event_type,
                            payload_json,
                            now,
                            tail_seq,
                            prev_hash,
                            event_hash_value,
                        )
                    )
                    sequences.append((tail_seq, event_id))
                    prev_hash = event_hash_value
                connection.executemany(
                    """INSERT INTO audit_events
                    (id, tenant_id, conversation_id, request_id, actor, event_type,
                     payload_json, created_at, seq, prev_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    rows,
                )
                connection.executemany(
                    "UPDATE audit_events SET seq = ? WHERE id = ?",
                    sequences,
                )

    def list_audit(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT * FROM audit_events
                WHERE tenant_id = ? AND conversation_id = ?
                ORDER BY created_at, rowid""",
                (tenant_id, conversation_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item.pop("tenant_id", None)
            item.pop("conversation_id", None)
            item.pop("seq", None)  # internal monotonic ordering column
            item.pop("prev_hash", None)  # internal hash-chain link (28.3)
            item.pop("event_hash", None)  # internal hash-chain digest (28.3)
            result.append(item)
        return result

    def export_audit_events(
        self,
        tenant_id: str,
        *,
        conversation_id: str | None = None,
        event_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        limit: int = 500,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if conversation_id:
            clauses.append("conversation_id = ?")
            values.append(conversation_id)
        if event_type:
            clauses.append("event_type = ?")
            values.append(event_type)
        if since:
            clauses.append("created_at >= ?")
            values.append(since)
        if until:
            clauses.append("created_at <= ?")
            values.append(until)
        where = " AND ".join(clauses)
        values.extend([max(1, min(limit, 2000)), max(0, offset)])
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM audit_events WHERE {where}
                ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?""",
                values,
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["payload"] = json.loads(item.pop("payload_json"))
            item.pop("seq", None)  # internal monotonic ordering column
            item.pop("prev_hash", None)  # internal hash-chain link (28.3)
            item.pop("event_hash", None)  # internal hash-chain digest (28.3)
            result.append(item)
        return result

    def list_audit_archives(
        self, tenant_id: str, *, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        """List durable audit-retention manifests without exposing payloads."""
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, tenant_id, cutoff, event_count, first_seq, last_seq,
                          first_event_hash, last_event_hash, content_sha256, created_at
                   FROM audit_archives
                   WHERE tenant_id = ?
                   ORDER BY created_at DESC, id DESC
                   LIMIT ? OFFSET ?""",
                (tenant_id, max(1, min(limit, 1000)), max(0, offset)),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item.pop("tenant_id", None)
            result.append(item)
        return result

    def get_audit_archive(self, tenant_id: str, archive_id: str) -> dict[str, Any] | None:
        """Return one tenant-scoped archive manifest and its original rows.

        Two payload shapes (42.3): legacy rows carry inline ``archive_json``
        validated whole; object-store rows stream their compressed partition
        through the incremental chain validator — the event list is built
        once, but no raw JSON blob is ever materialised alongside it.
        """
        with self.connect() as connection:
            row = connection.execute(
                """SELECT id, tenant_id, cutoff, event_count, first_seq, last_seq,
                          first_event_hash, last_event_hash, archive_json,
                          content_sha256, created_at, object_key, object_sha256
                   FROM audit_archives
                   WHERE tenant_id = ? AND id = ?""",
                (tenant_id, archive_id),
            ).fetchone()
        if not row:
            return None
        from app.audit_chain import validate_audit_archive, validate_audit_archive_stream

        item = dict(row)
        object_key = item.pop("object_key", None)
        item.pop("object_sha256", None)
        if object_key:
            store = getattr(self, "archive_object_store", None)
            if store is None:
                raise RuntimeError(
                    "audit archive payload is on the object store but no "
                    "ArchiveObjectStore is configured for this process"
                )

            digest_cell: dict[str, str] = {}

            def _digesting(lines: Any) -> Any:
                """Recompute the canonical-stream digest while streaming."""
                digest = hashlib.sha256()
                for line in lines:
                    raw = line if isinstance(line, bytes) else line.encode("utf-8")
                    digest.update(raw)
                    yield line
                digest_cell["sha256"] = digest.hexdigest()

            expected_digest = str(item.get("content_sha256") or "")
            item["events"] = list(
                validate_audit_archive_stream(
                    item, _digesting(store.iter_lines(tenant_id, object_key))
                )
            )
            if digest_cell.get("sha256") != expected_digest:
                raise ValueError("audit archive content hash mismatch")
        else:
            item["events"] = validate_audit_archive(item)
        item.pop("archive_json")
        item.pop("tenant_id", None)
        return item

    @staticmethod
    def _assistant_metrics(
        connection: sqlite3.Connection, tenant_id: str
    ) -> tuple[int, int, float, int]:
        try:
            row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE
                              WHEN json_valid(metadata_json)
                               AND COALESCE(json_extract(metadata_json, '$.agent'), '')
                                   != 'escalation'
                              THEN 1 ELSE 0 END) AS automated,
                          AVG(CASE
                              WHEN json_valid(metadata_json)
                               AND typeof(json_extract(metadata_json, '$.confidence'))
                                   IN ('integer', 'real')
                              THEN CAST(json_extract(metadata_json, '$.confidence') AS REAL)
                              ELSE NULL END) AS average_confidence,
                          SUM(CASE
                              WHEN json_valid(metadata_json)
                               AND json_array_length(
                                   json_extract(metadata_json, '$.citations')
                               ) > 0
                              THEN 1 ELSE 0 END) AS grounded
                FROM messages WHERE tenant_id = ? AND role = 'assistant'""",
                (tenant_id,),
            ).fetchone()
            return (
                int(row["total"] or 0),
                int(row["automated"] or 0),
                float(row["average_confidence"] or 0.0),
                int(row["grounded"] or 0),
            )
        except sqlite3.OperationalError:
            rows = connection.execute(
                """SELECT metadata_json FROM messages
                WHERE tenant_id = ? AND role = 'assistant'""",
                (tenant_id,),
            ).fetchall()
            confidences: list[float] = []
            grounded = 0
            automated = 0
            for item in rows:
                metadata = json.loads(item["metadata_json"])
                confidence = metadata.get("confidence")
                if isinstance(confidence, (int, float)):
                    confidences.append(float(confidence))
                if metadata.get("citations"):
                    grounded += 1
                if metadata.get("agent") != "escalation":
                    automated += 1
            average = sum(confidences) / len(confidences) if confidences else 0.0
            return len(rows), automated, average, grounded

    def dashboard(self, tenant_id: str) -> dict[str, Any]:
        cached = self._dashboard_cache.get(tenant_id)
        if cached is not None:
            return dict(cached)
        now = utc_now()
        with self.connect() as connection:
            conversation_row = connection.execute(
                """SELECT
                    SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) AS open_count,
                    SUM(CASE WHEN status = 'waiting_human' THEN 1 ELSE 0 END) AS waiting_human,
                    SUM(CASE WHEN status = 'human_active' THEN 1 ELSE 0 END) AS human_active,
                    SUM(CASE WHEN status = 'resolved' THEN 1 ELSE 0 END) AS resolved,
                    COUNT(*) AS total,
                    SUM(CASE WHEN status != 'resolved' AND sla_due_at IS NOT NULL
                              AND sla_due_at < ? THEN 1 ELSE 0 END) AS sla_breached,
                    SUM(CASE WHEN status != 'resolved' AND claimed_by IS NOT NULL
                              AND claim_expires_at IS NOT NULL AND claim_expires_at > ?
                         THEN 1 ELSE 0 END) AS claimed_active,
                    SUM(CASE WHEN status != 'resolved' AND priority = 'high'
                         THEN 1 ELSE 0 END) AS high_priority,
                    SUM(CASE WHEN status != 'resolved' AND needs_response = 1
                         THEN 1 ELSE 0 END) AS needs_response,
                    AVG(CASE WHEN status != 'resolved' AND first_response_at IS NOT NULL
                         THEN (julianday(first_response_at) - julianday(created_at)) * 86400.0
                         ELSE NULL END) AS first_response_seconds
                FROM conversations WHERE tenant_id = ?""",
                (now, now, tenant_id),
            ).fetchone()
            total_assistant, automated, average_confidence, grounded = self._assistant_metrics(
                connection, tenant_id
            )
            feedback_row = connection.execute(
                """SELECT COUNT(*) AS total,
                          SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) AS positive
                FROM feedback WHERE tenant_id = ?""",
                (tenant_id,),
            ).fetchone()
        feedback_total = int(feedback_row["total"] or 0)
        result = {
            "open": int(conversation_row["open_count"] or 0),
            "waiting_human": int(conversation_row["waiting_human"] or 0),
            "human_active": int(conversation_row["human_active"] or 0),
            "resolved": int(conversation_row["resolved"] or 0),
            "total": int(conversation_row["total"] or 0),
            "sla_breached": int(conversation_row["sla_breached"] or 0),
            "claimed_active": int(conversation_row["claimed_active"] or 0),
            "high_priority": int(conversation_row["high_priority"] or 0),
            "needs_response": int(conversation_row["needs_response"] or 0),
            "average_first_response_seconds": round(
                float(conversation_row["first_response_seconds"] or 0.0), 1
            ),
            "automated_responses": automated,
            "average_confidence": round(average_confidence, 3),
            "grounded_rate": round(grounded / total_assistant, 3) if total_assistant else 0.0,
            "positive_feedback_rate": round(int(feedback_row["positive"] or 0) / feedback_total, 3)
            if feedback_total
            else 0.0,
        }
        self._dashboard_cache.set(tenant_id, result)
        return dict(result)
