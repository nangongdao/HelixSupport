"""Audit-gap tracking and same-transaction anchoring (Phase 41.3, SEC-005).

Securing the audit trail only matters if the trail is actually written.  This
module couples two guarantees:

- **Same-transaction high-risk anchoring.**  A high-risk mutation
  (security / permission / credential / DSR change) appends its audit event
  and records the resulting chain tip ``(last_seq, last_hash)`` in one
  ``BEGIN IMMEDIATE`` transaction.  If the audit append itself fails the whole
  mutation aborts (fail-closed): the caller sees 503 instead of a mutation
  that ran with no record.  Only the chain tip is persisted here — the anchor
  row is one small frontier block per high-risk event, deliberately cheap.

- **Observable ``audit_gap``.**  Ordinary conversation telemetry follows the
  existing best-effort path; when an append fails there, the miss must surface
  as a durable, queryable gap plus an in-process alert counter, so an
  unrecoverable audit path becomes an alert rather than silent data loss.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from contextlib import nullcontext
from typing import Any

from app.context import current_scope_mode, tenant_scope
from app.db._util import utc_now
from app.db.audit import HIGH_RISK_EVENT_TYPES

logger = logging.getLogger(__name__)


class AuditUnavailableError(RuntimeError):
    """The audit trail could not be persisted for a high-risk mutation.

    Raised by :func:`audit_high_risk` so callers can fail closed (503): a
    security / permission / credential / DSR mutation must never appear to
    succeed while its audit evidence is lost.
    """


class AuditGapTracker:
    """Thread-safe in-process gap counter plus shared DB gap rows."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._local: dict[str, int] = {}

    def record_gap(self, database: Any, *, event_type: str) -> None:
        self._increment(event_type)
        now = utc_now()
        try:
            with database.connect() as connection:
                connection.execute(
                    "INSERT INTO audit_gaps (event_type, gap_count, last_seen_at) "
                    "VALUES (?, 1, ?) "
                    "ON CONFLICT(event_type) DO UPDATE SET "
                    "gap_count = audit_gaps.gap_count + 1, last_seen_at = ?",
                    (event_type, now, now),
                )
        except Exception:
            # The DB itself is unreachable; keep the in-process counter as the
            # only evidence and surface it on the next query.
            logger.exception("audit_gap.row_persist_failed", extra={"event_type": event_type})

    def _increment(self, event_type: str) -> None:
        with self._lock:
            count = self._local.get(event_type, 0) + 1
            self._local[event_type] = count

    @property
    def local_counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._local)


def _tracked_frontier_event(event_type: str) -> bool:
    return event_type in HIGH_RISK_EVENT_TYPES


def audit_high_risk(
    database: Any,
    *,
    tenant_id: str,
    conversation_id: str | None,
    actor: str,
    event_type: str,
    payload: dict[str, Any],
    reason: str,
) -> str:
    """Append a high-risk audit event and anchor its tip atomically.

    Runs inside one audit transaction: the audit row, the tip frontier row and
    the gap-handshake row commit or roll back together, so the ``audit_gap``
    for a successful high-risk mutation can never outlive its mutation.  Raises
    on audit failure so the caller can fail closed.
    """
    event_id = ""
    # Phase 43.2: high-risk events are written from authenticated request
    # paths (already scoped) and from system paths (not scoped) — bind the
    # event's own tenant so enforced RLS never rejects the evidence append.
    ambient_scope = nullcontext() if current_scope_mode() == "tenant" else tenant_scope(tenant_id)
    with ambient_scope:
        with database.audit_transaction() as connection:
            try:
                event_id = database.audit_in_transaction(
                    connection,
                    tenant_id=tenant_id,
                    conversation_id=conversation_id,
                    actor=actor,
                    event_type=event_type,
                    payload=payload,
                )
                tail_hash, tail_seq = database._audit_chain_tail(connection)
                connection.execute(
                    """INSERT INTO audit_anchors
                    (anchor_id, seq, chain_hash, event_type, reason, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        f"fr_{event_id}",
                        int(tail_seq),
                        str(tail_hash or ""),
                        event_type,
                        reason,
                        utc_now(),
                    ),
                )
            except Exception as exc:
                # The whole transaction (audit row + frontier anchor) is rolled
                # back by the context manager; surface a fail-closed signal so
                # the mutation is never mistaken for a success.
                logger.exception(
                    "audit_high_risk.failed",
                    extra={
                        "event_type": event_type,
                        "reason": reason,
                        "tenant_id": tenant_id,
                    },
                )
                raise AuditUnavailableError(
                    f"audit trail unavailable; {event_type} mutation rolled back"
                ) from exc
    return event_id


def record_audit_gap(
    database: Any,
    tracker: AuditGapTracker,
    *,
    event_type: str,
) -> None:
    """Best-effort path for ordinary telemetry appends that failed to persist.

    Deliberately swallowable — the caller already handled the audit exception —
    but never silent: a durable ``audit_gaps`` row plus an in-process counter
    make the miss observable to `GET /api/admin/audit/gaps`.
    """
    if _tracked_frontier_event(event_type):
        # High-risk mutations anchor atomically; a gap row here would be a
        # lie (the audit could still land in the same transaction).
        return
    tracker.record_gap(database, event_type=event_type)


def clear_gaps(database: Any) -> None:
    with database.connect() as connection:
        connection.execute("DELETE FROM audit_gaps")


def list_gaps(database: Any) -> list[dict[str, Any]]:
    """Return persisted gap rows; merge with in-process counts via ``merge_gap_rows``."""
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT event_type, gap_count, last_seen_at FROM audit_gaps "
            "ORDER BY gap_count DESC, event_type"
        ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        result.append(item)
    return result


def merge_gap_rows(
    rows: Iterable[dict[str, Any]], local_counts: dict[str, int]
) -> list[dict[str, Any]]:
    """Combine DB rows with the in-process totals of this process."""
    merged: dict[str, dict[str, Any]] = {row["event_type"]: dict(row) for row in rows}
    for event_type, count in local_counts.items():
        if event_type in merged:
            merged[event_type]["gap_count"] = int(merged[event_type]["gap_count"]) + count
        else:
            merged[event_type] = {
                "event_type": event_type,
                "gap_count": count,
                "last_seen_at": "",
            }
    return sorted(merged.values(), key=lambda item: item["event_type"])


__all__ = [
    "HIGH_RISK_EVENT_TYPES",
    "AuditGapTracker",
    "AuditUnavailableError",
    "audit_high_risk",
    "clear_gaps",
    "list_gaps",
    "merge_gap_rows",
    "record_audit_gap",
]
