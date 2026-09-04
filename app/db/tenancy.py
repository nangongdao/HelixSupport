"""Database tenancy mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false
import json
import secrets
from typing import Any
from uuid import uuid4

from app.db._util import (
    utc_now,
)


class DatabaseTenancyMixin:
    def ensure_tenant(self, tenant_id: str, name: str | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
                (tenant_id, name or tenant_id, utc_now()),
            )
        with self._pool_lock:
            self._tenant_exists_cache.add(tenant_id)

    def set_tenant_model_policy(
        self,
        tenant_id: str,
        allowed_models: list[str] | None,
        daily_turn_budget: int | None,
    ) -> None:
        """Set the per-tenant model policy (Phase 19.4).

        ``allowed_models`` is the allow-list of model refs; ``None`` means no
        restriction. ``daily_turn_budget`` is the per-day turn cap; ``None``
        means unlimited (the default). Raises ``LookupError`` if the tenant
        does not exist so the API can return 404.
        """
        models_json = json.dumps(allowed_models) if allowed_models else None
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tenants SET allowed_models_json = ?, daily_turn_budget = ? WHERE id = ?",
                (models_json, daily_turn_budget, tenant_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Tenant not found")

    def get_tenant_model_policy(self, tenant_id: str) -> dict[str, Any]:
        """Read the per-tenant model policy (Phase 19.4)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT allowed_models_json, daily_turn_budget FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Tenant not found")
        models = json.loads(row["allowed_models_json"]) if row["allowed_models_json"] else None
        return {
            "allowed_models": models,
            "daily_turn_budget": row["daily_turn_budget"],
        }

    def increment_tenant_usage(self, tenant_id: str, date_str: str) -> int:
        """Increment and return the per-tenant daily turn count (Phase 19.4)."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tenant_usage_daily(tenant_id, date, turn_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(tenant_id, date) DO UPDATE SET turn_count = turn_count + 1",
                (tenant_id, date_str),
            )
            row = connection.execute(
                "SELECT turn_count FROM tenant_usage_daily WHERE tenant_id = ? AND date = ?",
                (tenant_id, date_str),
            ).fetchone()
        return int(row["turn_count"]) if row else 0

    def increment_tenant_usage_conversations(self, tenant_id: str, date_str: str) -> int:
        """Increment the per-tenant daily conversation count (Phase 22.4).

        Called once per created conversation so ``GET /api/admin/usage`` can
        reconcile billing data against the audit trail.
        """
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tenant_usage_daily(tenant_id, date, conversation_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(tenant_id, date) DO UPDATE SET "
                "conversation_count = conversation_count + 1",
                (tenant_id, date_str),
            )
            row = connection.execute(
                "SELECT conversation_count FROM tenant_usage_daily "
                "WHERE tenant_id = ? AND date = ?",
                (tenant_id, date_str),
            ).fetchone()
        return int(row["conversation_count"]) if row else 0

    def increment_tenant_usage_messages(self, tenant_id: str, date_str: str) -> int:
        """Increment the per-tenant daily message count (Phase 22.4)."""
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO tenant_usage_daily(tenant_id, date, message_count) "
                "VALUES (?, ?, 1) "
                "ON CONFLICT(tenant_id, date) DO UPDATE SET "
                "message_count = message_count + 1",
                (tenant_id, date_str),
            )
            row = connection.execute(
                "SELECT message_count FROM tenant_usage_daily WHERE tenant_id = ? AND date = ?",
                (tenant_id, date_str),
            ).fetchone()
        return int(row["message_count"]) if row else 0

    def list_tenant_usage(
        self,
        tenant_id: str,
        *,
        since: str | None = None,
        until: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Raw daily usage rows for billing export (Phase 22.4).

        Columns: tenant_id, date, turn_count, conversation_count,
        message_count. Filtered by optional date range and bounded by limit.
        """
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if since:
            clauses.append("date >= ?")
            values.append(since)
        if until:
            clauses.append("date <= ?")
            values.append(until)
        limit = max(1, min(int(limit), 500))
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT tenant_id, date, turn_count, conversation_count, message_count "
                f"FROM tenant_usage_daily WHERE {' AND '.join(clauses)} "
                "ORDER BY date DESC LIMIT ?",
                (*values, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_tenant_daily_usage(self, tenant_id: str, date_str: str) -> int:
        """Read the per-tenant daily turn count (Phase 19.4)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT turn_count FROM tenant_usage_daily WHERE tenant_id = ? AND date = ?",
                (tenant_id, date_str),
            ).fetchone()
        return int(row["turn_count"]) if row else 0

    def tenant_exists(self, tenant_id: str) -> bool:
        with self._pool_lock:
            if tenant_id in self._tenant_exists_cache:
                return True
        with self.connect() as connection:
            row = connection.execute("SELECT 1 FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
        exists = row is not None
        if exists:
            with self._pool_lock:
                self._tenant_exists_cache.add(tenant_id)
        return exists

    def list_tenants(self) -> list[str]:
        """Return every provisioned tenant id (Phase 43.2 key-rotation sweep)."""
        with self.connect() as connection:
            rows = connection.execute("SELECT id FROM tenants ORDER BY id").fetchall()
        return [str(row["id"]) for row in rows]

    def seed_demo(self) -> None:
        now = utc_now()
        self.ensure_tenant("demo", "Northstar Retail")
        with self.connect() as connection:
            articles = [
                (
                    "kb-shipping",
                    "配送时效",
                    "现货订单付款后 24 小时内出库。标准配送通常需要 2-4 个工作日；偏远地区可能延长 1-2 个工作日。",
                    "配送 物流 时效 shipping delivery 多久",
                    "shipping",
                    "/kb/shipping",
                ),
                (
                    "kb-return",
                    "退换货政策",
                    "商品签收后 7 天内可申请无理由退货。商品须保持未使用且包装完整。质量问题由平台承担退回运费。退款必须由人工客服审核。",
                    "退货 退款 换货 return refund",
                    "returns",
                    "/kb/returns",
                ),
                (
                    "kb-warranty",
                    "保修服务",
                    "电子产品享受 12 个月有限保修。人为损坏、进水及未经授权拆修不在保修范围内。",
                    "保修 维修 warranty repair",
                    "warranty",
                    "/kb/warranty",
                ),
            ]
            for article_id, title, content, tags, category, url in articles:
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_articles
                    (id, tenant_id, title, content, tags, category, source_url, updated_at)
                    VALUES (?, 'demo', ?, ?, ?, ?, ?, ?)""",
                    (article_id, title, content, tags, category, url, now),
                )
            orders = [
                (
                    "ORD-10482",
                    "CUST-1001",
                    "林嘉",
                    "运输中",
                    "¥629.00",
                    "2026-07-20",
                    "SF1380010482",
                ),
                (
                    "ORD-10931",
                    "CUST-1002",
                    "周然",
                    "已出库",
                    "¥189.00",
                    "2026-07-21",
                    "YT202610931",
                ),
            ]
            for order in orders:
                connection.execute(
                    """INSERT OR IGNORE INTO orders
                    (id, tenant_id, customer_ref, customer_name, status, amount, eta, tracking_code)
                    VALUES (?, 'demo', ?, ?, ?, ?, ?, ?)""",
                    order,
                )
                connection.execute(
                    "UPDATE orders SET customer_ref = ? WHERE tenant_id = 'demo' AND id = ?",
                    (order[1], order[0]),
                )
            macros = [
                (
                    "macro_greeting",
                    "开场问候",
                    "您好，我是人工客服，已接手您的会话，请稍候。",
                    "greeting",
                    '["欢迎","接入"]',
                ),
                (
                    "macro_shipping",
                    "配送说明",
                    "标准配送通常需要 2-4 个工作日，偏远地区可能再延长 1-2 天。如需查单请提供订单号。",
                    "shipping",
                    '["配送","物流"]',
                ),
                (
                    "macro_wait",
                    "请稍候",
                    "正在为您核实信息，请稍等片刻。",
                    "wait",
                    '["等待"]',
                ),
            ]
            for macro_id, title, body, shortcut, tags_json in macros:
                connection.execute(
                    """INSERT OR IGNORE INTO canned_responses
                    (id, tenant_id, title, body, shortcut, tags_json, active, usage_count,
                     created_by, updated_by, created_at, updated_at)
                    VALUES (?, 'demo', ?, ?, ?, ?, 1, 0, 'system', 'system', ?, ?)""",
                    (macro_id, title, body, shortcut, tags_json, now, now),
                )

    def provision_tenant(
        self,
        tenant_id: str,
        name: str,
        actor_id: str,
        *,
        allowed_models: list[str] | None = None,
        daily_turn_budget: int | None = None,
        conversation_quota: int | None = None,
        storage_quota_bytes: int | None = None,
        region: str | None = None,
    ) -> dict[str, Any]:
        """Idempotently provision a tenant with default policy (Phase 22.1).

        Creates the tenant row (if absent) and seeds baseline knowledge
        articles so the new tenant is immediately serviceable. Re-running
        with the same id is a no-op for the tenant row but updates quota
        columns, so provisioning is idempotent. ``region`` fixes data
        residency at creation time (ROADMAP 43.4); like quota fields it is
        applied on both calls, but only when explicitly given — an absent
        region never overwrites a previously pinned value.
        """
        now = utc_now()
        models_json = json.dumps(allowed_models) if allowed_models else None
        with self.connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tenants(id, name, created_at) VALUES (?, ?, ?)",
                (tenant_id, name, now),
            )
            connection.execute(
                """UPDATE tenants
                SET allowed_models_json = COALESCE(?, allowed_models_json),
                    daily_turn_budget = COALESCE(?, daily_turn_budget),
                    conversation_quota = COALESCE(?, conversation_quota),
                    storage_quota_bytes = COALESCE(?, storage_quota_bytes),
                    region = COALESCE(?, region)
                WHERE id = ?""",
                (
                    models_json,
                    daily_turn_budget,
                    conversation_quota,
                    storage_quota_bytes,
                    region,
                    tenant_id,
                ),
            )
        with self._pool_lock:
            self._tenant_exists_cache.add(tenant_id)
        self._seed_tenant_knowledge(tenant_id)
        quota = self.get_tenant_quota(tenant_id)
        self.audit(
            tenant_id,
            None,
            actor_id,
            "tenant.provisioned",
            {"name": name, "region": quota["region"]},
        )
        return quota

    def get_tenant_quota(self, tenant_id: str) -> dict[str, Any]:
        """Read a tenant's quota and model policy (Phase 22.4)."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, allowed_models_json, daily_turn_budget, "
                "conversation_quota, storage_quota_bytes, created_at, region "
                "FROM tenants WHERE id = ?",
                (tenant_id,),
            ).fetchone()
        if row is None:
            raise LookupError("Tenant not found")
        models = json.loads(row["allowed_models_json"]) if row["allowed_models_json"] else None
        return {
            "tenant_id": row["id"],
            "name": row["name"],
            "allowed_models": models,
            "daily_turn_budget": row["daily_turn_budget"],
            "conversation_quota": row["conversation_quota"],
            "storage_quota_bytes": row["storage_quota_bytes"],
            "created_at": row["created_at"],
            # Residency fixed at creation time; ``local`` is the pre-43.4
            # default so legacy tenants read back without a backfill.
            "region": row["region"] or "local",
        }

    def set_tenant_quota(
        self,
        tenant_id: str,
        *,
        conversation_quota: int | None = None,
        storage_quota_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Update quota limits for a tenant (Phase 22.4). None = unchanged."""
        sets: list[str] = []
        values: list[Any] = []
        if conversation_quota is not None:
            sets.append("conversation_quota = ?")
            values.append(conversation_quota)
        if storage_quota_bytes is not None:
            sets.append("storage_quota_bytes = ?")
            values.append(storage_quota_bytes)
        if not sets:
            return self.get_tenant_quota(tenant_id)
        values.append(tenant_id)
        with self.connect() as connection:
            cursor = connection.execute(
                f"UPDATE tenants SET {', '.join(sets)} WHERE id = ?", values
            )
            if cursor.rowcount == 0:
                raise LookupError("Tenant not found")
        return self.get_tenant_quota(tenant_id)

    def _seed_tenant_knowledge(self, tenant_id: str) -> None:
        """Seed a new tenant with the standard knowledge articles (22.1).

        Idempotent: articles are keyed by id and inserted with
        ``INSERT OR IGNORE``. Only runs for tenants that have none, so a
        tenant that later removes an article keeps it removed.
        """
        now = utc_now()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT COUNT(*) AS n FROM knowledge_articles WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()
            if existing and existing["n"] > 0:
                return
            articles = [
                (
                    f"kb-{tenant_id}-shipping",
                    "配送时效",
                    "现货订单付款后 24 小时内出库。标准配送通常需要 2-4 个工作日；偏远地区可能延长 1-2 个工作日。",
                    "配送 物流 时效 shipping delivery 多久",
                    "shipping",
                    f"/kb/{tenant_id}/shipping",
                ),
                (
                    f"kb-{tenant_id}-return",
                    "退换货政策",
                    "商品签收后 7 天内可申请无理由退货。商品须保持未使用且包装完整。质量问题由平台承担退回运费。退款必须由人工客服审核。",
                    "退货 退款 换货 return refund",
                    "returns",
                    f"/kb/{tenant_id}/returns",
                ),
            ]
            for article_id, title, content, tags, category, source_url in articles:
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_articles
                    (id, tenant_id, title, content, tags, category, source_url,
                     active, version, updated_at, status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 1, 1, ?, 'published')""",
                    (article_id, tenant_id, title, content, tags, category, source_url, now),
                )

    def invite_member(
        self,
        tenant_id: str,
        actor_id: str,
        role: str,
        invited_by: str,
    ) -> dict[str, Any]:
        """Create a tenant member in ``invited`` status (Phase 22.2).

        Idempotent per ``(tenant_id, actor_id)``: re-inviting an existing
        member updates role and resets status to ``invited``. Raises
        ``LookupError`` if the tenant does not exist.
        """
        if not self.tenant_exists(tenant_id):
            raise LookupError("Tenant not found")
        now = utc_now()
        member_id = f"mem_{uuid4().hex[:12]}"
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO tenant_members
                (id, tenant_id, actor_id, role, status, invited_by, invited_at,
                 updated_at)
                VALUES (?, ?, ?, ?, 'invited', ?, ?, ?)
                ON CONFLICT(tenant_id, actor_id) DO UPDATE SET
                    role = excluded.role,
                    status = 'invited',
                    invited_by = excluded.invited_by,
                    invited_at = excluded.invited_at,
                    updated_at = excluded.updated_at""",
                (member_id, tenant_id, actor_id, role, invited_by, now, now),
            )
            row = connection.execute(
                "SELECT * FROM tenant_members WHERE tenant_id = ? AND actor_id = ?",
                (tenant_id, actor_id),
            ).fetchone()
        self.audit(
            tenant_id,
            None,
            invited_by,
            "member.invited",
            {"actor_id": actor_id, "role": role},
        )
        return dict(row)

    def list_members(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, tenant_id, actor_id, role, status, invited_by, "
                "invited_at, updated_at, last_login_at "
                "FROM tenant_members WHERE tenant_id = ? ORDER BY actor_id",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_collaborators(self, tenant_id: str) -> list[dict[str, Any]]:
        """Tenant actors eligible for @-mention autocomplete (backlog M18).

        Filters mirror what the mention extractor accepts: only role values the
        roster schema can express, only accounts not deactivated, and only
        actor ids ``_mention_actors`` can actually extract (≤64 chars) — a
        65-80 char id would otherwise be suggested yet silently drop out of
        the mention match.
        """
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT actor_id, role FROM tenant_members "
                "WHERE tenant_id = ? "
                "AND role IN ('admin','supervisor','operator','channel','viewer','auditor') "
                "AND status <> 'deactivated' "
                "AND length(actor_id) <= 64 "
                "ORDER BY actor_id",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_member(self, tenant_id: str, actor_id: str) -> dict[str, Any] | None:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM tenant_members WHERE tenant_id = ? AND actor_id = ?",
                (tenant_id, actor_id),
            ).fetchone()
        return dict(row) if row else None

    def find_active_members_by_actor(self, actor_id: str) -> list[dict[str, Any]]:
        """All active roster rows for an actor across tenants (OIDC single-tenant
        resolution: exactly one row may match)."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tenant_members WHERE actor_id = ? AND status = 'active'",
                (actor_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def update_member_role(
        self, tenant_id: str, actor_id: str, role: str, updated_by: str
    ) -> dict[str, Any]:
        """Change a member's role (Phase 22.2). No-op for unknown members."""
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tenant_members SET role = ?, status = 'active', "
                "updated_at = ? WHERE tenant_id = ? AND actor_id = ?",
                (role, now, tenant_id, actor_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Member not found")
            row = connection.execute(
                "SELECT * FROM tenant_members WHERE tenant_id = ? AND actor_id = ?",
                (tenant_id, actor_id),
            ).fetchone()
        self.audit(
            tenant_id,
            None,
            updated_by,
            "member.role_changed",
            {"actor_id": actor_id, "role": role},
        )
        return dict(row)

    def deactivate_member(
        self, tenant_id: str, actor_id: str, deactivated_by: str
    ) -> dict[str, Any]:
        """Deactivate a member; sessions and audit history are retained (22.2)."""
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE tenant_members SET status = 'deactivated', updated_at = ? "
                "WHERE tenant_id = ? AND actor_id = ?",
                (now, tenant_id, actor_id),
            )
            if cursor.rowcount == 0:
                raise LookupError("Member not found")
            row = connection.execute(
                "SELECT * FROM tenant_members WHERE tenant_id = ? AND actor_id = ?",
                (tenant_id, actor_id),
            ).fetchone()
        self.audit(
            tenant_id,
            None,
            deactivated_by,
            "member.deactivated",
            {"actor_id": actor_id},
        )
        return dict(row)

    def record_member_login(self, tenant_id: str, actor_id: str) -> None:
        """Update last_login_at; tolerates members not yet provisioned."""
        with self.connect() as connection:
            connection.execute(
                "UPDATE tenant_members SET last_login_at = ?, updated_at = ? "
                "WHERE tenant_id = ? AND actor_id = ?",
                (utc_now(), utc_now(), tenant_id, actor_id),
            )

    # -------------------------------------------------------- key revocation (28.2)

    def revoke_api_key(self, credential_id: str, revoked_by: str) -> None:
        """Persist a revoked credential id (Phase 28.2). Idempotent."""
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO revoked_api_keys (credential_id, revoked_at, revoked_by)
                VALUES (?, ?, ?)
                ON CONFLICT(credential_id) DO NOTHING""",
                (credential_id, utc_now(), revoked_by),
            )

    def list_revoked_api_keys(self) -> list[str]:
        """Return all persisted revoked credential ids (startup seed)."""
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT credential_id FROM revoked_api_keys ORDER BY revoked_at"
            ).fetchall()
        return [str(row["credential_id"]) for row in rows]

    # ----------------------------------------------------------- CSAT surveys

    def create_csat_survey(self, tenant_id: str, conversation_id: str, expires_at: str) -> str:
        """Create a one-time CSAT survey token (backlog item)."""
        token = secrets.token_urlsafe(24)
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO csat_surveys
                (token, tenant_id, conversation_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?)""",
                (token, tenant_id, conversation_id, now, expires_at),
            )
        return token

    def get_csat_survey(self, token: str) -> dict[str, Any] | None:
        """Return the survey row, or None when missing/expired/answered."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM csat_surveys WHERE token = ?", (token,)
            ).fetchone()
        if row is None:
            return None
        item = dict(row)
        if item["expires_at"] and item["expires_at"] < utc_now():
            return None
        if item["responded_at"]:
            return None
        return item

    def get_pending_csat_survey(
        self, tenant_id: str, conversation_id: str
    ) -> dict[str, Any] | None:
        """Return the newest unanswered, unexpired survey for a conversation."""
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM csat_surveys "
                "WHERE tenant_id = ? AND conversation_id = ? AND responded_at IS NULL "
                "AND (expires_at IS NULL OR expires_at >= ?) "
                "ORDER BY created_at DESC LIMIT 1",
                (tenant_id, conversation_id, utc_now()),
            ).fetchone()
        return dict(row) if row else None

    def submit_csat_rating(self, token: str, rating: int) -> dict[str, Any] | None:
        """Record a CSAT rating once, atomically; returns the updated row or None.

        The guarded ``UPDATE`` (``responded_at IS NULL`` plus the expiry check)
        turns the "read then write" race into a single atomic transition, so
        two concurrent submissions cannot both succeed or double-reflow into
        feedback.
        """
        now = utc_now()
        with self.connect() as connection:
            cursor = connection.execute(
                "UPDATE csat_surveys SET rating = ?, responded_at = ? "
                "WHERE token = ? AND responded_at IS NULL "
                "AND (expires_at IS NULL OR expires_at >= ?)",
                (rating, now, token, now),
            )
            if cursor.rowcount == 0:
                return None
            row = connection.execute(
                "SELECT * FROM csat_surveys WHERE token = ?", (token,)
            ).fetchone()
        return dict(row) if row else None

    def summarize_csat(self, tenant_id: str, days: int) -> dict[str, Any]:
        """Aggregate answered CSAT surveys for the admin summary card.

        Returns overall totals (sample count, 1–5 mean rating, share of
        positive ≥4 responses) plus a per-day trend (newest ``days`` first)
        for the admin-view「CSAT 评分汇总」card. The overall figures are
        all-history; ``days`` only bounds the trend slice (W1 — the UI labels
        the readouts 累计 to make the two apertures unambiguous).
        """
        days = max(1, min(int(days), 90))  # defensive, S1: direct callers
        with self.connect() as connection:
            overall = connection.execute(
                "SELECT COUNT(*) AS total, AVG(rating) AS avg_rating, "
                "SUM(CASE WHEN rating >= 4 THEN 1 ELSE 0 END) AS positive "
                "FROM csat_surveys WHERE tenant_id = ? AND rating IS NOT NULL",
                (tenant_id,),
            ).fetchone()
            trend_rows = connection.execute(
                "SELECT substr(responded_at, 1, 10) AS date, COUNT(*) AS count, "
                "AVG(rating) AS avg_rating FROM csat_surveys "
                "WHERE tenant_id = ? AND rating IS NOT NULL "
                "GROUP BY substr(responded_at, 1, 10) ORDER BY date DESC LIMIT ?",
                (tenant_id, days),
            ).fetchall()
        total = int(overall["total"] or 0)
        avg_rating = float(overall["avg_rating"] or 0.0)
        positive = int(overall["positive"] or 0)
        return {
            "total": total,
            "avg_rating": round(avg_rating, 2),
            "positive_rate": round(positive / total, 3) if total else 0.0,
            "per_day": [
                {
                    "date": row["date"],
                    "count": int(row["count"]),
                    "avg_rating": round(float(row["avg_rating"] or 0.0), 2),
                }
                for row in trend_rows
            ],
        }

    # ------------------------------------------------------- auto routing (backlog)

    def create_agent_group(
        self, tenant_id: str, name: str, skills: list[str], capacity: int
    ) -> dict[str, Any]:
        """Create an agent group with skill tags and a concurrency cap."""
        group_id = f"grp_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            connection.execute(
                """INSERT INTO agent_groups
                (id, tenant_id, name, skills_json, capacity, created_at)
                VALUES (?, ?, ?, ?, ?, ?)""",
                (group_id, tenant_id, name, json.dumps(skills), max(1, capacity), now),
            )
            row = connection.execute(
                "SELECT * FROM agent_groups WHERE id = ?", (group_id,)
            ).fetchone()
        return _agent_group_row(row)

    def list_agent_groups(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_groups WHERE tenant_id = ? ORDER BY name",
                (tenant_id,),
            ).fetchall()
        return [_agent_group_row(r) for r in rows]

    def delete_agent_group(self, tenant_id: str, group_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM agent_groups WHERE id = ? AND tenant_id = ?",
                (group_id, tenant_id),
            )
            if cursor.rowcount:
                connection.execute(
                    "DELETE FROM agent_group_members WHERE group_id = ?", (group_id,)
                )
                connection.execute("DELETE FROM routing_rules WHERE group_id = ?", (group_id,))
            return cursor.rowcount > 0

    def add_group_agent(
        self, tenant_id: str, group_id: str, actor_id: str
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            group = connection.execute(
                "SELECT id FROM agent_groups WHERE id = ? AND tenant_id = ?",
                (group_id, tenant_id),
            ).fetchone()
            if group is None:
                return None
            connection.execute(
                """INSERT INTO agent_group_members (group_id, tenant_id, actor_id, added_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(group_id, actor_id) DO NOTHING""",
                (group_id, tenant_id, actor_id, utc_now()),
            )
            row = connection.execute(
                "SELECT * FROM agent_group_members WHERE group_id = ? AND actor_id = ?",
                (group_id, actor_id),
            ).fetchone()
        return dict(row) if row else None

    def list_group_agents(self, tenant_id: str, group_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM agent_group_members WHERE group_id = ? AND tenant_id = ? "
                "ORDER BY actor_id",
                (group_id, tenant_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_group_agent(self, tenant_id: str, group_id: str, actor_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM agent_group_members WHERE group_id = ? AND tenant_id = ? "
                "AND actor_id = ?",
                (group_id, tenant_id, actor_id),
            )
            return cursor.rowcount > 0

    def create_routing_rule(
        self,
        tenant_id: str,
        *,
        intent: str | None,
        label: str | None,
        channel: str | None,
        group_id: str,
        priority: int,
    ) -> dict[str, Any]:
        """Create a routing rule (match intent/label/channel -> group)."""
        rule_id = f"rule_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            group = connection.execute(
                "SELECT id FROM agent_groups WHERE id = ? AND tenant_id = ?",
                (group_id, tenant_id),
            ).fetchone()
            if group is None:
                raise LookupError("Agent group not found")
            connection.execute(
                """INSERT INTO routing_rules
                (id, tenant_id, intent, label, channel, group_id, priority, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (rule_id, tenant_id, intent, label, channel, group_id, priority, now),
            )
            row = connection.execute(
                "SELECT * FROM routing_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        return dict(row)

    def list_routing_rules(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM routing_rules WHERE tenant_id = ? ORDER BY priority DESC, id",
                (tenant_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def delete_routing_rule(self, tenant_id: str, rule_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute(
                "DELETE FROM routing_rules WHERE id = ? AND tenant_id = ?",
                (rule_id, tenant_id),
            )
            return cursor.rowcount > 0

    def find_routing_group(
        self, tenant_id: str, *, intent: str | None, label: str | None, channel: str | None
    ) -> str | None:
        """Return the best-matching group id for a conversation (highest priority)."""
        rows = self.list_routing_rules(tenant_id)
        for rule in rows:
            if rule["intent"] and rule["intent"] != intent:
                continue
            if rule["label"] and rule["label"] != label:
                continue
            if rule["channel"] and rule["channel"] != channel:
                continue
            return rule["group_id"]
        return None

    def pick_agent_for_group(self, tenant_id: str, group_id: str) -> str | None:
        """Pick a group agent with spare capacity (round-robin); None if full."""
        members = self.list_group_agents(tenant_id, group_id)
        if not members:
            return None
        with self.connect() as connection:
            group = connection.execute(
                "SELECT capacity FROM agent_groups WHERE id = ? AND tenant_id = ?",
                (group_id, tenant_id),
            ).fetchone()
            capacity = int(group["capacity"]) if group else 1
            # Count currently-assigned conversations per member.
            counts: dict[str, int] = {}
            for member in members:
                row = connection.execute(
                    "SELECT COUNT(*) AS n FROM conversations "
                    "WHERE tenant_id = ? AND assigned_agent = ? "
                    "AND status IN ('open', 'waiting_human', 'human_active')",
                    (tenant_id, member["actor_id"]),
                ).fetchone()
                counts[member["actor_id"]] = int(row["n"])
        # Round-robin: pick the least-loaded member under capacity.
        sorted_members = sorted(members, key=lambda m: counts.get(m["actor_id"], 0))
        for member in sorted_members:
            if counts.get(member["actor_id"], 0) < capacity:
                return member["actor_id"]
        return None

    # ------------------------------------------------------------ SLA policies

    def set_sla_policy(
        self,
        *,
        tenant_id: str | None,
        priority: str | None,
        channel: str | None,
        first_response_minutes: int,
        resolve_minutes: int,
    ) -> dict[str, Any]:
        """Create or update an SLA policy (tenant may be NULL for global default)."""
        policy_id = f"sla_{uuid4().hex[:12]}"
        now = utc_now()
        with self.connect() as connection:
            # Null-safe equality written portably (``IS ?`` is SQLite-only and
            # breaks the PostgreSQL dialect): ``col = ?`` when the param is not
            # NULL, plus an explicit ``IS NULL`` branch when it is.
            existing = connection.execute(
                "SELECT id FROM sla_policies WHERE "
                "(tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL)) AND "
                "(priority = ? OR (priority IS NULL AND ? IS NULL)) AND "
                "(channel = ? OR (channel IS NULL AND ? IS NULL))",
                (tenant_id, tenant_id, priority, priority, channel, channel),
            ).fetchone()
            if existing:
                connection.execute(
                    "UPDATE sla_policies SET first_response_minutes=?, resolve_minutes=?, "
                    "updated_at=? WHERE id=?",
                    (first_response_minutes, resolve_minutes, now, existing["id"]),
                )
                row = connection.execute(
                    "SELECT * FROM sla_policies WHERE id = ?", (existing["id"],)
                ).fetchone()
            else:
                connection.execute(
                    """INSERT INTO sla_policies
                    (id, tenant_id, priority, channel, first_response_minutes,
                     resolve_minutes, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        policy_id,
                        tenant_id,
                        priority,
                        channel,
                        first_response_minutes,
                        resolve_minutes,
                        now,
                        now,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM sla_policies WHERE id = ?", (policy_id,)
                ).fetchone()
        return dict(row)

    def list_sla_policies(self, tenant_id: str | None = None) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if tenant_id is not None:
                # A tenant sees its own policies plus the global defaults that
                # also apply to it (the global tier is a valid configure target).
                rows = connection.execute(
                    "SELECT * FROM sla_policies WHERE tenant_id = ? OR tenant_id IS NULL "
                    "ORDER BY priority",
                    (tenant_id,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM sla_policies ORDER BY tenant_id, priority"
                ).fetchall()
        return [dict(r) for r in rows]

    def resolve_sla_policy(
        self,
        tenant_id: str,
        priority: str,
        channel: str,
        default_minutes: int,
    ) -> int:
        """Resolve the SLA resolve_minutes for a conversation (backlog).

        Lookup order: (tenant, priority, channel) exact -> (tenant, priority,
        NULL channel) -> (tenant, NULL priority, NULL channel) tenant default
        -> global exact (NULL tenant, priority, channel) -> global
        (NULL tenant, priority, NULL channel) -> global default (all NULL)
        -> the built-in default_minutes.
        """
        candidates = [
            (tenant_id, priority, channel),
            (tenant_id, priority, None),
            (tenant_id, None, None),
            (None, priority, channel),
            (None, priority, None),
            (None, None, None),
        ]
        for tenant, prio, chan in candidates:
            row = self._find_sla_policy(tenant, prio, chan)
            if row is not None:
                return int(row["resolve_minutes"])
        return default_minutes

    def _find_sla_policy(
        self, tenant_id: str | None, priority: str | None, channel: str | None
    ) -> dict[str, Any] | None:
        with self.connect() as connection:
            # Portable null-safe equality (see set_sla_policy): ``IS ?`` is a
            # SQLite extension that would break the PostgreSQL dialect.
            row = connection.execute(
                "SELECT * FROM sla_policies WHERE "
                "(tenant_id = ? OR (tenant_id IS NULL AND ? IS NULL)) AND "
                "(priority = ? OR (priority IS NULL AND ? IS NULL)) AND "
                "(channel = ? OR (channel IS NULL AND ? IS NULL)) LIMIT 1",
                (tenant_id, tenant_id, priority, priority, channel, channel),
            ).fetchone()
        return dict(row) if row else None


def _agent_group_row(row: Any) -> dict[str, Any]:
    """Parse an agent_groups row, expanding skills_json."""
    item = dict(row)
    try:
        item["skills"] = json.loads(item.pop("skills_json") or "[]")
    except (TypeError, ValueError):
        item["skills"] = []
    return item
