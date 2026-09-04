"""Database collaboration mixin (backlog: 坐席协作).

Operator-facing collaboration primitives:

* ``conversation_mentions`` — every internal note that ``@``-mentions a
  colleague records a row here, scoped by ``mentioned_actor`` so each
  operator only ever sees their own inbox (tenant-scoped).
* ``conversation_revision`` — a deterministic, multi-process-safe watermark
  for the supervisor live-view (旁观模式) SSE: one read-only query built from
  the conversation ``updated_at`` and the latest message ``seq``, so the
  stream needs no shared in-memory state and works identically on SQLite and
  PostgreSQL.
"""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
from typing import Any
from uuid import uuid4

from app.db._util import utc_now


class DatabaseCollaborationMixin:
    def record_mentions(
        self,
        tenant_id: str,
        conversation_id: str,
        note_id: str,
        mentioned_by: str,
        mentioned_actors: list[str],
    ) -> int:
        """Record one ``conversation_mentions`` row per mentioned colleague.

        The same note id can never repeat, so a fresh id per row is safe and
        there is no accidental read-state reuse when a note is re-created.
        """
        if not mentioned_actors:
            return 0
        now = utc_now()
        rows: list[dict[str, Any]] = []
        for actor in dict.fromkeys(mentioned_actors):
            rows.append(
                {
                    "id": f"men_{uuid4().hex[:12]}",
                    "tenant_id": tenant_id,
                    "conversation_id": conversation_id,
                    "note_id": note_id,
                    "mentioned_actor": actor,
                    "mentioned_by": mentioned_by,
                    "created_at": now,
                }
            )
        with self.connect() as connection:
            connection.executemany(
                """INSERT INTO conversation_mentions
                (id, tenant_id, conversation_id, note_id, mentioned_actor,
                 mentioned_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                [
                    (
                        item["id"],
                        item["tenant_id"],
                        item["conversation_id"],
                        item["note_id"],
                        item["mentioned_actor"],
                        item["mentioned_by"],
                        item["created_at"],
                    )
                    for item in rows
                ],
            )
        return len(rows)

    def list_mentions_for_actor(
        self,
        tenant_id: str,
        actor_id: str,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List the given actor's mentions with conversation context.

        Rows are scoped to ``tenant_id`` and ``mentioned_actor``, so an
        operator can never see another tenant's or another colleague's
        mentions. Unread rows sort first (oldest unread first), then newest.
        """
        clauses = ["m.tenant_id = ?", "m.mentioned_actor = ?"]
        values: list[Any] = [tenant_id, actor_id]
        if unread_only:
            clauses.append("m.read_at IS NULL")
        where = " AND ".join(clauses)
        query = f"""
            SELECT m.id, m.conversation_id, m.note_id, m.mentioned_actor,
                   m.mentioned_by, m.created_at, m.read_at,
                   c.customer_name, c.channel, c.status,
                   n.content AS note_content
            FROM conversation_mentions m
            LEFT JOIN conversations c
                   ON c.tenant_id = m.tenant_id AND c.id = m.conversation_id
            LEFT JOIN messages n
                   ON n.tenant_id = m.tenant_id AND n.id = m.note_id
            WHERE {where}
            ORDER BY (m.read_at IS NULL) DESC, m.created_at ASC, m.id ASC
            LIMIT ? OFFSET ?
        """
        values.extend([max(1, limit), max(0, offset)])
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["unread"] = item["read_at"] is None
            item["note_preview"] = (item.get("note_content") or "")[:160].strip()
            result.append(item)
        return result

    def count_unread_mentions(self, tenant_id: str, actor_id: str) -> int:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM conversation_mentions "
                "WHERE tenant_id = ? AND mentioned_actor = ? AND read_at IS NULL",
                (tenant_id, actor_id),
            ).fetchone()
        return int(row["n"] if row else 0)

    def mark_mention_read(
        self, tenant_id: str, actor_id: str, mention_id: str
    ) -> dict[str, Any] | None:
        """Idempotently mark one of the actor's mentions as read.

        Returns the row (read state included) so the caller can audit the
        change, or ``None`` when the mention does not belong to this actor in
        this tenant (so a cross-actor read is impossible).
        """
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                "UPDATE conversation_mentions SET read_at = COALESCE(read_at, ?) "
                "WHERE tenant_id = ? AND mentioned_actor = ? AND id = ?",
                (now, tenant_id, actor_id, mention_id),
            )
            row = connection.execute(
                "SELECT id, conversation_id, note_id, mentioned_actor, "
                "mentioned_by, created_at, read_at FROM conversation_mentions "
                "WHERE tenant_id = ? AND mentioned_actor = ? AND id = ?",
                (tenant_id, actor_id, mention_id),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["unread"] = item["read_at"] is None
        return item

    def conversation_revision(self, tenant_id: str, conversation_id: str) -> str:
        """Deterministic watermark for the supervisor live-view SSE.

        Combines the conversation ``updated_at`` with the latest message
        ``seq`` so both state transitions and new messages change the value.
        A pure query means the stream holds no in-memory counters, so it stays
        correct under multi-instance/PostgreSQL setups.
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT c.updated_at, COALESCE(MAX(m.seq), 0) AS seq "
                "FROM conversations c "
                "LEFT JOIN messages m "
                "ON m.tenant_id = c.tenant_id AND m.conversation_id = c.id "
                "WHERE c.tenant_id = ? AND c.id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        if not row:
            return "missing"
        return f"{row['updated_at']}:{int(row['seq'])}"

    def list_notes(self, tenant_id: str, conversation_id: str) -> list[dict[str, Any]]:
        """List the conversation's internal notes in discussion order.

        Only ``internal_note`` messages are returned, each carrying
        ``reply_to`` (the note it replies to, when threaded) and the usual
        message shape minus internal columns. The layer is intentionally free
        of grouping logic — the API groups roots and replies for the client.
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM messages WHERE tenant_id = ? "
                "AND conversation_id = ? AND role = 'internal_note' "
                "ORDER BY created_at ASC, seq ASC",
                (tenant_id, conversation_id),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json"))
            item.pop("tenant_id", None)
            item.pop("conversation_id", None)
            item.pop("turn_id", None)
            item.pop("seq", None)
            result.append(item)
        return result
