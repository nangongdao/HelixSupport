"""Cross-region asynchronous replication (ROADMAP 2.2.2).

Provides eventual consistency across regions:
- ReplicationLog: records data changes to be replicated
- ReplicationWorker: background task that pushes changes to target regions
- Conflict resolution: last-write-wins based on timestamp
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass
from typing import Any

import httpx

from app.db._util import utc_now

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ReplicationLogEntry:
    """A single replication log entry."""

    id: str
    tenant_id: str
    source_region: str
    target_region: str
    table_name: str
    row_id: str
    operation: str  # "insert" | "update" | "delete"
    payload_json: str
    created_at: str
    replicated_at: str | None = None


@dataclass
class ReplicationStats:
    """Statistics for a replication run."""

    entries_processed: int
    entries_succeeded: int
    entries_failed: int
    duration_seconds: float


class ReplicationLog:
    """Records data changes for cross-region replication."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def record_change(
        self,
        tenant_id: str,
        source_region: str,
        target_region: str,
        table_name: str,
        row_id: str,
        operation: str,
        payload: dict[str, Any],
    ) -> str:
        """Record a change to be replicated.

        Args:
            tenant_id: Tenant owning this data
            source_region: Source region identifier
            target_region: Target region identifier
            table_name: Table being modified
            row_id: Primary key of the row
            operation: "insert", "update", or "delete"
            payload: Full row data (for insert/update) or metadata (for delete)

        Returns:
            Replication log entry ID
        """
        entry_id = f"repl-{int(time.time() * 1000)}-{secrets.token_hex(4)}"
        payload_json = json.dumps(payload)

        with self._database.connect() as conn:
            conn.execute(
                """
                INSERT INTO replication_log (
                    id, tenant_id, source_region, target_region,
                    table_name, row_id, operation, payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    entry_id,
                    tenant_id,
                    source_region,
                    target_region,
                    table_name,
                    row_id,
                    operation,
                    payload_json,
                    utc_now(),
                ),
            )

        logger.info(
            "replication.recorded",
            extra={
                "entry_id": entry_id,
                "tenant_id": tenant_id,
                "table": table_name,
                "operation": operation,
            },
        )
        return entry_id

    def get_pending_entries(
        self, target_region: str, limit: int = 100
    ) -> list[ReplicationLogEntry]:
        """Get pending replication entries for a target region.

        Args:
            target_region: Target region to filter by
            limit: Maximum number of entries to return

        Returns:
            List of pending replication log entries
        """
        with self._database.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, tenant_id, source_region, target_region,
                       table_name, row_id, operation, payload_json,
                       created_at, replicated_at
                FROM replication_log
                WHERE target_region = ? AND replicated_at IS NULL
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (target_region, limit),
            ).fetchall()

        return [
            ReplicationLogEntry(
                id=row["id"],
                tenant_id=row["tenant_id"],
                source_region=row["source_region"],
                target_region=row["target_region"],
                table_name=row["table_name"],
                row_id=row["row_id"],
                operation=row["operation"],
                payload_json=row["payload_json"],
                created_at=row["created_at"],
                replicated_at=row["replicated_at"],
            )
            for row in rows
        ]

    def mark_replicated(self, entry_id: str) -> None:
        """Mark a replication entry as successfully replicated."""
        with self._database.connect() as conn:
            conn.execute(
                "UPDATE replication_log SET replicated_at = ? WHERE id = ?",
                (utc_now(), entry_id),
            )

    def count_pending(self, target_region: str) -> int:
        """Count pending entries for a target region."""
        with self._database.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM replication_log WHERE target_region = ? AND replicated_at IS NULL",
                (target_region,),
            ).fetchone()
        return int(row["n"]) if row else 0


class ReplicationWorker:
    """Background worker that pushes replication log entries to target regions."""

    def __init__(
        self,
        database: Any,
        replication_log: ReplicationLog,
        target_region: str,
        target_base_url: str,
        batch_size: int = 50,
    ) -> None:
        self._database = database
        self._replication_log = replication_log
        self._target_region = target_region
        self._target_base_url = target_base_url
        self._batch_size = batch_size

    async def replicate_batch(self) -> ReplicationStats:
        """Process one batch of pending replication entries.

        Returns:
            Statistics for this batch run
        """
        import time

        start = time.monotonic()
        entries = self._replication_log.get_pending_entries(
            self._target_region, limit=self._batch_size
        )

        if not entries:
            return ReplicationStats(
                entries_processed=0,
                entries_succeeded=0,
                entries_failed=0,
                duration_seconds=time.monotonic() - start,
            )

        succeeded = 0
        failed = 0

        for entry in entries:
            try:
                await self._push_entry(entry)
                self._replication_log.mark_replicated(entry.id)
                succeeded += 1
            except Exception:
                logger.exception(
                    "replication.push_failed",
                    extra={
                        "entry_id": entry.id,
                        "table": entry.table_name,
                        "target_region": self._target_region,
                    },
                )
                failed += 1

        duration = time.monotonic() - start
        logger.info(
            "replication.batch_complete",
            extra={
                "target_region": self._target_region,
                "processed": len(entries),
                "succeeded": succeeded,
                "failed": failed,
                "duration_seconds": round(duration, 3),
            },
        )

        return ReplicationStats(
            entries_processed=len(entries),
            entries_succeeded=succeeded,
            entries_failed=failed,
            duration_seconds=duration,
        )

    async def _push_entry(self, entry: ReplicationLogEntry) -> None:
        """Push a single replication entry to the target region.

        Args:
            entry: Replication log entry to push

        Raises:
            httpx.HTTPError: If the push request fails
        """
        payload = json.loads(entry.payload_json)
        url = f"{self._target_base_url}/api/internal/replication/apply"

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                url,
                json={
                    "entry_id": entry.id,
                    "tenant_id": entry.tenant_id,
                    "table_name": entry.table_name,
                    "row_id": entry.row_id,
                    "operation": entry.operation,
                    "payload": payload,
                    "source_region": entry.source_region,
                    "created_at": entry.created_at,
                },
            )
            response.raise_for_status()


async def periodic_replication(
    worker: ReplicationWorker, interval_seconds: int = 60
) -> None:
    """Background task to periodically replicate pending changes.

    Args:
        worker: Replication worker instance
        interval_seconds: Interval between replication runs
    """
    while True:
        try:
            stats = await worker.replicate_batch()
            if stats.entries_processed > 0:
                logger.info(
                    "replication.periodic_run",
                    extra={
                        "processed": stats.entries_processed,
                        "succeeded": stats.entries_succeeded,
                        "failed": stats.entries_failed,
                    },
                )
        except Exception:
            logger.exception("replication.periodic_run_failed")

        await asyncio.sleep(interval_seconds)
