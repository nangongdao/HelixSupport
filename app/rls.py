"""PostgreSQL row-level tenant isolation (ROADMAP 43.2 contract (a)).

Defence in depth *underneath* the application-layer ``tenant_id`` filters:
every protected table carries a policy that compares the row's ``tenant_id``
against a transaction-scoped custom GUC set by the connection layer from the
ambient :data:`app.context.tenant_scope_context` — never from request input.
A session that somehow lost its ``WHERE tenant_id`` predicate, or a pooled
connection reused without fresh context, can no longer read or write rows
outside the established scope:

* no GUC at all  -> ``current_setting(..., true)`` returns NULL -> zero rows
  match, inserts/updates fail the ``WITH CHECK`` (fail closed);
* wrong tenant   -> same equality test simply does not match;
* pool reuse     -> the GUC is transaction-scoped (``set_config(..., true)``)
  and disappears at commit/rollback, so the next transaction starts clean.

Cross-tenant system work (queue claiming, housekeeping sweeps, schema
initialisation/migrations) runs inside
:func:`app.context.maintenance_scope`, which carries no tenant GUC by design
and therefore requires a database role RLS does not bind — the table owner,
or a dedicated non-app role with ``BYPASSRLS``. Provisioning those roles is a
deployment concern; ``scripts/run_rls_drill.py`` provisions them on a scratch
cluster and proves every guarantee above against live PostgreSQL.

Rollout posture: ``install_rls`` is idempotent DDL executed like the other
PostgreSQL compatibility installers; ``DATABASE_RLS_ENABLED=1`` additionally
makes application connections bind and enforce the per-transaction context.
Table owners bypass RLS unless ``force_owner=True``, so single-role dev
deployments keep working while least-privilege deployments get the full
isolation boundary.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("helix")

# The custom GUC carrying the caller's tenant for the current transaction.
TENANT_CONTEXT_GUC = "app.tenant_id"

POLICY_NAME = "helix_tenant_isolation"

# Core customer-data surfaces whose access paths are strictly tenant-scoped
# in application code AND whose schema pins ``tenant_id TEXT NOT NULL``:
# baseline (v01), attachments (v22), archive cold tier (v24), channel
# threads (v27). Deliberately excluded: configuration/registry tables that
# mix global rows or unauthenticated token paths (tenants, sla_policies,
# prompt_versions, csat_surveys, webhook_*, quality_daily aggregates) — they
# join the policy set only together with their access-path rework.
RLS_TABLES: tuple[str, ...] = (
    "conversations",
    "messages",
    "knowledge_articles",
    "turn_jobs",
    "turn_requests",
    "feedback",
    "canned_responses",
    "saved_views",
    "conversation_labels",
    "audit_events",
    "attachments",
    "data_subject_requests",
    "orders",
    "channel_threads",
    "conversations_archive",
    "messages_archive",
    "conversation_labels_archive",
    "feedback_archive",
)


class TenantContextError(RuntimeError):
    """Raised when an RLS-enforcing connection has no ambient scope.

    Failing loudly beats silently seeing zero rows: this error means a code
    path reached the database neither inside :func:`app.context.tenant_scope`
    nor :func:`app.context.maintenance_scope`, which under RLS would return
    empty/partial results instead of raising.
    """


def tenant_filter_clause() -> str:
    """The shared USING / WITH CHECK expression for one policy."""
    return f"tenant_id = current_setting('{TENANT_CONTEXT_GUC}', true)"


def _is_tenant_policy(expression: str) -> bool:
    """True when a stored policy expression matches the tenant filter.

    ``pg_get_expr`` renders the parsed node tree, not the source text: the
    string literal gains an explicit ``::text`` cast and the whole expression
    is parenthesised. Comparing source text would therefore always fail, so
    this checks the three semantic tokens that make the policy the isolation
    boundary: the row column, the GUC lookup, and the exact custom GUC name.
    """
    return (
        "tenant_id" in expression
        and "current_setting" in expression
        and TENANT_CONTEXT_GUC in expression
    )


def install_statements(tables: tuple[str, ...] = RLS_TABLES) -> list[str]:
    """Idempotent PostgreSQL DDL enabling row-level isolation on ``tables``."""
    statements: list[str] = []
    for table in tables:
        clause = tenant_filter_clause()
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS {POLICY_NAME} ON {table}")
        statements.append(
            f"CREATE POLICY {POLICY_NAME} ON {table} USING ({clause}) WITH CHECK ({clause})"
        )
    return statements


def force_owner_statement(table: str) -> str:
    """Subject the table owner to RLS as well (paranoid deployments)."""
    return f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"


def install_rls(connection: Any, *, force_owner: bool = False) -> None:
    """Apply :func:`install_statements`; optionally FORCE on the owner.

    Idempotent: policies are dropped and recreated, ENABLE is a no-op when
    already active. Must run as the migration/owner role (the release job's
    role), never as the least-privilege app role.
    """
    statements = install_statements()
    if force_owner:
        statements.extend(force_owner_statement(table) for table in RLS_TABLES)
    for statement in statements:
        connection.execute(statement)


def verify_rls(connection: Any) -> dict[str, Any]:
    """Report live RLS state per protected table for ops/diagnostics.

    Returns ``{"guc": ..., "current_user": ..., "tables": {name: {...}}}``
    where each table entry carries ``rel_rls`` (ENABLEd), ``rel_force``
    (FORCED) and ``policy_ok`` (the isolation policy exists with both
    USING and WITH CHECK bound to the tenant equality clause).
    """
    placeholders = ", ".join("?" for _ in RLS_TABLES)
    rows = connection.execute(
        "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
        f"FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
        f"WHERE n.nspname = 'public' AND c.relkind = 'r' "
        f"AND c.relname IN ({placeholders})",
        tuple(RLS_TABLES),
    ).fetchall()
    states = {
        str(row["relname"]): {
            "rel_rls": bool(row["relrowsecurity"]),
            "rel_force": bool(row["relforcerowsecurity"]),
        }
        for row in rows
    }

    policy_rows = connection.execute(
        "SELECT pol.polrelid::regclass AS table_name, pol.polname, "
        "pg_get_expr(pol.polqual, pol.polrelid) AS using_expr, "
        "pg_get_expr(pol.polwithcheck, pol.polrelid) AS check_expr "
        "FROM pg_policy pol WHERE pol.polname = ?",
        (POLICY_NAME,),
    ).fetchall()
    for row in policy_rows:
        entry = states.setdefault(str(row["table_name"]), {})
        entry["policy_ok"] = _is_tenant_policy(str(row["using_expr"] or "")) and _is_tenant_policy(
            str(row["check_expr"] or "")
        )

    guc_row = connection.execute(
        "SELECT current_setting(?, true) AS value", (TENANT_CONTEXT_GUC,)
    ).fetchone()
    user_row = connection.execute("SELECT current_user AS who").fetchone()
    # PostgreSQL returns '' (not NULL) for an unset custom GUC; normalise it
    # so ``current_tenant`` is None when no tenant scope is active.
    guc_value = guc_row["value"] if guc_row else None
    return {
        "guc": TENANT_CONTEXT_GUC,
        "current_tenant": guc_value or None,
        "current_user": user_row["who"] if user_row else None,
        "tables": states,
    }


def missing_protection(report: dict[str, Any]) -> list[str]:
    """Tables from ``verify_rls`` lacking full ENABLE+policy coverage."""
    problems: list[str] = []
    for table in RLS_TABLES:
        state = report.get("tables", {}).get(table)
        if state is None:
            problems.append(f"{table}: not found")
        elif not state.get("rel_rls"):
            problems.append(f"{table}: RLS not enabled")
        elif not state.get("policy_ok", False):
            problems.append(f"{table}: isolation policy missing or stale")
    return problems


__all__ = [
    "POLICY_NAME",
    "RLS_TABLES",
    "TENANT_CONTEXT_GUC",
    "TenantContextError",
    "force_owner_statement",
    "install_rls",
    "install_statements",
    "missing_protection",
    "tenant_filter_clause",
    "verify_rls",
]
