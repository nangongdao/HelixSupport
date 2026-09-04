"""Database conversations_query mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import sqlite3
from collections.abc import Sequence
from typing import Any

from app.db._util import (
    knowledge_search_terms,
    utc_now,
)

# PostgreSQL queue-search fast path: how many newest conversations to probe for
# the message term before giving up to the aggregated CTE.  The window is a
# hard bound so a pathological full-match term costs at most one ordered index
# scan plus ``window`` per-conversation probes (see ``_query_conversations``).
_PG_SEARCH_WINDOW = 1024


class DatabaseConversationsQueryMixin:
    def list_conversations(
        self,
        tenant_id: str,
        status: str | None = None,
        search: str | None = None,
        label: str | None = None,
        priority: str | None = None,
        channel: str | None = None,
        assigned_to: str | None = None,
        claimed_by: str | None = None,
        unassigned: bool = False,
        unclaimed: bool = False,
        sla_breached: bool | None = None,
        needs_response: bool | None = None,
        sort: str = "priority",
        limit: int = 100,
        offset: int = 0,
        cursor: tuple[str, int | None, str | None, str, str] | None = None,
        archived: bool = False,
    ) -> list[dict[str, Any]]:
        limit = max(1, limit)
        offset = max(0, offset)
        if sort not in {"priority", "waiting", "sla", "updated"}:
            raise ValueError("Unsupported conversation sort")
        search_terms = knowledge_search_terms(search, limit=64) if search else []
        # Archived rows were deliberately evicted from the message FTS mirror
        # to bound its volume, so an archive search falls back to LIKE and FTS
        # is only ever attempted against the hot table.
        use_fts = bool(not archived and search and search_terms and self._message_fts_enabled)
        table = "conversations_archive" if archived else "conversations"
        inbox_messages_table = "messages_archive" if archived else "messages"
        label_table = "conversation_labels_archive" if archived else "conversation_labels"
        try:
            rows = self._query_conversations(
                tenant_id,
                status=status,
                search=search,
                label=label,
                priority=priority,
                channel=channel,
                assigned_to=assigned_to,
                claimed_by=claimed_by,
                unassigned=unassigned,
                unclaimed=unclaimed,
                sla_breached=sla_breached,
                needs_response=needs_response,
                sort=sort,
                limit=limit,
                offset=offset,
                cursor=cursor,
                search_terms=search_terms,
                use_fts=use_fts,
                table=table,
                inbox_messages_table=inbox_messages_table,
                label_table=label_table,
            )
        except sqlite3.OperationalError:
            if not use_fts:
                raise
            with self._pool_lock:
                self._message_fts_fallbacks += 1
            rows = self._query_conversations(
                tenant_id,
                status=status,
                search=search,
                label=label,
                priority=priority,
                channel=channel,
                assigned_to=assigned_to,
                claimed_by=claimed_by,
                unassigned=unassigned,
                unclaimed=unclaimed,
                sla_breached=sla_breached,
                needs_response=needs_response,
                sort=sort,
                limit=limit,
                offset=offset,
                cursor=cursor,
                search_terms=search_terms,
                use_fts=False,
                table="conversations",
                inbox_messages_table="messages",
                label_table="conversation_labels",
            )
        else:
            if search:
                with self._pool_lock:
                    if use_fts:
                        self._message_fts_queries += 1
                    else:
                        self._message_fts_fallbacks += 1
        return [dict(row) for row in rows]

    def conversation_watermark(self, tenant_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(updated_at), '') AS watermark FROM conversations WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
        return str(row["watermark"] if row else "")

    def _conversation_filter_clauses(
        self,
        now: str,
        tenant_id: str,
        *,
        status: str | None,
        priority: str | None,
        channel: str | None,
        assigned_to: str | None,
        claimed_by: str | None,
        unassigned: bool,
        unclaimed: bool,
        sla_breached: bool | None,
        needs_response: bool | None,
        label: str | None,
        cursor: tuple[str, int | None, str | None, str, str] | None,
        sort: str,
        label_table: str,
    ) -> tuple[list[str], list[Any]]:
        """Build the non-search WHERE clauses shared by every query shape.

        Returns ``(clauses, query_values)`` where the first clause is always
        ``c.tenant_id = ?`` with the tenant as the first bound value.  The
        message-search predicate is composed separately so the PostgreSQL
        windowed fast path can probe the newest conversations and the
        aggregated CTE fallback can run over the whole tenant.
        """
        clauses = ["c.tenant_id = ?"]
        query_values: list[Any] = [tenant_id]
        if status:
            clauses.append("c.status = ?")
            query_values.append(status)
        if priority:
            clauses.append("c.priority = ?")
            query_values.append(priority)
        if channel:
            clauses.append("c.channel = ?")
            query_values.append(channel)
        if assigned_to:
            clauses.append("c.assigned_agent = ?")
            query_values.append(assigned_to)
        if unassigned:
            clauses.append(
                "(c.assigned_agent IS NULL OR c.assigned_agent IN "
                "('knowledge', 'order', 'escalation', 'policy', 'triage', 'quality'))"
            )
        if claimed_by:
            clauses.append(
                "c.claimed_by = ? AND c.claim_expires_at IS NOT NULL AND c.claim_expires_at > ?"
            )
            query_values.extend([claimed_by, now])
        if unclaimed:
            clauses.append(
                "(c.claimed_by IS NULL OR c.claim_expires_at IS NULL OR c.claim_expires_at <= ?)"
            )
            query_values.append(now)
        if sla_breached is True:
            clauses.append(
                "c.status != 'resolved' AND c.sla_due_at IS NOT NULL AND c.sla_due_at < ?"
            )
            query_values.append(now)
        elif sla_breached is False:
            clauses.append("(c.status = 'resolved' OR c.sla_due_at IS NULL OR c.sla_due_at >= ?)")
            query_values.append(now)
        if needs_response is not None:
            clauses.append("c.needs_response = ?")
            query_values.append(int(needs_response))
        if label:
            clauses.append(
                f"EXISTS (SELECT 1 FROM {label_table} cl "
                "WHERE cl.tenant_id = c.tenant_id AND cl.conversation_id = c.id "
                "AND cl.label = ?)"
            )
            query_values.append(label.casefold())
        if cursor:
            cursor_sort, priority_rank, sort_key, updated_at, conversation_id = cursor
            if cursor_sort != sort:
                raise ValueError("cursor sort does not match requested sort")
            if sort == "priority":
                rank = "CASE c.priority WHEN 'high' THEN 0 ELSE 1 END"
                clauses.append(
                    f"({rank} > ? OR ({rank} = ? AND c.updated_at < ?) "
                    f"OR ({rank} = ? AND c.updated_at = ? AND c.id < ?))"
                )
                query_values.extend(
                    [
                        priority_rank,
                        priority_rank,
                        updated_at,
                        priority_rank,
                        updated_at,
                        conversation_id,
                    ]
                )
            elif sort == "waiting":
                wait_expr = "COALESCE(c.waiting_since, '9999-12-31T00:00:00+00:00')"
                clauses.append(
                    f"({wait_expr} > ? OR ({wait_expr} = ? AND c.updated_at < ?) "
                    f"OR ({wait_expr} = ? AND c.updated_at = ? AND c.id < ?))"
                )
                query_values.extend(
                    [sort_key, sort_key, updated_at, sort_key, updated_at, conversation_id]
                )
            elif sort == "sla":
                sla_expr = "COALESCE(c.sla_due_at, '9999-12-31T00:00:00+00:00')"
                clauses.append(
                    f"({sla_expr} > ? OR ({sla_expr} = ? AND c.updated_at < ?) "
                    f"OR ({sla_expr} = ? AND c.updated_at = ? AND c.id < ?))"
                )
                query_values.extend(
                    [sort_key, sort_key, updated_at, sort_key, updated_at, conversation_id]
                )
            else:
                clauses.append("(c.updated_at < ? OR (c.updated_at = ? AND c.id < ?))")
                query_values.extend([updated_at, updated_at, conversation_id])
        return clauses, query_values

    def _query_conversations_windowed(
        self,
        now: str,
        tenant_id: str,
        base_clauses: Sequence[str],
        base_values: Sequence[Any],
        search: str,
        limit: int,
        offset: int,
    ) -> list[sqlite3.Row] | None:
        """PostgreSQL queue-search fast path for ``sort="updated"``.

        The planner cannot use pg_trgm statistics for a bound ``LIKE`` pattern,
        so for a term matching most messages it falls back to a generic plan
        that aggregates the whole message table (CAPACITY 3.2: 627-809ms).
        This shape removes the planner from the decision: it first pulls the
        newest ``offset + limit`` conversations through the ordered index
        ``idx_conversations_tenant_updated_id`` (a sub-ms scan — no estimate
        involved), then applies the search predicate with a per-conversation
        probe served by ``idx_messages_page_seq``.  A term matching most
        messages satisfies the page inside the window and the query returns
        exactly ``limit`` rows, which is provably the global page (every match
        outside the window sorts below every window row).  A term too rare to
        fill the window returns fewer rows and the caller falls through to the
        aggregated CTE, which the pg_trgm GIN index makes fast for selective
        terms.  Trade-off: a rare term spends up to ``window_k`` per-conversation
        probes before the fallback reruns the CTE — bounded by
        ``_PG_SEARCH_WINDOW``, and worth it because the common (dense) case
        avoids the planner's estimate entirely.

        Returns the completed page, or ``None`` when the window did not fill
        ``limit`` rows (caller must fall through).
        """
        needle = f"%{search}%"
        search_clause = (
            "(c.customer_name LIKE ? OR c.id LIKE ? OR c.customer_ref LIKE ? OR EXISTS ("
            "SELECT 1 FROM messages m WHERE m.tenant_id = c.tenant_id "
            "AND m.conversation_id = c.id AND m.content LIKE ?))"
        )
        window_k = offset + limit
        inner_where = " AND ".join(base_clauses)
        sql = f"""SELECT c.*,
            CASE c.priority WHEN 'high' THEN 0 ELSE 1 END AS queue_priority_rank,
            CASE WHEN c.status != 'resolved' AND c.sla_due_at IS NOT NULL
                 AND c.sla_due_at < ? THEN 1 ELSE 0 END AS sla_breached,
            CASE WHEN c.claimed_by IS NOT NULL AND c.claim_expires_at IS NOT NULL
                 AND c.claim_expires_at > ? THEN 1 ELSE 0 END AS claim_active
            FROM conversations c
            WHERE c.tenant_id = ? AND {search_clause}
              AND c.id IN (
                SELECT id FROM conversations c WHERE {inner_where}
                ORDER BY updated_at DESC, id DESC LIMIT ?
              )
            ORDER BY c.updated_at DESC, c.id DESC LIMIT ? OFFSET ?"""
        values: list[Any] = [now, now, tenant_id]
        values.extend([needle] * 4)
        values.extend(base_values)
        values.extend([window_k, limit, offset])
        with self.connect() as connection:
            rows = connection.execute(sql, values).fetchall()
        if len(rows) == limit:
            return rows
        return None

    def _query_conversations(
        self,
        tenant_id: str,
        *,
        status: str | None,
        search: str | None,
        label: str | None,
        priority: str | None,
        channel: str | None,
        assigned_to: str | None,
        claimed_by: str | None,
        unassigned: bool,
        unclaimed: bool,
        sla_breached: bool | None,
        needs_response: bool | None,
        sort: str,
        limit: int,
        offset: int,
        cursor: tuple[str, int | None, str | None, str, str] | None,
        search_terms: Sequence[str],
        use_fts: bool,
        table: str = "conversations",
        inbox_messages_table: str = "messages",
        label_table: str = "conversation_labels",
    ) -> list[sqlite3.Row]:
        now = utc_now()
        base_clauses, base_values = self._conversation_filter_clauses(
            now,
            tenant_id,
            status=status,
            priority=priority,
            channel=channel,
            assigned_to=assigned_to,
            claimed_by=claimed_by,
            unassigned=unassigned,
            unclaimed=unclaimed,
            sla_breached=sla_breached,
            needs_response=needs_response,
            label=label,
            cursor=cursor,
            sort=sort,
            label_table=label_table,
        )
        # PostgreSQL queue-search fast path: for the indexed ``updated`` sort
        # (the §18.5 acceptance probe), probe the newest conversations instead
        # of aggregating the message table, so a term matching most messages
        # converges under the 500ms line (see ``_query_conversations_windowed``).
        # Rare terms fall through to the CTE, where the pg_trgm GIN index is
        # fast.  Deep offsets stay on the CTE: the window would have to cover
        # the whole page anyway.  ``table == "conversations"`` makes the
        # non-archived invariant explicit (the windowed SQL only ever touches
        # the hot tables; archive searches always take the CTE/LIKE path).
        if (
            self.backend == "postgresql"
            and search
            and not use_fts
            and sort == "updated"
            and table == "conversations"
            and inbox_messages_table == "messages"
            and offset + limit <= _PG_SEARCH_WINDOW
        ):
            rows = self._query_conversations_windowed(
                now,
                tenant_id,
                base_clauses,
                base_values,
                search,
                limit,
                offset,
            )
            if rows is not None:
                return rows

        cte = ""
        from_clause = f"FROM {table} c"
        values: list[Any] = []
        if use_fts:
            match_query = " OR ".join(f'"{term}"' for term in search_terms)
            cte = """WITH message_matches AS (
                SELECT DISTINCT tenant_id, conversation_id FROM message_fts
                WHERE message_fts MATCH ? AND tenant_id = ?
            ) """
            from_clause += " LEFT JOIN message_matches mm ON mm.tenant_id = c.tenant_id "
            from_clause += "AND mm.conversation_id = c.id"
            values.extend([match_query, tenant_id])
        elif search:
            # PostgreSQL has no FTS mirror (and archive searches are evicted
            # from the hot mirror), so the message predicate has to probe the
            # message table directly.  A per-row EXISTS scan probes every
            # conversation's rows (PG full-tenant: ~2.4s worst case).  Pre-
            # aggregating the matched conversations into a CTE lets the planner
            # weight the pg_trgm GIN index on ``messages.content`` (installed by
            # app.pg_compat.install_trgm_search) for the LIKE predicate
            # (CAPACITY 3.2 PG worst ~0.7s, selective ~75ms).
            cte = f"""WITH message_matches AS (
                SELECT tenant_id, conversation_id FROM {inbox_messages_table}
                WHERE tenant_id = ? AND content LIKE ?
                GROUP BY tenant_id, conversation_id
            ) """
            from_clause += " LEFT JOIN message_matches mm ON mm.tenant_id = c.tenant_id "
            from_clause += "AND mm.conversation_id = c.id"
            values.extend([tenant_id, f"%{search}%"])
        clauses = base_clauses
        query_values = list(base_values)
        if search:
            needle = f"%{search}%"
            clauses.append(
                "(c.customer_name LIKE ? OR c.id LIKE ? OR c.customer_ref LIKE ? "
                "OR mm.conversation_id IS NOT NULL)"
            )
            query_values.extend([needle, needle, needle])
        where = " AND ".join(clauses)
        if sort == "waiting":
            order_by = (
                "COALESCE(c.waiting_since, '9999-12-31T00:00:00+00:00') ASC, "
                "c.updated_at DESC, c.id DESC"
            )
        elif sort == "sla":
            order_by = (
                "COALESCE(c.sla_due_at, '9999-12-31T00:00:00+00:00') ASC, "
                "c.updated_at DESC, c.id DESC"
            )
        elif sort == "updated":
            order_by = "c.updated_at DESC, c.id DESC"
        else:
            order_by = "CASE c.priority WHEN 'high' THEN 0 ELSE 1 END, c.updated_at DESC, c.id DESC"
        query = f"""{cte}SELECT c.*,
            CASE c.priority WHEN 'high' THEN 0 ELSE 1 END AS queue_priority_rank,
            CASE WHEN c.status != 'resolved' AND c.sla_due_at IS NOT NULL
                 AND c.sla_due_at < ? THEN 1 ELSE 0 END AS sla_breached,
            CASE WHEN c.claimed_by IS NOT NULL AND c.claim_expires_at IS NOT NULL
                 AND c.claim_expires_at > ? THEN 1 ELSE 0 END AS claim_active
            {from_clause} WHERE {where}
            ORDER BY {order_by} LIMIT ? OFFSET ?"""
        values.extend([now, now, *query_values, limit, offset])
        with self.connect() as connection:
            return connection.execute(query, values).fetchall()
