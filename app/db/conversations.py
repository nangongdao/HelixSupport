"""Database conversations mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import json
import sqlite3
from typing import Any, Sequence
from uuid import uuid4

from app.db._util import (
    utc_after,
    utc_after_seconds,
    utc_now,
)
from app.domain import ConversationStatus
from app.labels import normalize_conversation_labels


class DatabaseConversationsMixin:
    def _conversation_from_row(
        self, row: sqlite3.Row | dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if row is None:
            return None
        item = dict(row)
        item["labels"] = json.loads(item.pop("labels_json", "[]") or "[]")
        now = utc_now()
        item["claim_active"] = bool(
            item.get("claimed_by")
            and item.get("claim_expires_at")
            and item["claim_expires_at"] > now
        )
        if not item["claim_active"]:
            item["claimed_by"] = None
            item["claimed_at"] = None
            item["claim_expires_at"] = None
        return item

    def create_conversation(
        self,
        tenant_id: str,
        customer_name: str,
        customer_ref: str | None,
        channel: str,
        actor: str,
        sla_minutes: int,
        connection: Any | None = None,
    ) -> dict[str, Any]:
        conversation_id = f"conv_{uuid4().hex[:12]}"
        now = utc_now()
        # Backlog: SLA policy engine — resolve the first-response/resolve
        # deadline from tenant/priority/channel policy instead of the caller's
        # hardcoded minutes (which remains the fallback).
        effective_sla = self.resolve_sla_policy(tenant_id, "normal", channel, sla_minutes)
        # 43.3: when the caller supplies an open transaction (v2 ingress +
        # transactional outbox), the business row commits atomically with its
        # domain event. Post-steps (quota metering, audit, cache invalidation)
        # open their own connections and would deadlock SQLite's single
        # writer inside this uncommitted transaction, so the CALLER runs them
        # right after commit.
        if connection is not None:
            connection.execute(
                """INSERT INTO conversations
                (id, tenant_id, customer_name, customer_ref, channel, status, priority,
                 sla_due_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)""",
                (
                    conversation_id,
                    tenant_id,
                    customer_name,
                    customer_ref,
                    channel,
                    ConversationStatus.OPEN,
                    utc_after(effective_sla),
                    now,
                    now,
                ),
            )
            return {
                "id": conversation_id,
                "tenant_id": tenant_id,
                "customer_name": customer_name,
                "customer_ref": customer_ref,
                "channel": channel,
                "status": ConversationStatus.OPEN,
                "priority": "normal",
                "created_at": now,
                "updated_at": now,
            }
        with self.connect() as own_connection:
            own_connection.execute(
                """INSERT INTO conversations
                (id, tenant_id, customer_name, customer_ref, channel, status, priority,
                 sla_due_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 'normal', ?, ?, ?)""",
                (
                    conversation_id,
                    tenant_id,
                    customer_name,
                    customer_ref,
                    channel,
                    ConversationStatus.OPEN,
                    utc_after(effective_sla),
                    now,
                    now,
                ),
            )
        self._invalidate_dashboard(tenant_id)
        self.increment_tenant_usage_conversations(tenant_id, utc_now()[:10])
        self.audit(
            tenant_id,
            conversation_id,
            actor,
            "conversation.created",
            {"channel": channel, "customer_verified": bool(customer_ref)},
        )
        return self.get_conversation(tenant_id, conversation_id) or {}

    def get_conversation(self, tenant_id: str, conversation_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        if row is None:
            # ROADMAP 18.3: read-path transparent merge — a conversation that
            # has been moved to the archive tier is still resolvable by id, so
            # existing detail links keep working after archiving.
            return self.get_archived_conversation(tenant_id, conversation_id)
        return self._conversation_from_row(row)

    def set_conversation_language(
        self, tenant_id: str, conversation_id: str, language: str | None
    ) -> None:
        """Record the conversation language (or clear it back to auto).

        Backlog (多语言客服): only writes when the stored language differs, so
        the version/updated_at metadata is untouched on repeated messages in
        the same language. ``None`` clears the manual override and lets the
        writer path re-detect automatically.

        The null-safe inequality is written portably (``IS ?`` is SQLite-only
        and breaks the PG dialect): the row is skipped only when stored equals
        ``language``, including both-NULL.
        """
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE conversations SET language = ?
                WHERE tenant_id = ? AND id = ?
                  AND (language IS NULL OR ? IS NULL OR language <> ?)""",
                (language, tenant_id, conversation_id, language, language),
            )
            if cursor.rowcount == 1:
                self._invalidate_dashboard(tenant_id)

    def set_routing(
        self,
        tenant_id: str,
        conversation_id: str,
        status: ConversationStatus,
        intent: str,
        assigned_agent: str,
        priority: str,
        handoff_reason: str | None,
        confidence: float,
        sla_minutes: int,
    ) -> bool:
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE conversations
                SET status = ?, intent = ?, assigned_agent = ?, priority = ?,
                    handoff_reason = ?, last_confidence = ?,
                    sla_due_at = CASE WHEN ? = 'waiting_human' THEN ? ELSE sla_due_at END,
                    updated_at = ?, version = version + 1
                WHERE tenant_id = ? AND id = ? AND status = 'open'""",
                (
                    status,
                    intent,
                    assigned_agent,
                    priority,
                    handoff_reason,
                    confidence,
                    status,
                    utc_after(sla_minutes),
                    now,
                    tenant_id,
                    conversation_id,
                ),
            )
        updated = cursor.rowcount == 1
        if updated:
            self._invalidate_dashboard(tenant_id)
        return updated

    def transition_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        expected_statuses: Sequence[ConversationStatus | str],
        new_status: ConversationStatus,
        assigned_agent: str | None = None,
        clear_handoff: bool = False,
        clear_assignment: bool = False,
        clear_claim: bool = False,
        sla_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        statuses = [str(status) for status in expected_statuses]
        if not statuses:
            raise ValueError("expected_statuses cannot be empty")
        placeholders = ", ".join("?" for _ in statuses)
        resolved_at = utc_now() if new_status == ConversationStatus.RESOLVED else None
        sla_due_at = utc_after(sla_minutes) if sla_minutes is not None else None
        clear_claim_flag = clear_claim or new_status == ConversationStatus.RESOLVED
        with self.connect() as connection:
            cursor = connection.execute(
                f"""UPDATE conversations
                SET status = ?,
                    assigned_agent = CASE WHEN ? THEN NULL
                                          ELSE COALESCE(?, assigned_agent) END,
                    handoff_reason = CASE WHEN ? THEN NULL ELSE handoff_reason END,
                    claimed_by = CASE WHEN ? THEN NULL ELSE claimed_by END,
                    claimed_at = CASE WHEN ? THEN NULL ELSE claimed_at END,
                    claim_expires_at = CASE WHEN ? THEN NULL ELSE claim_expires_at END,
                    sla_due_at = COALESCE(?, sla_due_at),
                    resolved_at = ?, updated_at = ?, version = version + 1
                WHERE tenant_id = ? AND id = ? AND status IN ({placeholders})""",
                (
                    new_status,
                    int(clear_assignment),
                    assigned_agent,
                    int(clear_handoff),
                    int(clear_claim_flag),
                    int(clear_claim_flag),
                    int(clear_claim_flag),
                    sla_due_at,
                    resolved_at,
                    utc_now(),
                    tenant_id,
                    conversation_id,
                    *statuses,
                ),
            )
        if cursor.rowcount != 1:
            return None
        self._invalidate_dashboard(tenant_id)
        return self.get_conversation(tenant_id, conversation_id)

    def claim_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        claim_seconds: int,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if claim_seconds < 30:
            raise ValueError("claim_seconds must be at least 30")
        now = utc_now()
        expires_at = utc_after_seconds(claim_seconds)
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
            if current is None:
                return None
            if current["status"] == ConversationStatus.RESOLVED:
                return None
            active_claim = (
                current["claimed_by"]
                and current["claim_expires_at"]
                and current["claim_expires_at"] > now
            )
            if active_claim and current["claimed_by"] != actor_id and not force:
                return None
            if active_claim and current["claimed_by"] == actor_id:
                cursor = connection.execute(
                    """UPDATE conversations
                    SET claimed_at = ?, claim_expires_at = ?, updated_at = ?, version = version + 1
                    WHERE tenant_id = ? AND id = ?""",
                    (now, expires_at, now, tenant_id, conversation_id),
                )
            else:
                cursor = connection.execute(
                    """UPDATE conversations
                    SET claimed_by = ?, claimed_at = ?, claim_expires_at = ?,
                        updated_at = ?, version = version + 1
                    WHERE tenant_id = ? AND id = ?
                      AND status != 'resolved'
                      AND (
                        claimed_by IS NULL OR claim_expires_at IS NULL
                        OR claim_expires_at <= ? OR claimed_by = ? OR ? = 1
                      )""",
                    (
                        actor_id,
                        now,
                        expires_at,
                        now,
                        tenant_id,
                        conversation_id,
                        now,
                        actor_id,
                        int(force),
                    ),
                )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        self._invalidate_dashboard(tenant_id)
        return self._conversation_from_row(updated)

    def assign_conversation(
        self,
        tenant_id: str,
        conversation_id: str,
        assignee_id: str,
        claim_seconds: int,
        *,
        force: bool = False,
        actor_id: str | None = None,
    ) -> dict[str, Any] | None:
        if claim_seconds < 30:
            raise ValueError("claim_seconds must be at least 30")
        now = utc_now()
        expires_at = utc_after_seconds(claim_seconds)
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
            if current is None or current["status"] == ConversationStatus.RESOLVED:
                return None
            active_claim = (
                current["claimed_by"]
                and current["claim_expires_at"]
                and current["claim_expires_at"] > now
            )
            if (
                active_claim
                and current["claimed_by"] not in {None, assignee_id, actor_id}
                and not force
            ):
                return None
            cursor = connection.execute(
                """UPDATE conversations
                SET assigned_agent = ?, claimed_by = ?, claimed_at = ?, claim_expires_at = ?,
                    status = CASE
                        WHEN status IN ('open', 'waiting_human') THEN 'human_active'
                        ELSE status
                    END,
                    updated_at = ?, version = version + 1
                WHERE tenant_id = ? AND id = ? AND status != 'resolved'""",
                (
                    assignee_id,
                    assignee_id,
                    now,
                    expires_at,
                    now,
                    tenant_id,
                    conversation_id,
                ),
            )
            if cursor.rowcount != 1:
                return None
        self._invalidate_dashboard(tenant_id)
        return self.get_conversation(tenant_id, conversation_id)

    def bulk_claim_conversations(
        self,
        tenant_id: str,
        conversation_ids: Sequence[str],
        actor_id: str,
        claim_seconds: int,
        *,
        force: bool = False,
    ) -> tuple[list[str], int]:
        ids = list(dict.fromkeys(conversation_ids))
        if not ids:
            return [], 0
        if claim_seconds < 30:
            raise ValueError("claim_seconds must be at least 30")
        now = utc_now()
        expires_at = utc_after_seconds(claim_seconds)
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id, status, claimed_by, claim_expires_at FROM conversations
                WHERE tenant_id = ? AND id IN ({placeholders})""",
                (tenant_id, *ids),
            ).fetchall()
            matched = len(rows)
            claimable: list[str] = []
            for row in rows:
                if row["status"] == ConversationStatus.RESOLVED:
                    continue
                active_claim = (
                    row["claimed_by"] and row["claim_expires_at"] and row["claim_expires_at"] > now
                )
                if active_claim and row["claimed_by"] != actor_id and not force:
                    continue
                claimable.append(str(row["id"]))
            if claimable:
                claim_placeholders = ", ".join("?" for _ in claimable)
                connection.execute(
                    f"""UPDATE conversations
                    SET claimed_by = ?, claimed_at = ?, claim_expires_at = ?,
                        updated_at = ?, version = version + 1
                    WHERE tenant_id = ? AND id IN ({claim_placeholders})
                      AND status != 'resolved'""",
                    (actor_id, now, expires_at, now, tenant_id, *claimable),
                )
        if claimable:
            self._invalidate_dashboard(tenant_id)
        return claimable, matched

    def bulk_release_claims(
        self,
        tenant_id: str,
        conversation_ids: Sequence[str],
        actor_id: str,
        *,
        force: bool = False,
    ) -> tuple[list[str], int]:
        ids = list(dict.fromkeys(conversation_ids))
        if not ids:
            return [], 0
        now = utc_now()
        placeholders = ", ".join("?" for _ in ids)
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id, claimed_by, claim_expires_at FROM conversations
                WHERE tenant_id = ? AND id IN ({placeholders})""",
                (tenant_id, *ids),
            ).fetchall()
            matched = len(rows)
            releasable: list[str] = []
            for row in rows:
                active_claim = (
                    row["claimed_by"] and row["claim_expires_at"] and row["claim_expires_at"] > now
                )
                if not active_claim:
                    continue
                if row["claimed_by"] != actor_id and not force:
                    continue
                releasable.append(str(row["id"]))
            if releasable:
                release_placeholders = ", ".join("?" for _ in releasable)
                connection.execute(
                    f"""UPDATE conversations
                    SET claimed_by = NULL, claimed_at = NULL, claim_expires_at = NULL,
                        updated_at = ?, version = version + 1
                    WHERE tenant_id = ? AND id IN ({release_placeholders})""",
                    (now, tenant_id, *releasable),
                )
        if releasable:
            self._invalidate_dashboard(tenant_id)
        return releasable, matched

    def release_conversation_claim(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any] | None:
        now = utc_now()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
            if current is None:
                return None
            active_claim = (
                current["claimed_by"]
                and current["claim_expires_at"]
                and current["claim_expires_at"] > now
            )
            if not active_claim:
                item = dict(current)
                item["labels"] = json.loads(item.pop("labels_json", "[]") or "[]")
                return item
            if current["claimed_by"] != actor_id and not force:
                return None
            cursor = connection.execute(
                """UPDATE conversations
                SET claimed_by = NULL, claimed_at = NULL, claim_expires_at = NULL,
                    updated_at = ?, version = version + 1
                WHERE tenant_id = ? AND id = ?""",
                (now, tenant_id, conversation_id),
            )
            if cursor.rowcount != 1:
                return None
            updated = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        self._invalidate_dashboard(tenant_id)
        return self._conversation_from_row(updated)

    def update_priority(
        self,
        tenant_id: str,
        conversation_id: str,
        priority: str,
        sla_minutes: int | None = None,
    ) -> dict[str, Any] | None:
        if priority not in {"normal", "high"}:
            raise ValueError("Unsupported conversation priority")
        tightened_due_at = utc_after(sla_minutes) if sla_minutes is not None else None
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE conversations
                SET priority = ?,
                    sla_due_at = CASE
                        WHEN ? IS NOT NULL AND status != 'resolved'
                             AND (sla_due_at IS NULL OR sla_due_at > ?)
                        THEN ? ELSE sla_due_at END,
                    updated_at = ?, version = version + 1
                WHERE tenant_id = ? AND id = ?""",
                (
                    priority,
                    tightened_due_at,
                    tightened_due_at,
                    tightened_due_at,
                    utc_now(),
                    tenant_id,
                    conversation_id,
                ),
            )
        if cursor.rowcount != 1:
            return None
        self._invalidate_dashboard(tenant_id)
        return self.get_conversation(tenant_id, conversation_id)

    def replace_conversation_labels(
        self,
        tenant_id: str,
        conversation_id: str,
        labels: Sequence[str],
        actor_id: str,
    ) -> tuple[dict[str, Any] | None, bool]:
        normalized = normalize_conversation_labels(labels)
        now = utc_now()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
            if current is None:
                return None, False
            existing = json.loads(current["labels_json"] or "[]")
            if existing == normalized:
                return dict(current), False
            connection.execute(
                "DELETE FROM conversation_labels WHERE tenant_id = ? AND conversation_id = ?",
                (tenant_id, conversation_id),
            )
            connection.executemany(
                """INSERT INTO conversation_labels
                (tenant_id, conversation_id, label, created_by, created_at)
                VALUES (?, ?, ?, ?, ?)""",
                [(tenant_id, conversation_id, label, actor_id, now) for label in normalized],
            )
            connection.execute(
                """UPDATE conversations SET labels_json = ?, version = version + 1
                WHERE tenant_id = ? AND id = ?""",
                (json.dumps(normalized, ensure_ascii=False), tenant_id, conversation_id),
            )
            updated = connection.execute(
                "SELECT * FROM conversations WHERE tenant_id = ? AND id = ?",
                (tenant_id, conversation_id),
            ).fetchone()
        return (dict(updated) if updated else None), True

    def list_conversation_labels(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT label, COUNT(*) AS conversation_count
                FROM conversation_labels WHERE tenant_id = ?
                GROUP BY label ORDER BY conversation_count DESC, label""",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_saved_views(self, tenant_id: str, actor_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT id, tenant_id, actor_id, name, filters_json, created_at, updated_at
                FROM saved_views WHERE tenant_id = ? AND actor_id = ?
                ORDER BY updated_at DESC, id DESC""",
                (tenant_id, actor_id),
            ).fetchall()
        return [self._saved_view_row(row) for row in rows]

    def create_saved_view(
        self, tenant_id: str, actor_id: str, name: str, filters: dict[str, Any]
    ) -> dict[str, Any]:
        now = utc_now()
        view_id = f"view_{uuid4().hex}"
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO saved_views
                (id, tenant_id, actor_id, name, filters_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    view_id,
                    tenant_id,
                    actor_id,
                    name,
                    json.dumps(filters, ensure_ascii=False),
                    now,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM saved_views WHERE id = ?", (view_id,)
            ).fetchone()
        return self._saved_view_row(row)

    def delete_saved_view(self, tenant_id: str, actor_id: str, view_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM saved_views WHERE id = ? AND tenant_id = ? AND actor_id = ?",
                (view_id, tenant_id, actor_id),
            )
        return cursor.rowcount == 1

    @staticmethod
    def _saved_view_row(row: sqlite3.Row | None) -> dict[str, Any]:
        if row is None:
            raise ValueError("Saved view not found")
        item = dict(row)
        item["filters"] = json.loads(item.pop("filters_json"))
        return item

    def bulk_update_priority(
        self,
        tenant_id: str,
        conversation_ids: Sequence[str],
        priority: str,
        sla_minutes: int | None = None,
    ) -> tuple[list[dict[str, str]], int]:
        if priority not in {"normal", "high"}:
            raise ValueError("Unsupported conversation priority")
        ids = list(dict.fromkeys(conversation_ids))
        if not ids:
            return [], 0
        placeholders = ", ".join("?" for _ in ids)
        tightened_due_at = utc_after(sla_minutes) if sla_minutes is not None else None
        now = utc_now()
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id, priority FROM conversations
                WHERE tenant_id = ? AND id IN ({placeholders})""",
                (tenant_id, *ids),
            ).fetchall()
            changed = [
                {"id": str(row["id"]), "from": str(row["priority"]), "to": priority}
                for row in rows
                if row["priority"] != priority
            ]
            changed_ids = [item["id"] for item in changed]
            if changed_ids:
                changed_placeholders = ", ".join("?" for _ in changed_ids)
                connection.execute(
                    f"""UPDATE conversations
                    SET priority = ?,
                        sla_due_at = CASE
                            WHEN ? IS NOT NULL AND status != 'resolved'
                                 AND (sla_due_at IS NULL OR sla_due_at > ?)
                            THEN ? ELSE sla_due_at END,
                        updated_at = ?, version = version + 1
                    WHERE tenant_id = ? AND id IN ({changed_placeholders})""",
                    (
                        priority,
                        tightened_due_at,
                        tightened_due_at,
                        tightened_due_at,
                        now,
                        tenant_id,
                        *changed_ids,
                    ),
                )
        if changed:
            self._invalidate_dashboard(tenant_id)
        return changed, len(rows)

    def bulk_modify_labels(
        self,
        tenant_id: str,
        conversation_ids: Sequence[str],
        labels: Sequence[str],
        actor_id: str,
        *,
        add: bool,
    ) -> tuple[list[str], int]:
        normalized = normalize_conversation_labels(labels)
        if not normalized:
            raise ValueError("labels cannot be empty")
        ids = list(dict.fromkeys(conversation_ids))
        if not ids:
            return [], 0
        placeholders = ", ".join("?" for _ in ids)
        now = utc_now()
        changed: list[str] = []
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT id, labels_json FROM conversations
                WHERE tenant_id = ? AND id IN ({placeholders})""",
                (tenant_id, *ids),
            ).fetchall()
            for row in rows:
                conversation_id = str(row["id"])
                current = normalize_conversation_labels(json.loads(row["labels_json"] or "[]"))
                if add:
                    target = normalize_conversation_labels([*current, *normalized])
                else:
                    target = [label for label in current if label not in normalized]
                if target == current:
                    continue
                if add:
                    connection.executemany(
                        """INSERT OR IGNORE INTO conversation_labels
                        (tenant_id, conversation_id, label, created_by, created_at)
                        VALUES (?, ?, ?, ?, ?)""",
                        [
                            (tenant_id, conversation_id, label, actor_id, now)
                            for label in normalized
                        ],
                    )
                else:
                    label_placeholders = ", ".join("?" for _ in normalized)
                    connection.execute(
                        f"""DELETE FROM conversation_labels
                        WHERE tenant_id = ? AND conversation_id = ?
                          AND label IN ({label_placeholders})""",
                        (tenant_id, conversation_id, *normalized),
                    )
                connection.execute(
                    """UPDATE conversations SET labels_json = ?, version = version + 1
                    WHERE tenant_id = ? AND id = ?""",
                    (
                        json.dumps(target, ensure_ascii=False),
                        tenant_id,
                        conversation_id,
                    ),
                )
                changed.append(conversation_id)
        return changed, len(rows)

    # -----------------------------------------------------------------------
    # Conversation summaries (backlog: session intelligent summary)
    # -----------------------------------------------------------------------

    def upsert_conversation_summary(
        self,
        tenant_id: str,
        conversation_id: str,
        kind: str,
        content: str,
        source: str = "rule",
    ) -> dict[str, Any]:
        """Upsert one summary kind for a conversation; returns the stored row."""
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO conversation_summaries
                (tenant_id, conversation_id, kind, content, source, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, conversation_id, kind) DO UPDATE SET
                    content = excluded.content,
                    source = excluded.source,
                    updated_at = excluded.updated_at""",
                (tenant_id, conversation_id, kind, content, source, now, now),
            )
            row = connection.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE tenant_id = ? AND conversation_id = ? AND kind = ?",
                (tenant_id, conversation_id, kind),
            ).fetchone()
        return dict(row) if row else {}

    def get_conversation_summary(
        self, tenant_id: str, conversation_id: str, kind: str
    ) -> dict[str, Any] | None:
        """Return one stored summary kind, or None."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE tenant_id = ? AND conversation_id = ? AND kind = ?",
                (tenant_id, conversation_id, kind),
            ).fetchone()
        return dict(row) if row else None

    def list_conversation_summaries(
        self, tenant_id: str, conversation_id: str
    ) -> list[dict[str, Any]]:
        """Return all stored summaries for a conversation (any kind)."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversation_summaries "
                "WHERE tenant_id = ? AND conversation_id = ? ORDER BY kind",
                (tenant_id, conversation_id),
            ).fetchall()
        return [dict(row) for row in rows]
