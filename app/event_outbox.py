"""Transactional outbox for versioned domain events (ROADMAP 43.3).

Business writes and their domain events commit in the **same transaction**
(``record`` accepts the caller's open connection); a dispatcher drains
unpublished rows and marks them published. Consumers deduplicate on
``event_id`` — replaying a drain, or racing two dispatchers, must never
deliver one event twice to a well-behaved consumer, and the claim update is
guarded so only one dispatcher wins each row.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable
from uuid import uuid4

from app.db._util import utc_now
from app.event_schemas import latest_event_schema

logger = logging.getLogger("helix")

EventHandler = Callable[[dict[str, Any]], None]


def _known_types() -> set[str]:
    from app.event_schemas import all_event_schemas

    return set(all_event_schemas())


class UnknownEventTypeError(ValueError):
    """The event type has no registered schema — refuse to publish it."""


class DomainEventOutbox:
    def __init__(self, database: Any) -> None:
        self.database = database

    def record(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        connection: Any | None = None,
        occurred_at: str | None = None,
    ) -> str:
        """Append one domain event; commits with the caller's transaction.

        ``connection=None`` records on its own committed transaction (fine
        for tests and non-critical paths); production write paths pass the
        open connection so the business row and its event are atomic.
        """
        schema = latest_event_schema(event_type) if event_type in _known_types() else None
        if schema is None:
            raise UnknownEventTypeError(f"unknown domain event type: {event_type!r}")
        missing = [name for name in schema.fields if name not in payload]
        if missing:
            raise ValueError(
                f"event {event_type} v{schema.version} payload missing fields: {missing}"
            )
        event_id = f"evt_{uuid4().hex[:20]}"
        now = occurred_at or utc_now()
        statement = (
            "INSERT INTO domain_events "
            "(event_id, tenant_id, event_type, schema_version, payload_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        values = (
            event_id,
            tenant_id,
            event_type,
            schema.version,
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            now,
        )
        if connection is not None:
            connection.execute(statement, values)
        else:
            with self.database.connect() as own:
                own.execute(statement, values)
        return event_id

    def _row_to_event(self, row: Any) -> dict[str, Any]:
        return {
            "event_id": str(row["event_id"]),
            "tenant_id": str(row["tenant_id"]),
            "event_type": str(row["event_type"]),
            "schema_version": int(row["schema_version"]),
            "payload": json.loads(row["payload_json"]),
            "created_at": str(row["created_at"]),
        }

    def drain(self, handler: EventHandler, *, limit: int = 100) -> int:
        """Publish up to ``limit`` unpublished events oldest-first.

        Each row is atomically claimed (``published_at`` set under a
        ``IS NULL`` guard) **before** the handler runs, so two concurrent
        dispatchers can never both hand the same event_id over — the loser
        of the claim skips. If the handler raises, the claim is released so
        a later drain retries: delivery is at-least-once and consumers
        deduplicate by ``event_id``.
        """
        delivered = 0
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT event_id, tenant_id, event_type, schema_version,
                          payload_json, created_at
                FROM domain_events WHERE published_at IS NULL
                ORDER BY created_at, event_id LIMIT ?""",
                (max(1, limit),),
            ).fetchall()
        now = utc_now()
        for row in rows:
            with self.database.connect() as connection:
                claimed = connection.execute(
                    "UPDATE domain_events SET published_at = ? "
                    "WHERE event_id = ? AND published_at IS NULL",
                    (now, row["event_id"]),
                ).rowcount
            if not claimed:
                continue  # another dispatcher won this row
            event = self._row_to_event(row)
            try:
                handler(event)
            except Exception:
                # Release the claim so the event is retried later.
                with self.database.connect() as connection:
                    connection.execute(
                        "UPDATE domain_events SET published_at = NULL WHERE event_id = ?",
                        (row["event_id"],),
                    )
                raise
            delivered += 1
            logger.info(
                "outbox.published event_id=%s type=%s", row["event_id"], row["event_type"]
            )
        return delivered

    def pending_count(self) -> int:
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM domain_events WHERE published_at IS NULL"
            ).fetchone()
        return int(row["n"])