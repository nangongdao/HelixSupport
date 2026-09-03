"""Ambient execution context (Phase 42.6 / 43.2).

Two contextvars travel with every request/worker task:

``request_id_context``
    Correlation id shared by the HTTP entry point, the turn job row, worker
    audit events and outbound webhooks (42.6 trace-context chain).

``tenant_scope_context``
    The server-derived tenant (or explicit maintenance marker) the current
    task is operating on. Set *only* from authenticated identity — the API
    key principal, the widget/channel token, or the durable job row — never
    from request parameters. When PostgreSQL row-level security is enabled
    (:mod:`app.rls`), :class:`app.postgres_db.PostgresDatabase` turns this
    value into a transaction-scoped ``set_config('app.tenant_id', ...)`` so
    RLS policies filter rows even if application-level ``WHERE tenant_id``
    predicates were lost.

Scope modes
-----------
``tenant_scope(tenant_id)``
    Business logic on behalf of exactly one tenant.
``maintenance_scope(reason)``
    Cross-tenant system bookkeeping (queue claiming, housekeeping sweeps,
    schema init/migrations). Connections made inside this scope carry no
    tenant GUC, so they require a database role that RLS does not bind
    (table owner or BYPASSRLS) — provisioning is a deployment concern and is
    verified by ``scripts/run_rls_drill.py``. Every activation logs at INFO
    with the reason so operations can audit break-glass usage.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

logger = logging.getLogger("helix")

request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)

_SCOPE_TENANT = "tenant"
_SCOPE_MAINTENANCE = "maintenance"

# (mode, id-or-reason) pair; None means "no scope established yet".
tenant_scope_context: ContextVar[tuple[str, str] | None] = ContextVar("tenant_scope", default=None)


def current_request_id() -> str | None:
    return request_id_context.get()


def current_tenant() -> str | None:
    """The tenant this task operates on, or None outside a tenant scope."""
    scope = tenant_scope_context.get()
    return scope[1] if scope and scope[0] == _SCOPE_TENANT else None


def current_scope_mode() -> str | None:
    """``tenant``, ``maintenance`` or None when no scope was established."""
    scope = tenant_scope_context.get()
    return scope[0] if scope else None


def bind_tenant_scope(tenant_id: str) -> str:
    """Bind the tenant scope for the remainder of the current task.

    Unlike :func:`tenant_scope` this deliberately does not return a reset
    handle: request dependencies call it once after authentication and the
    surrounding task's context — discarded when the request finishes — acts
    as the reset boundary.
    """
    if not tenant_id or not tenant_id.strip():
        raise ValueError("bind_tenant_scope requires a non-empty tenant id")
    cleaned = tenant_id.strip()
    tenant_scope_context.set((_SCOPE_TENANT, cleaned))
    return cleaned


@contextmanager
def tenant_scope(tenant_id: str) -> Iterator[str]:
    """Run the block on behalf of one tenant (RLS row filter context)."""
    if not tenant_id or not tenant_id.strip():
        raise ValueError("tenant_scope requires a non-empty tenant id")
    cleaned = tenant_id.strip()
    token: Token[tuple[str, str] | None] = tenant_scope_context.set((_SCOPE_TENANT, cleaned))
    try:
        yield cleaned
    finally:
        tenant_scope_context.reset(token)


@contextmanager
def maintenance_scope(reason: str) -> Iterator[str]:
    """Run cross-tenant system work; connections carry no tenant context.

    Requires an exempt database role when RLS is enabled (see module doc).
    Nested scopes are allowed: an inner ``tenant_scope`` narrows back to one
    tenant and the reset restores the maintenance marker.
    """
    if not reason or not reason.strip():
        raise ValueError("maintenance_scope requires a reason for the audit trail")
    cleaned = reason.strip()
    token: Token[tuple[str, str] | None] = tenant_scope_context.set((_SCOPE_MAINTENANCE, cleaned))
    logger.info("rls.maintenance_scope_entered reason=%s", cleaned)
    try:
        yield cleaned
    finally:
        tenant_scope_context.reset(token)
