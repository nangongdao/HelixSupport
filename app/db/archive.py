"""Database archive mixin (ROADMAP 18.3): cold-tier conversation archival."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
from typing import Any

from app.db._util import utc_now
from app.db.messages import _message_row_to_item, _query_messages


class DatabaseArchiveMixin:
    """Move resolved conversations out of the hot tables into a read-only tier.

    ROADMAP 18.3: resolved conversations closed more than ``before_dt`` days
    ago are copied into the ``*_archive`` tables and then physically deleted
    from the hot tables.  The hot-table deletes cascade into the message FTS
    mirror via the existing triggers, which is how the tier bounds queue index
    and FTS volume.  The archive tables carry no triggers, FTS mirror, or
    foreign keys: they are a cold, immutable snapshot, and the write path
    stays hot-only (mutations on an archived conversation fail with 404).
    """

    def archive_resolved_conversations(self, before_dt: str, max_count: int = 100) -> int:
        """Archive up to ``max_count`` resolved conversations closed before ``before_dt``.

        Each conversation is moved atomically with its messages and labels in
        one transaction; the whole batch rolls back on any failure.  Returns
        the number of conversations archived.  Idempotent: already-archived
        conversations no longer appear in the hot tables, so a replay archives
        nothing.
        """
        now = utc_now()
        archived = 0
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id FROM conversations c
                WHERE c.status = 'resolved' AND c.resolved_at IS NOT NULL
                  AND c.resolved_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM turn_jobs j
                      WHERE j.conversation_id = c.id
                        AND j.status IN ('queued', 'processing'))
                ORDER BY c.updated_at ASC LIMIT ?""",
                (before_dt, max(1, max_count)),
            ).fetchall()
            for row in rows:
                self._move_conversation_to_archive(connection, str(row["id"]), now)
                archived += 1
        return archived

    def _move_conversation_to_archive(
        self, connection: Any, conversation_id: str, now: str
    ) -> None:
        """Copy one conversation (row, messages, labels) into the archive tier."""
        connection.execute(
            """INSERT INTO conversations_archive
            (id, tenant_id, customer_name, customer_ref, channel, status, intent,
             assigned_agent, priority, handoff_reason, sla_due_at, last_confidence,
             version, preview, message_count, last_message_at, labels_json,
             claimed_by, claimed_at, claim_expires_at, needs_response, waiting_since,
             first_response_at, created_at, updated_at, resolved_at, archived_at)
            SELECT id, tenant_id, customer_name, customer_ref, channel, status, intent,
                   assigned_agent, priority, handoff_reason, sla_due_at, last_confidence,
                   version, preview, message_count, last_message_at, labels_json,
                   claimed_by, claimed_at, claim_expires_at, needs_response, waiting_since,
                   first_response_at, created_at, updated_at, resolved_at, ?
            FROM conversations WHERE id = ?""",
            (now, conversation_id),
        )
        connection.execute(
            """INSERT INTO messages_archive
            (id, tenant_id, conversation_id, turn_id, role, author, content,
             metadata_json, created_at, seq, channel_message_id, reply_to)
            SELECT id, tenant_id, conversation_id, turn_id, role, author, content,
                   metadata_json, created_at, seq, channel_message_id, reply_to
            FROM messages WHERE conversation_id = ?""",
            (conversation_id,),
        )
        connection.execute(
            """INSERT INTO conversation_labels_archive
            (tenant_id, conversation_id, label, created_by, created_at)
            SELECT tenant_id, conversation_id, label, created_by, created_at
            FROM conversation_labels WHERE conversation_id = ?""",
            (conversation_id,),
        )
        # The hot ``conversations.labels_json`` projection can drift from the
        # ``conversation_labels`` rows (e.g. labels added through the labels
        # table), so recompute it from the archived labels to keep the cold
        # snapshot self-consistent.
        label_rows = connection.execute(
            "SELECT label FROM conversation_labels_archive WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchall()
        connection.execute(
            "UPDATE conversations_archive SET labels_json = ? WHERE id = ?",
            (
                json.dumps(sorted({str(r["label"]) for r in label_rows}), ensure_ascii=False),
                conversation_id,
            ),
        )
        connection.execute(
            """INSERT INTO feedback_archive
            (id, tenant_id, conversation_id, message_id, actor, rating, reason,
             created_at, updated_at)
            SELECT id, tenant_id, conversation_id, message_id, actor, rating, reason,
                   created_at, updated_at
            FROM feedback WHERE conversation_id = ?""",
            (conversation_id,),
        )
        # Delete children first (FK: feedback.message_id -> messages, and
        # messages/labels/feedback/turn_* -> conversations).  The message
        # deletes also drop the matching message_fts rows, bounding FTS volume
        # — the point of the tier.  Turn request/job rows are operational and
        # already subject to retention pruning, so they are discarded here;
        # the candidate SELECT above guarantees none are active.
        connection.execute(
            "DELETE FROM conversation_labels WHERE conversation_id = ?", (conversation_id,)
        )
        connection.execute("DELETE FROM feedback WHERE conversation_id = ?", (conversation_id,))
        connection.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        connection.execute(
            "DELETE FROM turn_requests WHERE conversation_id = ?", (conversation_id,)
        )
        connection.execute("DELETE FROM turn_jobs WHERE conversation_id = ?", (conversation_id,))
        connection.execute("DELETE FROM conversations WHERE id = ?", (conversation_id,))

    def get_archived_conversation(
        self, tenant_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """Resolve a conversation that has been moved to the archive tier.

        Mirrors ``get_conversation``'s row shape so callers can treat archived
        and hot conversations interchangeably (the read-path transparent merge).
        """
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations_archive WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        return self._conversation_from_row(row)

    def list_archived_messages(
        self,
        tenant_id: str,
        conversation_id: str,
        *,
        limit: int | None = None,
        cursor: tuple[str, int] | None = None,
        before: bool = False,
    ) -> list[dict[str, Any]]:
        """Keyset-paginate an archived conversation's message history."""
        with self.connect() as connection:
            rows = _query_messages(
                connection,
                "messages_archive",
                tenant_id,
                conversation_id,
                limit=limit,
                cursor=cursor,
                before=before,
            )
        result = [_message_row_to_item(row) for row in rows]
        if before:
            result.reverse()
        return result
