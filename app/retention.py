"""Data retention, PII redaction, and data-subject-request utilities.

Provides GDPR-style data governance:
- Configurable retention policies per tenant and data type
- Data subject deletion (right to erasure)
- Data subject export (right to portability)
- Field-level PII redaction for exports
- Batch enforcement job to prune expired data
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, TYPE_CHECKING
from uuid import uuid4

from app.database import Database, utc_now
from app.dsr import DsrExportStore

if TYPE_CHECKING:  # pragma: no cover
    from app.attachment_store import DiskAttachmentStore

logger = logging.getLogger(__name__)

# Default retention periods (days). Tenants can override via set_retention_policy.
DEFAULT_RETENTION_DAYS: dict[str, int] = {
    "messages": 365,
    "audit_events": 2555,  # ~7 years for compliance
    "feedback": 365,
    "turn_jobs": 30,
    "conversations": 730,
}

# Fields considered PII – redacted in exports unless explicitly authorized.
PII_FIELDS: frozenset[str] = frozenset(
    {
        "customer_name",
        "customer_ref",
        "author",
        "actor",
        "claimed_by",
        "assigned_agent",
        "content",
        "preview",
    }
)

REDACTED = "[REDACTED]"
AUDIT_ARCHIVE_BATCH_SIZE = 500


def redact_pii(
    data: dict[str, Any] | list[dict[str, Any]],
    *,
    fields: frozenset[str] = PII_FIELDS,
) -> dict[str, Any] | list[dict[str, Any]]:
    """Return a copy with PII fields replaced by ``[REDACTED]``."""
    if isinstance(data, list):
        return [redact_pii(item, fields=fields) for item in data]  # type: ignore[return-value]
    if not isinstance(data, dict):
        return data
    result: dict[str, Any] = {}
    for key, value in data.items():
        if key in fields:
            result[key] = REDACTED
        elif isinstance(value, (dict, list)):
            result[key] = redact_pii(value, fields=fields)  # type: ignore[assignment]
        else:
            result[key] = value
    return result


class RetentionService:
    """Applies data retention policies and handles data-subject requests."""

    def __init__(
        self,
        database: Database,
        dsr_export_secret: str | None = None,
        archive_object_store: Any | None = None,
    ) -> None:
        self.database = database
        self.dsr_exports = DsrExportStore(database, secret=dsr_export_secret)
        # 42.3 REL-002: when configured, audit-retention payloads are written
        # as compressed partitions on the object store and the DB row keeps
        # only a slim manifest reference (migration v33 columns). ``None``
        # preserves the legacy inline-JSON behaviour.
        self.archive_object_store = archive_object_store
        # 42.4 SEC-006: when set, DSR deletion also removes attachment blobs
        # from the object store so rows and files stay consistent.
        self.attachment_store: DiskAttachmentStore | None = None

    def set_retention_policy(
        self,
        tenant_id: str,
        data_type: str,
        retention_days: int,
        actor: str,
    ) -> dict[str, Any]:
        if data_type not in DEFAULT_RETENTION_DAYS:
            raise ValueError(f"Unknown data type: {data_type}")
        if not 1 <= retention_days <= 3650:
            raise ValueError("retention_days must be between 1 and 3650")
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO retention_policies
                    (tenant_id, data_type, retention_days, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(tenant_id, data_type) DO UPDATE SET
                    retention_days = excluded.retention_days,
                    updated_at = excluded.updated_at
                """,
                (tenant_id, data_type, retention_days, utc_now()),
            )
        # Phase 41.3 SEC-005: retention policy is a high-risk family; the
        # audit event and its chain-tip anchor commit atomically or the caller
        # fail-closed (the mutation itself rolled back before this raise).
        from app.audit_gap import audit_high_risk

        audit_high_risk(
            self.database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=actor,
            event_type="retention.policy_updated",
            payload={"data_type": data_type, "retention_days": retention_days},
            reason="retention policy updated",
        )
        return {"tenant_id": tenant_id, "data_type": data_type, "retention_days": retention_days}

    def get_retention_policies(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT tenant_id, data_type, retention_days, updated_at "
                "FROM retention_policies WHERE tenant_id = ? ORDER BY data_type",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def effective_retention_days(self, tenant_id: str, data_type: str) -> int:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT retention_days FROM retention_policies "
                "WHERE tenant_id = ? AND data_type = ?",
                (tenant_id, data_type),
            ).fetchone()
        return row[0] if row else DEFAULT_RETENTION_DAYS[data_type]

    def enforce_retention(
        self, tenant_id: str, data_type: str, retention_days: int | None = None
    ) -> int:
        """Delete records older than the retention period. Returns count deleted."""
        days = retention_days or self.effective_retention_days(tenant_id, data_type)
        cutoff = _utc_days_ago(days)
        deleted = 0
        if data_type == "audit_events":
            # Archive and delete in bounded transactions. Each batch is
            # committed only after the manifest can be read back, so a
            # failed archive write never loses the source audit rows.
            while True:
                archived = self._archive_audit_batch(tenant_id, cutoff)
                if archived == 0:
                    break
                deleted += archived
            if deleted:
                logger.info(
                    "Retention enforcement: archived and deleted %d audit_events older "
                    "than %s for tenant %s",
                    deleted,
                    cutoff,
                    tenant_id,
                )
            return deleted

        with self.database.connect() as conn:
            if data_type == "turn_jobs":
                result = conn.execute(
                    "DELETE FROM turn_jobs WHERE tenant_id = ? AND completed_at IS NOT NULL "
                    "AND completed_at < ?",
                    (tenant_id, cutoff),
                )
                deleted = result.rowcount
            elif data_type == "feedback":
                result = conn.execute(
                    "DELETE FROM feedback WHERE tenant_id = ? AND created_at < ?",
                    (tenant_id, cutoff),
                )
                deleted = result.rowcount
            elif data_type == "messages":
                result = conn.execute(
                    "DELETE FROM messages WHERE tenant_id = ? AND created_at < ?",
                    (tenant_id, cutoff),
                )
                deleted = result.rowcount
            elif data_type == "conversations":
                result = conn.execute(
                    "DELETE FROM conversations WHERE tenant_id = ? AND status = 'resolved' "
                    "AND resolved_at IS NOT NULL AND resolved_at < ?",
                    (tenant_id, cutoff),
                )
                deleted = result.rowcount
            if deleted:
                logger.info(
                    "Retention enforcement: deleted %d %s records older than %s for tenant %s",
                    deleted,
                    data_type,
                    cutoff,
                    tenant_id,
                )
        return deleted

    def _archive_audit_batch(self, tenant_id: str, cutoff: str) -> int:
        """Persist one verifiable audit batch, then remove its hot rows."""
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT id, tenant_id, conversation_id, request_id, actor,
                          event_type, payload_json, created_at, seq, prev_hash,
                          event_hash
                   FROM audit_events
                   WHERE tenant_id = ? AND created_at < ?
                   ORDER BY seq ASC
                   LIMIT ?""",
                (tenant_id, cutoff, AUDIT_ARCHIVE_BATCH_SIZE),
            ).fetchall()
            if not rows:
                return 0

            events = [dict(row) for row in rows]
            created_at = utc_now()
            archive_id = f"audarc_{uuid4().hex}"

            store = self.archive_object_store
            if store is not None:
                # 42.3 REL-002: payload lives as a compressed JSONL partition
                # on the object store; the DB row keeps only the manifest.
                header = {
                    "record": "header",
                    "schema": 1,
                    "tenant_id": tenant_id,
                    "cutoff": cutoff,
                    "created_at": created_at,
                }
                entry = store.put_partition(
                    tenant_id,
                    partition_key=created_at[:10],
                    records=[header, *events],
                    time_key="created_at",
                    created_at=created_at,
                )
                connection.execute(
                    """INSERT INTO audit_archives
                       (id, tenant_id, cutoff, event_count, first_seq, last_seq,
                        first_event_hash, last_event_hash, archive_json,
                        content_sha256, created_at, object_key, object_sha256, object_bytes)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)""",
                    (
                        archive_id,
                        tenant_id,
                        cutoff,
                        len(events),
                        int(events[0]["seq"]),
                        int(events[-1]["seq"]),
                        str(events[0].get("event_hash") or ""),
                        str(events[-1].get("event_hash") or ""),
                        entry.content_sha256,
                        created_at,
                        entry.object_id,
                        entry.content_sha256,
                        entry.size_bytes,
                    ),
                )
                event_ids = [str(event["id"]) for event in events]
                self._verify_object_archive(
                    connection, tenant_id, archive_id, entry.object_id, event_ids
                )
            else:
                archive_document = {
                    "schema": 1,
                    "tenant_id": tenant_id,
                    "cutoff": cutoff,
                    "created_at": created_at,
                    "events": events,
                }
                archive_json = json.dumps(
                    archive_document,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                )
                digest = hashlib.sha256(archive_json.encode("utf-8")).hexdigest()
                connection.execute(
                    """INSERT INTO audit_archives
                       (id, tenant_id, cutoff, event_count, first_seq, last_seq,
                        first_event_hash, last_event_hash, archive_json,
                        content_sha256, created_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        archive_id,
                        tenant_id,
                        cutoff,
                        len(events),
                        int(events[0]["seq"]),
                        int(events[-1]["seq"]),
                        str(events[0].get("event_hash") or ""),
                        str(events[-1].get("event_hash") or ""),
                        archive_json,
                        digest,
                        created_at,
                    ),
                )
                event_ids = [str(event["id"]) for event in events]
                persisted = connection.execute(
                    """SELECT id, tenant_id, cutoff, event_count, first_seq, last_seq,
                              first_event_hash, last_event_hash, archive_json,
                              content_sha256, created_at
                       FROM audit_archives WHERE id = ?""",
                    (archive_id,),
                ).fetchone()
                if not persisted:
                    raise RuntimeError("audit archive persistence verification failed")
                from app.audit_chain import validate_audit_archive

                persisted_events = validate_audit_archive(dict(persisted))
                if [str(event["id"]) for event in persisted_events] != event_ids:
                    raise RuntimeError("audit archive source event verification failed")

            placeholders = ",".join("?" for _ in event_ids)
            result = connection.execute(
                f"DELETE FROM audit_events WHERE tenant_id = ? AND id IN ({placeholders})",
                [tenant_id, *event_ids],
            )
            if result.rowcount != len(events):
                raise RuntimeError("audit archive deletion count mismatch")
            return result.rowcount

    def _verify_object_archive(
        self,
        connection: Any,
        tenant_id: str,
        archive_id: str,
        object_id: str,
        event_ids: list[str],
    ) -> None:
        """Stream-verify a just-written object partition against its row.

        Reads back through the store (digest-checked) and validates the chain
        incrementally — bounded memory even at the batch cap. Uses the
        caller's open transaction so the just-inserted manifest row is
        visible.
        """
        from app.audit_chain import validate_audit_archive_stream

        store = self.archive_object_store
        if store is None:
            raise RuntimeError("object archive verification requires a configured store")
        row = connection.execute(
            """SELECT id, tenant_id, cutoff, event_count, first_seq, last_seq,
                      first_event_hash, last_event_hash, content_sha256, created_at,
                      object_key, object_sha256
               FROM audit_archives WHERE id = ?""",
            (archive_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("audit archive persistence verification failed")
        meta = dict(row)
        if meta.get("object_sha256") and meta["object_sha256"] != meta.get("content_sha256"):
            raise RuntimeError("audit archive object digest bookkeeping mismatch")
        streamed = [
            str(event["id"])
            for event in validate_audit_archive_stream(meta, store.iter_lines(tenant_id, object_id))
        ]
        if streamed != event_ids:
            raise RuntimeError("audit archive source event verification failed")

    def enforce_all(self, tenant_id: str) -> dict[str, int]:
        """Enforce retention for all data types. Returns per-type deletion counts."""
        results: dict[str, int] = {}
        for data_type in DEFAULT_RETENTION_DAYS:
            results[data_type] = self.enforce_retention(tenant_id, data_type)
        return results

    # ------------------------------------------------------------------
    # M0 SEC-002: data-subject request workflow (maker-checker)
    #
    # ``pending -> approved -> completed``.  Approval requires a privacy
    # permission holder; deletion execution uses maker-checker separation
    # (approver and executor must differ from the requester and from each
    # other).  Execution only accepts an approved request id — never an
    # arbitrary customer reference — and replaying a completed request
    # returns the stored execution summary.  Audit events carry the request
    # id and per-type counts, never the customer reference or content.
    # ------------------------------------------------------------------

    def create_data_subject_request(
        self,
        tenant_id: str,
        customer_ref: str,
        request_type: str,
        requested_by: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if request_type not in {"deletion", "export"}:
            raise ValueError("request_type must be 'deletion' or 'export'")
        if not customer_ref.strip():
            raise ValueError("customer_ref cannot be blank")
        if idempotency_key:
            with self.database.connect() as conn:
                row = conn.execute(
                    "SELECT id, tenant_id, customer_ref, request_type, status, "
                    "requested_by, created_at, completed_at "
                    "FROM data_subject_requests "
                    "WHERE tenant_id = ? AND idempotency_key = ?",
                    (tenant_id, idempotency_key),
                ).fetchone()
            if row is not None:
                return dict(row)
        request_id = uuid4().hex
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO data_subject_requests "
                "(id, tenant_id, customer_ref, request_type, status, requested_by, "
                "idempotency_key, created_at) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
                (
                    request_id,
                    tenant_id,
                    customer_ref,
                    request_type,
                    requested_by,
                    idempotency_key,
                    utc_now(),
                ),
            )
        # Phase 41.3 SEC-005: DSR lifecycle is a high-risk family; anchor
        # atomically or fail closed.
        from app.audit_gap import audit_high_risk

        audit_high_risk(
            self.database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=requested_by,
            event_type="data_subject_request.created",
            payload={"request_id": request_id, "request_type": request_type},
            reason="data subject request created",
        )
        return {
            "id": request_id,
            "tenant_id": tenant_id,
            "customer_ref": customer_ref,
            "request_type": request_type,
            "status": "pending",
        }

    def approve_data_subject_request(
        self,
        tenant_id: str,
        request_id: str,
        approver: str,
    ) -> dict[str, Any]:
        """Approve a pending request (maker-checker when deletion)."""
        row = self._get_dsr(tenant_id, request_id)
        if row["status"] != "pending":
            raise ValueError(f"Request is {row['status']}, only pending requests can be approved")
        if row["request_type"] == "deletion" and row["requested_by"] == approver:
            raise ValueError("Deletion requests cannot be approved by their requester")
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET status = 'approved', approver = ?, "
                "approved_at = ? WHERE id = ? AND tenant_id = ?",
                (approver, utc_now(), request_id, tenant_id),
            )
        from app.audit_gap import audit_high_risk

        audit_high_risk(
            self.database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=approver,
            event_type="data_subject_request.approved",
            payload={"request_id": request_id, "request_type": row["request_type"]},
            reason="data subject request approved",
        )
        return {
            "id": request_id,
            "tenant_id": tenant_id,
            "request_type": row["request_type"],
            "status": "approved",
        }

    def execute_data_subject_request(
        self,
        tenant_id: str,
        request_id: str,
        executor: str,
    ) -> dict[str, Any]:
        """Execute an approved request by id; idempotent on completion.

        Returns a summary without raw PII.  For exports the one-time
        download token is returned once and never persisted raw.
        """
        row = self._get_dsr(tenant_id, request_id)
        if row["status"] == "completed":
            summary = json.loads(row.get("execution_summary_json") or "{}")
            return {
                "request_id": request_id,
                "request_type": row["request_type"],
                "status": "completed",
                "idempotent_replay": True,
                "summary": summary,
            }
        if row["status"] != "approved":
            raise ValueError(f"Request is {row['status']}, only approved requests can be executed")
        request_type = row["request_type"]
        if request_type == "deletion":
            if row["requested_by"] == executor:
                raise ValueError("The requester cannot execute their own deletion")
            if row.get("approver") == executor:
                raise ValueError("The approver cannot execute the deletion they approved")
        counts = (
            self.execute_data_subject_deletion(tenant_id, row["customer_ref"])
            if request_type == "deletion"
            else None
        )
        export_meta: dict[str, Any] = {}
        summary: dict[str, Any] = {}
        if request_type == "export":
            raw = self.execute_data_subject_export(tenant_id, row["customer_ref"])
            export_blob = json.dumps(
                raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            import time as _time

            stored = self.dsr_exports.store(
                tenant_id,
                request_id,
                export_blob,
                now_epoch=int(_time.time()),
            )
            export_meta = {
                "object_id": stored["object_id"],
                "download_url": f"/api/data-subject-requests/{request_id}/export",
                "download_token": stored["raw_token"],
                "token_expires_at": stored["token_expires_at"],
                "object_expires_at": stored["expires_at"],
                "content_sha256": stored["content_sha256"],
                "byte_count": stored["byte_count"],
            }
            summary["exported"] = {
                "conversations": len(raw.get("conversations", [])),
                "messages": len(raw.get("messages", [])),
                "feedback": len(raw.get("feedback", [])),
            }
        if counts is not None:
            summary["deleted"] = counts
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET status = 'completed', executor = ?, "
                "executed_at = ?, execution_summary_json = ?, export_object_id = ? "
                "WHERE id = ? AND tenant_id = ?",
                (
                    executor,
                    utc_now(),
                    json.dumps(summary, ensure_ascii=False, sort_keys=True),
                    export_meta.get("object_id"),
                    request_id,
                    tenant_id,
                ),
            )
        from app.audit_gap import audit_high_risk

        audit_high_risk(
            self.database,
            tenant_id=tenant_id,
            conversation_id=None,
            actor=executor,
            event_type="data_subject_request.executed",
            payload={"request_id": request_id, "request_type": request_type, "summary": summary},
            reason="data subject request executed",
        )
        return {
            "request_id": request_id,
            "request_type": request_type,
            "status": "completed",
            "idempotent_replay": False,
            "summary": summary,
            **export_meta,
        }

    def _get_dsr(self, tenant_id: str, request_id: str) -> dict[str, Any]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT id, tenant_id, customer_ref, request_type, status, "
                "requested_by, approver, approved_at, executor, executed_at, "
                "execution_summary_json, export_object_id, created_at, completed_at "
                "FROM data_subject_requests WHERE id = ? AND tenant_id = ?",
                (request_id, tenant_id),
            ).fetchone()
        if row is None:
            raise LookupError("Data subject request not found")
        return dict(row)

    def list_data_subject_requests(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT id, tenant_id, customer_ref, request_type, status, "
                "requested_by, approver, approved_at, executor, executed_at, "
                "created_at, completed_at "
                "FROM data_subject_requests WHERE tenant_id = ? "
                "ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def download_data_subject_export(
        self,
        tenant_id: str,
        request_id: str,
        raw_token: str,
    ) -> dict[str, Any] | None:
        """Consume the one-time download token and return the decrypted export."""
        row = self._get_dsr(tenant_id, request_id)
        object_id = row.get("export_object_id")
        if row["request_type"] != "export" or not object_id:
            raise ValueError("This request has no export object")
        import time as _time

        payload = self.dsr_exports.consume(
            tenant_id, object_id, raw_token, now_epoch=int(_time.time())
        )
        if payload is None:
            return None
        try:
            return json.loads(payload["plaintext"].decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Export object is malformed") from exc

    def prune_dsr_exports(self) -> int:
        """Delete encrypted export objects past their 24-hour expiry."""
        import time as _time

        return self.dsr_exports.prune_expired(now_epoch=int(_time.time()))

    def execute_data_subject_deletion(self, tenant_id: str, customer_ref: str) -> dict[str, int]:
        """Delete customer data while preserving independently retained audit evidence."""
        counts: dict[str, int] = {}
        with self.database.connect() as conn:
            hot_conversation_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM conversations WHERE tenant_id = ? AND customer_ref = ?",
                    (tenant_id, customer_ref),
                ).fetchall()
            ]
            archived_conversation_ids = [
                row[0]
                for row in conn.execute(
                    "SELECT id FROM conversations_archive WHERE tenant_id = ? AND customer_ref = ?",
                    (tenant_id, customer_ref),
                ).fetchall()
            ]
            conversation_ids = list(dict.fromkeys(hot_conversation_ids + archived_conversation_ids))
            if not conversation_ids:
                result = conn.execute(
                    "DELETE FROM orders WHERE tenant_id = ? AND customer_ref = ?",
                    (tenant_id, customer_ref),
                )
                counts["orders"] = result.rowcount
                return counts
            placeholders = ",".join("?" * len(conversation_ids))
            params = [tenant_id] + conversation_ids

            # 42.4 SEC-006: unlink attachment blobs before their rows vanish,
            # so the object store never outlives the database. The removal
            # count is part of the deletion proof.
            if self.attachment_store is not None:
                keys = [
                    str(row[0])
                    for row in conn.execute(
                        f"""SELECT storage_key FROM attachments
                        WHERE tenant_id = ? AND conversation_id IN ({placeholders})
                          AND storage_key IS NOT NULL""",
                        params,
                    ).fetchall()
                ]
                removed = 0
                for key in keys:
                    try:
                        if self.attachment_store.delete(key):
                            removed += 1
                    except Exception:
                        logger.exception("dsr.attachment_unlink_failed")
                counts["attachment_objects"] = removed

            # Children first: anything keyed by a conversation id must go
            # before the conversations themselves (FK-safe in SQLite and PG).
            for table, count_key in (
                ("feedback", "feedback"),
                ("messages", "messages"),
                ("turn_jobs", "turn_jobs"),
                ("turn_job_chunks", "turn_job_chunks"),
                ("turn_requests", "turn_requests"),
                ("conversation_labels", "conversation_labels"),
                ("conversation_summaries", "conversation_summaries"),
                ("conversation_mentions", "conversation_mentions"),
                ("csat_surveys", "csat_surveys"),
                ("attachments", "attachments"),
                ("ticket_conversations", "ticket_conversations"),
            ):
                result = conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders})",
                    params,
                )
                counts[count_key] = result.rowcount

            # Audit evidence is governed by its own retention policy and hash
            # chain. Removing or rewriting selected rows here would invalidate
            # every later link, so a data-subject request deletes operational
            # customer data while audit rows remain until audit retention runs.
            counts["audit_events"] = 0
            counts["audit_archives"] = 0

            result = conn.execute(
                "DELETE FROM conversations WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            )
            counts["conversations"] = result.rowcount

            for table, count_key in (
                ("feedback_archive", "feedback_archive"),
                ("messages_archive", "messages_archive"),
                ("conversation_labels_archive", "conversation_labels_archive"),
            ):
                result = conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders})",
                    params,
                )
                counts[count_key] = result.rowcount
            result = conn.execute(
                "DELETE FROM conversations_archive WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            )
            counts["conversations_archive"] = result.rowcount

            result = conn.execute(
                "DELETE FROM orders WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            )
            counts["orders"] = result.rowcount

        logger.info(
            "Data subject deletion for tenant=%s customer_ref=%s: %s",
            tenant_id,
            customer_ref,
            counts,
        )
        return counts

    def execute_data_subject_export(self, tenant_id: str, customer_ref: str) -> dict[str, Any]:
        """Export all data associated with a customer reference (right to portability)."""
        export: dict[str, Any] = {"customer_ref": customer_ref, "tenant_id": tenant_id}
        with self.database.connect() as conn:
            hot_conversations = conn.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            ).fetchall()
            archived_conversations = conn.execute(
                "SELECT * FROM conversations_archive WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            ).fetchall()
            orders = conn.execute(
                "SELECT * FROM orders WHERE tenant_id = ? AND customer_ref = ?",
                (tenant_id, customer_ref),
            ).fetchall()
            export["orders"] = [dict(row) for row in orders]
            export["conversations"] = [
                *[dict(row) for row in hot_conversations],
                *[dict(row) for row in archived_conversations],
            ]
            conv_ids = [row["id"] for row in hot_conversations] + [
                row["id"] for row in archived_conversations
            ]
            if conv_ids:
                placeholders = ",".join("?" * len(conv_ids))
                messages = conn.execute(
                    f"SELECT * FROM messages WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders}) ORDER BY created_at",
                    [tenant_id] + conv_ids,
                ).fetchall()
                archived_messages = conn.execute(
                    f"SELECT * FROM messages_archive WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders}) ORDER BY created_at",
                    [tenant_id] + conv_ids,
                ).fetchall()
                export["messages"] = [
                    *[dict(row) for row in messages],
                    *[dict(row) for row in archived_messages],
                ]
                feedback = conn.execute(
                    f"SELECT * FROM feedback WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders})",
                    [tenant_id] + conv_ids,
                ).fetchall()
                archived_feedback = conn.execute(
                    f"SELECT * FROM feedback_archive WHERE tenant_id = ? "
                    f"AND conversation_id IN ({placeholders})",
                    [tenant_id] + conv_ids,
                ).fetchall()
                export["feedback"] = [
                    *[dict(row) for row in feedback],
                    *[dict(row) for row in archived_feedback],
                ]
            else:
                export["messages"] = []
                export["feedback"] = []
        return export


def _utc_days_ago(days: int) -> str:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="milliseconds")
