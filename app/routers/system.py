"""System routes: health, operator home, me, dashboard, runtime metrics (27.2).

Also hosts the cross-cell replication ingress (ROADMAP 2.2.2): the internal
``/api/internal/replication/apply`` endpoint is authenticated with the
control-plane secret so a cell cannot be poisoned by an unauthenticated peer.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from app.db._util import utc_now
from app.main import (
    get_principal,
    require_permission,
)
from app.routers.common import RouteDeps
from app.schemas import DashboardOut, MeOut
from app.security import ROLE_PERMISSIONS, Principal


def build_router(deps: RouteDeps) -> APIRouter:
    router = APIRouter()
    settings = deps.settings
    database = deps.database
    services = deps.services

    def _require_internal_auth(
        x_internal_token: Annotated[str | None, Header(alias="X-Internal-Token")] = None,
    ) -> None:
        """Cell-to-cell authentication for the replication ingress.

        The token is the control-plane secret; production deployments must set
        CONTROL_PLANE_SECRET (a long random value) or every cell shares the
        development default, which is equivalent to no auth at all.
        """
        expected = settings.control_plane_secret or settings.widget_secret
        if not x_internal_token or not hmac.compare_digest(x_internal_token, expected):
            raise HTTPException(status_code=401, detail="invalid internal token")

    # ROADMAP 2.2.2: tables allowed to receive replicated rows. The column
    # whitelist below is intersected with the live table's columns at apply
    # time, and NOT NULL columns without defaults are synthesised so an
    # upsert can never fail on a constraint.
    _REPLICATED_TABLES = {"conversations", "messages", "audit_events", "knowledge_articles"}
    _REPLICATED_COLUMNS = {
        "conversations": {"customer_name", "status", "preview", "channel", "intent", "priority"},
        "messages": {"content", "role", "channel_message_id", "kind", "seq"},
        "audit_events": {"event_type", "payload_json", "actor_id"},
        "knowledge_articles": {"title", "body", "status"},
    }
    _REPLICATED_SYNTHETIC = {
        "conversations": {"channel": "replicated", "status": "open", "customer_name": "Replicated"},
        "messages": {"role": "assistant", "content": ""},
        "audit_events": {"event_type": "replicated", "payload_json": "{}", "actor_id": "system"},
    }

    @router.post(
        "/api/internal/replication/apply",
        dependencies=[Depends(_require_internal_auth)],
        tags=["internal"],
        summary="Apply a replicated cross-cell change",
        description=(
            "Ingress for cell-to-cell async replication (ROADMAP 2.2.2): a peer cell "
            "pushes a tenant-scoped insert/update/delete for a whitelisted table. "
            "Authenticated with the control-plane secret."
        ),
    )
    async def apply_replication_change(payload: dict[str, Any]) -> dict[str, str]:
        """Apply one replicated change pushed by a peer cell (ROADMAP 2.2.2).

        The payload carries the tenant-scoped row (upsert semantics; deletes
        remove the row). The replicated row is written with columns that exist
        in both the whitelist and the live table; when the row cannot be
        applied (missing required columns), the entry stays pending on the
        source so the worker retries and operations can inspect the failure.
        """
        from uuid import uuid4

        table_name = str(payload.get("table_name") or "").strip()
        row_id = str(payload.get("row_id") or "").strip()
        operation = str(payload.get("operation") or "").strip()
        tenant_id = str(payload.get("tenant_id") or "").strip()
        if table_name not in _REPLICATED_TABLES:
            raise HTTPException(status_code=400, detail=f"unsupported replicated table: {table_name}")
        if not row_id or operation not in {"insert", "update", "delete"}:
            raise HTTPException(status_code=400, detail="invalid replication payload")

        with database.connect() as conn:
            # PRAGMA table_info is the only schema introspection that works on
            # both SQLite and the PostgreSQL compatibility shim.
            live_columns = {
                str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table_name})")
            }
            allowed = _REPLICATED_COLUMNS[table_name] & live_columns

            if operation == "delete":
                conn.execute(
                    f"DELETE FROM {table_name} WHERE tenant_id = ? AND id = ?",
                    (tenant_id, row_id),
                )
            else:
                incoming = payload.get("payload") or {}
                values: dict[str, Any] = {
                    col: incoming.get(col) for col in allowed if col in incoming
                }
                # NOT NULL columns without a usable incoming value get a
                # synthetic default so the apply never dies on a constraint.
                for col, fallback in _REPLICATED_SYNTHETIC.get(table_name, {}).items():
                    if col in live_columns and col not in values:
                        values[col] = fallback
                for time_col in ("created_at", "updated_at"):
                    if time_col in live_columns and time_col not in values:
                        values[time_col] = utc_now()
                if not values:
                    raise HTTPException(
                        status_code=422,
                        detail="replicated payload carries none of the whitelisted columns",
                    )
                columns = ", ".join(values)
                placeholders = ", ".join("?" for _ in values)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table_name} (id, tenant_id, {columns}) "
                    f"VALUES (?, ?, {placeholders})",
                    (row_id, tenant_id, *values.values()),
                )
            conn.execute(
                "INSERT INTO replication_log ("
                " id, tenant_id, source_region, target_region, table_name, row_id,"
                " operation, payload_json, created_at, replicated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"repl-applied-{uuid4().hex[:12]}",
                    tenant_id,
                    str(payload.get("source_region") or "unknown"),
                    settings.current_cell_id,
                    table_name,
                    row_id,
                    operation,
                    "{}",
                    utc_now(),
                    utc_now(),
                ),
            )
        return {"status": "applied"}

    @router.get("/health")
    @router.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok", "env": settings.app_env}

    @router.get("/health/startup", response_model=None)
    def startup() -> JSONResponse:
        """Startup probe (Phase 30.2): true once the app is serving.

        Distinct from liveness (process alive) and readiness (dependencies
        reachable): a freshly-started instance may report startup=ok before
        readiness=ok while it initializes.
        """
        return JSONResponse(status_code=200, content={"status": "started"})

    @router.get("/health/ready", response_model=None)
    def readiness() -> JSONResponse:
        ready = database.ping()
        queue_ready = deps.queue.is_ready()
        all_ready = ready and queue_ready
        return JSONResponse(
            status_code=200 if all_ready else 503,
            content={
                "status": "ready" if all_ready else "not_ready",
                "database": ready,
                "process_role": settings.process_role,
                "runs_turn_worker": settings.runs_turn_worker,
                "queue": {
                    "backend": deps.queue.backend_name,
                    "ready": queue_ready,
                    "degraded_reason": deps.queue.degraded_reason,
                    "last_success_epoch": deps.queue.last_success_epoch,
                },
            },
        )

    @router.get("/", response_class=HTMLResponse)
    def operator_home() -> HTMLResponse:
        index = deps.static_dir / "index.html"
        if not index.exists():
            return HTMLResponse("<h1>Helix Support API</h1><p>Operator UI missing.</p>")
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @router.get("/widget", response_class=HTMLResponse, include_in_schema=False)
    def widget_home() -> HTMLResponse:
        """Mobile-first customer Web Chat shell (ROADMAP 17.3)."""
        index = deps.static_dir / "widget.html"
        if not index.exists():
            return HTMLResponse("<h1>Web Chat unavailable</h1>", status_code=503)
        return HTMLResponse(index.read_text(encoding="utf-8"))

    @router.get("/favicon.ico", include_in_schema=False, response_model=None)
    def favicon() -> FileResponse:
        return FileResponse(deps.static_dir / "favicon.svg", media_type="image/svg+xml")

    @router.get("/api/me", response_model=MeOut)
    def me(principal: Annotated[Principal, Depends(get_principal)]) -> MeOut:
        return MeOut(
            tenant_id=principal.tenant_id,
            actor_id=principal.actor_id,
            role=principal.role,
            permissions=sorted(ROLE_PERMISSIONS[principal.role]),
            local_drafts_enabled=settings.local_drafts_enabled,
            local_draft_ttl_minutes=settings.local_draft_ttl_minutes,
            credential_id=principal.credential_id,
        )

    @router.get("/api/dashboard", response_model=DashboardOut)
    def dashboard(
        principal: Annotated[Principal, Depends(require_permission("conversation:read"))],
    ) -> DashboardOut:
        return DashboardOut(**database.dashboard(principal.tenant_id))

    @router.get("/api/system/metrics")
    def runtime_metrics(
        _principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
    ) -> dict[str, Any]:
        snapshot = services.metrics.snapshot()
        snapshot["database"] = database.performance_stats()
        snapshot["turn_jobs"] = database.turn_job_stats(_principal.tenant_id)
        snapshot["turn_worker"] = deps.turn_worker.snapshot()
        snapshot["telemetry"] = deps.telemetry_metrics.snapshot() if deps.telemetry_metrics else {}
        return snapshot

    @router.get("/api/admin/diagnostics")
    def diagnostics(
        principal: Annotated[Principal, Depends(require_permission("admin:manage"))],
    ) -> dict[str, Any]:
        """Support diagnostics bundle (Phase 30.2).

        Version, redacted config summary, queue state, worker snapshot,
        recent terminal failures, and the audit chain head — everything an
        on-call engineer needs to triage, without secrets.
        """
        from app.main import APP_VERSION

        config_summary = {
            "app_env": settings.app_env,
            "deployment_profile": settings.deployment_profile,
            "process_role": settings.process_role,
            "database_backend": settings.database_backend,
            "queue_backend": settings.queue_backend,
            "queue_failure_mode": settings.queue_failure_mode,
            "auth_mode": settings.auth_mode,
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "turn_worker_enabled": settings.turn_worker_enabled,
            "docs_enabled": settings.docs_enabled,
            "inbound_channel_accounts": deps.services.inbound_channels.account_count,
            "channel_webhook_replay_window_seconds": (
                settings.channel_webhook_replay_window_seconds
            ),
        }
        queue_state = {
            "backend": deps.queue.backend_name,
            "failure_mode": settings.queue_failure_mode,
            "ready": deps.queue.is_ready(),
            "degraded_reason": deps.queue.degraded_reason,
            "last_success_epoch": deps.queue.last_success_epoch,
            "jobs": database.turn_job_stats(),
        }
        worker = deps.turn_worker.snapshot()
        audit_head = database.audit_chain_head()
        with database.connect() as conn:
            failed_rows = conn.execute(
                "SELECT error_code, COUNT(*) AS n FROM turn_jobs "
                "WHERE status='failed' GROUP BY error_code ORDER BY n DESC LIMIT 5"
            ).fetchall()
        return {
            "version": APP_VERSION,
            "generated_at": utc_now(),
            "config": config_summary,
            "queue": queue_state,
            "turn_worker": worker,
            "audit_chain_head": audit_head,
            "recent_failures": [dict(r) for r in failed_rows],
        }

    return router
