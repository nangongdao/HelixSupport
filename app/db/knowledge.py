"""Database knowledge mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
import sqlite3
from collections.abc import Sequence
from typing import Any
from uuid import uuid4

from app.db._util import (
    ASCII_TERM_PATTERN,
    knowledge_search_terms,
    utc_now,
)


class DatabaseKnowledgeMixin:
    @staticmethod
    def _public_knowledge_item(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
        """Return a knowledge row without backend-only ordering metadata."""
        item = dict(row)
        # PostgreSQL maps SQLite's implicit ``rowid`` tiebreaker to a private
        # sequence column. Keep that implementation detail out of caches and
        # API/domain results while retaining it in the SQL ordering path.
        item.pop("seq", None)
        return item

    def search_knowledge(
        self,
        tenant_id: str,
        query: str,
        limit: int = 3,
        language: str | None = None,
    ) -> list[dict[str, Any]]:
        # ROADMAP 18.2b: defensive candidate cap — never let a single search
        # request more rows than the configured cap (FTS early-stops via SQL
        # LIMIT, but an unbounded caller-provided limit must not be honoured).
        limit = max(1, min(limit, self._knowledge_search_candidate_cap))
        query_terms = knowledge_search_terms(query, limit=64)
        if self._fts_enabled and query_terms:
            # ROADMAP 18.2b: short-lived cache keyed by (tenant, knowledge
            # version, normalized query, language, limit). Stale hits cannot
            # survive a knowledge write because the tenant version bumps.
            cache_key = (
                tenant_id,
                self._knowledge_version(tenant_id),
                query.casefold().strip(),
                language or "",
                limit,
            )
            cached = self._knowledge_search_cache.get(cache_key)
            if cached is not None:
                return [dict(item) for item in cached]
            match_query = " OR ".join(f'"{term}"' for term in query_terms)
            try:
                with self.connect() as connection:
                    rows = connection.execute(
                        """SELECT k.*,
                            bm25(knowledge_fts, 0.0, 0.0, 5.0, 1.0, 8.0, 3.0, 4.0)
                                AS fts_rank
                        FROM knowledge_fts
                        JOIN knowledge_articles k
                          ON k.id = knowledge_fts.article_id
                         AND k.tenant_id = knowledge_fts.tenant_id
                        WHERE knowledge_fts MATCH ?
                          AND knowledge_fts.tenant_id = ?
                          AND k.tenant_id = ?
                          AND k.active = 1
                          AND (k.status = 'published' OR k.status IS NULL)
                        ORDER BY CASE WHEN (? IS NULL OR k.language IS NULL
                                              OR k.language = ?) THEN 0 ELSE 1 END,
                                 fts_rank, k.updated_at DESC, k.rowid DESC
                        LIMIT ?""",
                        (match_query, tenant_id, tenant_id, language, language, limit),
                    ).fetchall()
            except sqlite3.OperationalError:
                with self._pool_lock:
                    self._fts_fallbacks += 1
            else:
                with self._pool_lock:
                    self._fts_queries += 1
                result = self._decorate_knowledge_matches(rows, query_terms)
                self._knowledge_search_cache.set(cache_key, [dict(item) for item in result])
                return result
        else:
            with self._pool_lock:
                self._fts_fallbacks += 1
        return self._fallback_search_knowledge(tenant_id, query, limit, language)

    @staticmethod
    def _decorate_knowledge_matches(
        rows: Sequence[sqlite3.Row], query_terms: Sequence[str]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            item = DatabaseKnowledgeMixin._public_knowledge_item(row)
            article_terms = set(
                knowledge_search_terms(
                    f"{item['tags']} {item['title']} {item['category']} {item['content']}"
                )
            )
            tag_terms = set(knowledge_search_terms(str(item["tags"])))
            title_terms = set(knowledge_search_terms(str(item["title"])))
            matched = [term for term in query_terms if term in article_terms]
            score = len(matched)
            score += 2 * sum(term in tag_terms for term in matched)
            score += 2 * sum(term in title_terms for term in matched)
            item["retrieval_score"] = max(1, score)
            item["matched_terms"] = matched
            result.append(item)
        return result

    def _fallback_search_knowledge(
        self, tenant_id: str, query: str, limit: int, language: str | None = None
    ) -> list[dict[str, Any]]:
        normalized = query.casefold()
        ascii_terms = set(ASCII_TERM_PATTERN.findall(normalized))
        cached = self._knowledge_cache.get(tenant_id)
        if cached is None:
            with self.connect() as connection:
                # Full active list — this cache is shared with ``list_knowledge``
                # and must not be truncated by the search candidate cap.
                rows = connection.execute(
                    """SELECT * FROM knowledge_articles
                    WHERE tenant_id = ? AND active = 1
                      AND (status = 'published' OR status IS NULL)
                    ORDER BY updated_at DESC, rowid DESC""",
                    (tenant_id,),
                ).fetchall()
            cached = [self._public_knowledge_item(row) for row in rows]
            self._knowledge_cache.set(tenant_id, cached)
        # ROADMAP 18.2b: candidate cap — the scoring loop reads at most
        # ``candidate_cap`` rows, so a very large knowledge base cannot turn
        # one no-FTS search into a full scan. The shared article cache stays
        # complete for ``list_knowledge``.
        candidates = cached[: self._knowledge_search_candidate_cap]
        scored: list[tuple[int, int, dict[str, Any]]] = []
        for cached_item in candidates:
            item = dict(cached_item)
            tags = [tag.casefold() for tag in item["tags"].split() if tag.strip()]
            matched = [tag for tag in tags if tag in normalized or tag in ascii_terms]
            title_match = item["title"].casefold() in normalized
            score = len(matched) * 3 + (4 if title_match else 0)
            if score:
                item["retrieval_score"] = score
                item["matched_terms"] = matched
                article_language = item.get("language")
                lang_pref = (
                    0
                    if (
                        language is None or article_language is None or article_language == language
                    )
                    else 1
                )
                scored.append((lang_pref, score, item))
        # Backlog (多语言客服): articles in the customer's language (or
        # language-agnostic ones) rank before cross-language matches.
        scored.sort(key=lambda pair: (pair[0], pair[1], pair[2]["updated_at"]), reverse=True)
        return [item for _, _, item in scored[:limit]]

    def list_knowledge(
        self, tenant_id: str, include_inactive: bool = False
    ) -> list[dict[str, Any]]:
        if not include_inactive:
            cached = self._knowledge_cache.get(tenant_id)
            if cached is not None:
                return [dict(item) for item in cached]
        query = "SELECT * FROM knowledge_articles WHERE tenant_id = ?"
        values: list[Any] = [tenant_id]
        if not include_inactive:
            query += " AND active = 1 AND (status = 'published' OR status IS NULL)"
        query += " ORDER BY updated_at DESC, rowid DESC, title"
        with self.connect() as connection:
            rows = connection.execute(query, values).fetchall()
        result = [self._public_knowledge_item(row) for row in rows]
        if not include_inactive:
            self._knowledge_cache.set(tenant_id, result)
        return [dict(item) for item in result]

    def create_knowledge(
        self,
        tenant_id: str,
        title: str,
        content: str,
        tags: list[str],
        category: str,
        source_url: str,
        language: str | None = None,
    ) -> dict[str, Any]:
        article_id = f"kb_{uuid4().hex[:12]}"
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_articles
                (id, tenant_id, title, content, tags, category, source_url, language,
                 active, version, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?)""",
                (
                    article_id,
                    tenant_id,
                    title,
                    content,
                    " ".join(dict.fromkeys(tags)),
                    category,
                    source_url,
                    language,
                    utc_now(),
                ),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_articles WHERE id = ? AND tenant_id = ?",
                (article_id, tenant_id),
            ).fetchone()
        self._invalidate_knowledge(tenant_id)
        return self._public_knowledge_item(row)

    def update_knowledge(
        self,
        tenant_id: str,
        article_id: str,
        fields: dict[str, Any],
    ) -> dict[str, Any] | None:
        allowed = {
            "title",
            "content",
            "tags",
            "category",
            "source_url",
            "language",
            "active",
            "status",
            "reviewed_by",
            "reviewed_at",
        }
        changes = {key: value for key, value in fields.items() if key in allowed}
        if "tags" in changes and isinstance(changes["tags"], list):
            changes["tags"] = " ".join(dict.fromkeys(changes["tags"]))
        if changes:
            changes["updated_at"] = utc_now()
            assignments = ", ".join(f"{key} = ?" for key in changes)
            with self.connect() as connection:
                connection.execute(
                    f"""UPDATE knowledge_articles
                    SET {assignments}, version = version + 1
                    WHERE tenant_id = ? AND id = ?""",
                    [*changes.values(), tenant_id, article_id],
                )
            self._invalidate_knowledge(tenant_id)
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM knowledge_articles WHERE tenant_id = ? AND id = ?",
                (tenant_id, article_id),
            ).fetchone()
        return self._public_knowledge_item(row) if row else None

    def create_knowledge_draft(
        self,
        tenant_id: str,
        title: str,
        content: str,
        tags: list[str],
        category: str,
        source_url: str,
        actor_id: str,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Create a knowledge article in ``draft`` status (Phase 21.3).

        Drafts are not retrievable by ``search_knowledge``/``list_knowledge``
        (which filter on ``status='published'`` once a draft is created via
        this path); they require explicit approval before going live.
        """
        article_id = f"kb_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO knowledge_articles
                (id, tenant_id, title, content, tags, category, source_url, language,
                 active, version, updated_at, status, reviewed_by, reviewed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, 1, ?, 'draft', NULL, NULL)""",
                (
                    article_id,
                    tenant_id,
                    title,
                    content,
                    " ".join(dict.fromkeys(tags)),
                    category,
                    source_url,
                    language,
                    now,
                ),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_articles WHERE id = ? AND tenant_id = ?",
                (article_id, tenant_id),
            ).fetchone()
        self._invalidate_knowledge(tenant_id)
        return self._public_knowledge_item(row)

    def review_knowledge(
        self,
        tenant_id: str,
        article_id: str,
        action: str,
        actor_id: str,
    ) -> dict[str, Any] | None:
        """Approve (publish) or reject (retire) a pending knowledge article.

        ``action`` is ``publish`` (sets ``status='published'``, ``active=1``)
        or ``retire`` (sets ``status='retired'``, ``active=0``).  Only
        ``draft`` and ``pending_review`` articles can be reviewed -- publishing
        an already-published article is rejected so the approval step cannot
        be bypassed.
        """
        from app.orchestrator import InvalidTransitionError

        if action not in {"publish", "retire"}:
            raise ValueError("action must be 'publish' or 'retire'")
        now = utc_now()
        with self.connect() as connection:
            current = connection.execute(
                "SELECT status FROM knowledge_articles WHERE id = ? AND tenant_id = ?",
                (article_id, tenant_id),
            ).fetchone()
            if current is None:
                return None
            current_status = current["status"] or "published"
            if action == "publish" and current_status not in {"draft", "pending_review"}:
                raise InvalidTransitionError(
                    "only draft or pending_review articles can be published"
                )
            if action == "retire" and current_status == "retired":
                raise InvalidTransitionError("article is already retired")
            new_status = "published" if action == "publish" else "retired"
            new_active = 1 if action == "publish" else 0
            connection.execute(
                """UPDATE knowledge_articles
                SET status = ?, active = ?, reviewed_by = ?, reviewed_at = ?,
                    updated_at = ?, version = version + 1
                WHERE id = ? AND tenant_id = ?""",
                (new_status, new_active, actor_id, now, now, article_id, tenant_id),
            )
            row = connection.execute(
                "SELECT * FROM knowledge_articles WHERE id = ? AND tenant_id = ?",
                (article_id, tenant_id),
            ).fetchone()
        self._invalidate_knowledge(tenant_id)
        return self._public_knowledge_item(row) if row else None

    def list_knowledge_gaps(
        self,
        tenant_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Return conversations with negative feedback and no citations.

        Used by the supervisor quality panel (Phase 21.2) to surface where
        the knowledge base is failing customers.  Each row carries the
        conversation id, customer name, intent, and the rated assistant
        message id so a draft can be generated from it.
        """
        limit = max(1, min(int(limit), 100))
        with self.connect() as connection:
            rows = connection.execute(
                """SELECT DISTINCT
                       c.id AS conversation_id,
                       c.customer_name,
                       c.intent,
                       f.message_id,
                       m.content AS assistant_content,
                       m.metadata_json
                   FROM feedback f
                   JOIN messages m
                     ON m.id = f.message_id AND m.tenant_id = f.tenant_id
                   JOIN conversations c
                     ON c.id = f.conversation_id AND c.tenant_id = f.tenant_id
                   WHERE f.tenant_id = ?
                     AND f.rating = -1
                     AND (
                       json_valid(m.metadata_json) = 0
                       OR COALESCE(
                            json_type(m.metadata_json, '$.citations'),
                            'null'
                          ) IN ('null', 'text', 'integer', 'real', 'boolean')
                       OR json_array_length(m.metadata_json, '$.citations') = 0
                     )
                   ORDER BY f.updated_at DESC
                   LIMIT ?""",
                (tenant_id, limit),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            try:
                metadata = json.loads(item.pop("metadata_json") or "{}")
            except (TypeError, ValueError):
                metadata = {}
            item["prompt_version"] = metadata.get("prompt_version")
            result.append(item)
        return result

    def get_order_for_customer(
        self, tenant_id: str, customer_ref: str, order_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                """SELECT * FROM orders
                WHERE tenant_id = ?
                  AND customer_ref = ? COLLATE NOCASE
                  AND id = ? COLLATE NOCASE""",
                (tenant_id, customer_ref, order_id),
            ).fetchone()
        return dict(row) if row else None

    def get_customer_profile(self, tenant_id: str, customer_ref: str) -> dict[str, Any] | None:
        """Resolve a customer reference to a profile, if they have any orders."""
        with self.connect() as connection:
            row = connection.execute(
                """SELECT customer_ref, customer_name FROM orders
                WHERE tenant_id = ? AND customer_ref = ? COLLATE NOCASE
                LIMIT 1""",
                (tenant_id, customer_ref),
            ).fetchone()
        return dict(row) if row else None

    def list_canned_responses(
        self,
        tenant_id: str,
        *,
        include_inactive: bool = False,
        search: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if not include_inactive:
            clauses.append("active = 1")
        if search:
            needle = f"%{search.strip()}%"
            clauses.append("(title LIKE ? OR body LIKE ? OR COALESCE(shortcut, '') LIKE ?)")
            values.extend([needle, needle, needle])
        where = " AND ".join(clauses)
        values.append(max(1, min(limit, 200)))
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT * FROM canned_responses WHERE {where}
                ORDER BY usage_count DESC, updated_at DESC, id DESC LIMIT ?""",
                values,
            ).fetchall()
        return [self._canned_response_row(row) for row in rows]

    def get_canned_response(self, tenant_id: str, response_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM canned_responses WHERE tenant_id = ? AND id = ?",
                (tenant_id, response_id),
            ).fetchone()
        return self._canned_response_row(row) if row else None

    def create_canned_response(
        self,
        tenant_id: str,
        *,
        title: str,
        body: str,
        shortcut: str | None,
        tags: Sequence[str],
        actor_id: str,
    ) -> dict[str, Any]:
        response_id = f"macro_{uuid4().hex[:12]}"
        now = utc_now()
        normalized_shortcut = shortcut.strip().casefold() if shortcut else None
        if normalized_shortcut == "":
            normalized_shortcut = None
        tags_json = json.dumps(list(tags), ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO canned_responses
                (id, tenant_id, title, body, shortcut, tags_json, active, usage_count,
                 created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?)""",
                (
                    response_id,
                    tenant_id,
                    title,
                    body,
                    normalized_shortcut,
                    tags_json,
                    actor_id,
                    actor_id,
                    now,
                    now,
                ),
            )
        return self.get_canned_response(tenant_id, response_id) or {}

    def update_canned_response(
        self,
        tenant_id: str,
        response_id: str,
        changes: dict[str, Any],
        actor_id: str,
    ) -> dict[str, Any] | None:
        if not changes:
            return self.get_canned_response(tenant_id, response_id)
        assignments: list[str] = []
        values: list[Any] = []
        if "title" in changes and changes["title"] is not None:
            assignments.append("title = ?")
            values.append(changes["title"])
        if "body" in changes and changes["body"] is not None:
            assignments.append("body = ?")
            values.append(changes["body"])
        if "shortcut" in changes:
            shortcut = changes["shortcut"]
            normalized = shortcut.strip().casefold() if isinstance(shortcut, str) else None
            assignments.append("shortcut = ?")
            values.append(normalized or None)
        if "tags" in changes and changes["tags"] is not None:
            assignments.append("tags_json = ?")
            values.append(json.dumps(list(changes["tags"]), ensure_ascii=False))
        if "active" in changes and changes["active"] is not None:
            assignments.append("active = ?")
            values.append(int(bool(changes["active"])))
        if not assignments:
            return self.get_canned_response(tenant_id, response_id)
        assignments.extend(["updated_by = ?", "updated_at = ?"])
        values.extend([actor_id, utc_now(), tenant_id, response_id])
        with self.connect() as connection:
            cursor = connection.execute(
                f"""UPDATE canned_responses SET {", ".join(assignments)}
                WHERE tenant_id = ? AND id = ?""",
                values,
            )
            if cursor.rowcount != 1:
                return None
        return self.get_canned_response(tenant_id, response_id)

    def record_canned_response_usage(
        self, tenant_id: str, response_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            cursor = connection.execute(
                """UPDATE canned_responses
                SET usage_count = usage_count + 1, updated_at = updated_at
                WHERE tenant_id = ? AND id = ? AND active = 1""",
                (tenant_id, response_id),
            )
            if cursor.rowcount != 1:
                return None
        return self.get_canned_response(tenant_id, response_id)

    @staticmethod
    def _canned_response_row(row: sqlite3.Row | dict[str, Any] | None) -> dict[str, Any]:
        if row is None:
            return {}
        item = dict(row)
        item["tags"] = json.loads(item.pop("tags_json", "[]") or "[]")
        item["active"] = bool(item.get("active", 1))
        return item
