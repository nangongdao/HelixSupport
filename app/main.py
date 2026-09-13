from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.bootstrap import AppServices  # noqa: F401  (re-export; home is bootstrap)
from app.config import Settings
from app.context import bind_tenant_scope
from app.observability import configure_logging
from app.schemas import (
    CannedResponseOut,
    ConversationOut,
    KnowledgeArticleOut,
    MessageOut,
    TurnJobOut,
    TurnResponse,
)
from app.security import (
    AuthenticationError,
    AuthorizationError,
    Principal,
)
from app.telemetry import configure_tracing
from app.telemetry import metrics as telemetry_metrics

configure_logging()
configure_tracing()
logger = logging.getLogger("helix")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
APP_VERSION = "2.12.0"


def _conversation_quota_exceeded(database: Any, tenant_id: str) -> str | None:
    """Return a message when the tenant's conversation quota is exhausted (22.4).

    The quota counts *active* conversations (status != 'resolved'). Returns
    ``None`` when the tenant has no quota (unlimited) or is under it.
    """
    try:
        quota = database.get_tenant_quota(tenant_id)
    except LookupError:
        return None
    limit = quota.get("conversation_quota")
    if limit is None:
        return None
    with database.connect() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM conversations WHERE tenant_id = ? AND status != 'resolved'",
            (tenant_id,),
        ).fetchone()
    count = int(row["n"]) if row else 0
    if count >= limit:
        return f"Conversation quota exceeded ({count}/{limit})"
    return None


def get_services(request: Request) -> AppServices:
    return request.app.state.services


async def get_principal(
    request: Request,
    response: Response,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
    x_tenant_id: Annotated[str | None, Header(alias="X-Tenant-Id")] = None,
) -> Principal:
    services = get_services(request)
    try:
        principal = services.authenticator.authenticate(x_api_key, x_tenant_id)
    except AuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not services.database.tenant_exists(principal.tenant_id):
        raise HTTPException(status_code=403, detail="Tenant is not provisioned")
    allowed, remaining, retry_after = services.limiter.check(principal.credential_id)
    response.headers["X-RateLimit-Limit"] = str(services.settings.rate_limit_per_minute)
    response.headers["X-RateLimit-Remaining"] = str(remaining)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    request.state.principal = principal
    # Phase 43.2 contract (a): bind the authenticated tenant as the ambient
    # RLS scope for the rest of this request task. Set only from the verified
    # credential — never from request parameters. ``tenant_exists`` above ran
    # before the scope existed, which is fine while enforcement stays off and,
    # once on, belongs to the pre-scope authentication phase (the tenants
    # table is not row-level protected).
    bind_tenant_scope(principal.tenant_id)
    return principal


def require_permission(permission: str) -> Callable[..., Principal]:
    def dependency(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if not principal.can(permission):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return principal

    return dependency


def require_any_permission(*permissions: str) -> Callable[..., Principal]:
    def dependency(
        principal: Annotated[Principal, Depends(get_principal)],
    ) -> Principal:
        if not any(principal.can(permission) for permission in permissions):
            raise HTTPException(status_code=403, detail="Insufficient permissions")
        return principal

    return dependency


def conversation_out(row: dict[str, Any]) -> ConversationOut:
    payload = dict(row)
    payload.pop("queue_priority_rank", None)
    if "labels_json" in payload:
        labels = payload.pop("labels_json")
        payload["labels"] = json.loads(labels) if isinstance(labels, str) else labels
    else:
        payload.setdefault("labels", [])
    now = datetime.now(UTC)
    if "sla_breached" not in payload:
        due_at = payload.get("sla_due_at")
        breached = False
        if due_at and payload.get("status") != "resolved":
            breached = datetime.fromisoformat(due_at) < now
        payload["sla_breached"] = breached
    expires_at = payload.get("claim_expires_at")
    claim_active = bool(
        payload.get("claimed_by") and expires_at and datetime.fromisoformat(expires_at) > now
    )
    payload["claim_active"] = claim_active
    if not claim_active:
        payload["claimed_by"] = None
        payload["claimed_at"] = None
        payload["claim_expires_at"] = None
    return ConversationOut(**payload)


def canned_response_out(row: dict[str, Any]) -> CannedResponseOut:
    return CannedResponseOut(**row)


def knowledge_out(row: dict[str, Any]) -> KnowledgeArticleOut:
    payload = dict(row)
    payload["tags"] = [tag for tag in str(payload["tags"]).split() if tag]
    payload["active"] = bool(payload["active"])
    payload.pop("retrieval_score", None)
    payload.pop("matched_terms", None)
    return KnowledgeArticleOut(**payload)


def turn_job_out(
    row: dict[str, Any],
    *,
    idempotent_replay: bool = False,
    include_result: bool = True,
) -> TurnJobOut:
    result = None
    if include_result and row.get("response_json"):
        result = TurnResponse(**json.loads(row["response_json"]))
    return TurnJobOut(
        id=row["id"],
        tenant_id=row["tenant_id"],
        conversation_id=row["conversation_id"],
        status=row["status"],
        attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        available_at=row["available_at"],
        locked_at=row.get("locked_at"),
        error_code=row.get("error_code"),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        completed_at=row.get("completed_at"),
        idempotent_replay=idempotent_replay,
        result=result,
    )


def message_out(row: dict[str, Any]) -> MessageOut:
    """Build the public message resource, dropping internal columns.

    ``list_messages`` keeps ``seq`` (the monotonic cursor key) in the row so the
    API can build the next cursor; it is not part of the public resource. The
    ``channel_message_id`` (Phase 23.2) is likewise an internal dedup key.
    """
    payload = dict(row)
    payload.pop("seq", None)
    payload.pop("channel_message_id", None)
    return MessageOut(**payload)


def _message_intent_for_quality(
    database: Any, tenant_id: str, conversation_id: str, message_id: str
) -> str | None:
    """Best-effort intent lookup for a feedback-rated message.

    The assistant message's metadata carries the turn's intent (written by the
    orchestrator during routing).  Returns ``None`` when the metadata is
    missing so the aggregator falls back to the ``unknown`` bucket.
    """
    try:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM messages WHERE tenant_id=? AND id=?",
                (tenant_id, message_id),
            ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"] or "{}")
        intent = metadata.get("intent")
        return str(intent) if intent else None
    except Exception:
        logger.exception("failed to resolve intent for quality feedback")
        return None


def _message_prompt_version_for_quality(
    database: Any, tenant_id: str, message_id: str
) -> str | None:
    """Best-effort prompt-version lookup for a feedback-rated message."""
    try:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM messages WHERE tenant_id=? AND id=?",
                (tenant_id, message_id),
            ).fetchone()
        if row is None:
            return None
        metadata = json.loads(row["metadata_json"] or "{}")
        version = metadata.get("prompt_version")
        return str(version) if version else None
    except Exception:
        logger.exception("failed to resolve prompt_version for quality feedback")
        return None


def _message_date_for_quality(database: Any, tenant_id: str, message_id: str) -> str | None:
    """Best-effort creation date (YYYY-MM-DD) of a feedback-rated message.

    The quality aggregate attributes a turn to the day the turn was
    processed, so feedback must land in the same bucket: using the rating
    submission date instead would misattribute negative counts when a
    customer rates a message on a later calendar day.
    """
    try:
        with database.connect() as connection:
            row = connection.execute(
                "SELECT created_at FROM messages WHERE tenant_id=? AND id=?",
                (tenant_id, message_id),
            ).fetchone()
        if row is None or not row["created_at"]:
            return None
        return str(row["created_at"])[:10]
    except Exception:
        logger.exception("failed to resolve message date for quality feedback")
        return None


def create_app(app_settings: Settings | None = None) -> FastAPI:
    settings = app_settings or Settings.from_env()
    settings.validate()

    # 43.3: an expired sunset means a deprecated endpoint should already be
    # gone; serving it past the advertised date breaks the API contract.
    from app.deprecation import validate_registry

    deprecation_problems = validate_registry()
    if deprecation_problems:
        raise RuntimeError(
            "API deprecation registry violates the sunset policy: "
            + "; ".join(deprecation_problems)
        )

    # Phase 27.2 home: the whole service assembly (database bootstrap,
    # credential registry, orchestrator/worker core, audit anchoring,
    # envelope encryption, outbox, archives, control plane, drift/shadow
    # monitors, cells) lives in app/bootstrap.py and runs in the original
    # order.
    from app.bootstrap import build_application

    ctx = build_application(settings)
    database = ctx.database
    queue = ctx.queue
    orchestrator = ctx.orchestrator
    turn_worker = ctx.turn_worker
    webhook_service = ctx.webhook_service
    services = ctx.services
    cell_registry = ctx.cell_registry
    oidc_config = ctx.oidc_config
    oidc_authenticator = ctx.oidc_authenticator
    oidc_flow = ctx.oidc_flow

    app = FastAPI(
        title="Helix Support",
        version=APP_VERSION,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url="/redoc" if settings.docs_enabled else None,
    )
    app.state.services = services
    app.router.on_shutdown.append(turn_worker.stop)
    app.router.on_shutdown.append(database.close)
    # Phase 42.1 / REL-001 web-worker split: a ``web`` process is a stateless
    # API tier and never runs the turn worker / housekeeping loop, so scaling
    # the web fleet does not silently add workers. ``worker``/``all`` start it
    # exactly when the queue backend is ready (M0 REL-001 fail-closed).
    if settings.runs_turn_worker and queue.is_ready():
        app.router.on_startup.append(turn_worker.start)
    elif not settings.runs_turn_worker:
        logger.info("PROCESS_ROLE=web; turn worker not started on this process")
    else:
        # M0 REL-001: a fail-closed deployment must not run its worker while
        # the task queue backend is down; the readiness endpoint reports the
        # degraded queue so the orchestrator can bring it back up.
        logger.error(
            "task queue not ready (%s); turn worker will not start",
            queue.degraded_reason or "unknown",
        )

    # ROADMAP 2.2.1: cell health checks run as background asyncio tasks alongside
    # the turn worker; fire-and-forget (never fatal), only when configured.
    if cell_registry is not None:

        async def _start_cell_health_checks() -> None:
            import asyncio

            from app.cell_router import periodic_health_check

            asyncio.create_task(periodic_health_check(cell_registry, interval_seconds=30))

        app.router.on_startup.append(_start_cell_health_checks)

        # ROADMAP 2.2.2: for every peer cell (any cell that is not the current
        # one), start a replication worker that drains pending replication log
        # entries to that peer's apply endpoint. The worker is fire-and-forget
        # and never fatal.
        from app.region_replication import ReplicationLog, ReplicationWorker, periodic_replication

        peer_cells = [
            cell for cell in cell_registry.list_cells() if cell.cell_id != settings.current_cell_id
        ]

        if peer_cells:

            async def _start_replication_workers() -> None:
                import asyncio

                for peer in peer_cells:
                    base_url = (
                        peer.health_url.rsplit("/health", 1)[0]
                        if "/health" in peer.health_url
                        else peer.health_url
                    )
                    worker = ReplicationWorker(
                        database=database,
                        replication_log=ReplicationLog(database),
                        target_region=peer.region,
                        target_base_url=base_url,
                        batch_size=50,
                    )
                    asyncio.create_task(periodic_replication(worker, interval_seconds=60))
                logger.info(
                    "replication.workers_started peer_count=%d",
                    len(peer_cells),
                )

            app.router.on_startup.append(_start_replication_workers)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_origins),
            allow_credentials=False,
            allow_methods=["GET", "POST", "PUT", "PATCH"],
            allow_headers=[
                "Content-Type",
                "X-API-Key",
                "X-Tenant-Id",
                "X-Request-Id",
                "Idempotency-Key",
            ],
            expose_headers=[
                "X-Has-More",
                "X-Next-Cursor",
                "X-Prev-Cursor",
                "X-Page-Limit",
                "X-Page-Offset",
                "X-Queue-Sort",
                "X-Idempotent-Replay",
                "Location",
                "X-Request-Id",
                "X-RateLimit-Limit",
                "X-RateLimit-Remaining",
            ],
        )

    static_dir = Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # Phase 27.2 home: request controls (request id, security headers, shadow
    # traffic, request metrics) and the versioned Problem Details handlers
    # live in app/middleware.py; the composition root only registers them.
    from app.middleware import register_error_handlers, register_request_controls

    register_request_controls(app, settings=settings, database=database, services=services)
    register_error_handlers(app)

    @app.get("/api/supervisor/knowledge-gaps", tags=["quality"])
    def list_knowledge_gaps(
        principal: Annotated[Principal, Depends(require_permission("metrics:read"))],
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Surface negative-feedback turns with no knowledge citations.

        Used by the supervisor quality panel (Phase 21.2) to locate where the
        knowledge base is failing customers and seed draft articles.
        """
        return database.list_knowledge_gaps(principal.tenant_id, limit=limit)

    # ------------------------------------------------------------------
    # Prompt version registry (Phase 19.1)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # OIDC / BFF session authentication
    # ------------------------------------------------------------------

    # Phase 27.2: domain routers (extracted from create_app).
    from app.routers.admin import build_router as build_admin_router
    from app.routers.analytics import build_router as build_analytics_router
    from app.routers.attachments import build_router as build_attachments_router
    from app.routers.auth import build_router as build_auth_router
    from app.routers.common import RouteDeps
    from app.routers.conversations import build_router as build_conversations_router
    from app.routers.copilot import build_router as build_copilot_router
    from app.routers.knowledge import build_router as build_knowledge_router
    from app.routers.reports import build_router as build_reports_router
    from app.routers.system import build_router as build_system_router
    from app.routers.tickets import build_router as build_tickets_router

    route_deps = RouteDeps(
        settings=settings,
        database=database,
        orchestrator=orchestrator,
        turn_worker=turn_worker,
        services=services,
        queue=queue,
        webhook_service=webhook_service,
        static_dir=static_dir,
        oidc_config=oidc_config,
        oidc_authenticator=oidc_authenticator,
        oidc_flow=oidc_flow,
        telemetry_metrics=telemetry_metrics,
    )
    app.include_router(build_system_router(route_deps))
    app.include_router(build_conversations_router(route_deps))
    app.include_router(build_knowledge_router(route_deps))
    app.include_router(build_admin_router(route_deps))
    app.include_router(build_auth_router(route_deps))
    app.include_router(build_copilot_router(route_deps))
    app.include_router(build_tickets_router(route_deps))
    app.include_router(build_reports_router(route_deps))
    app.include_router(build_attachments_router(route_deps))
    app.include_router(build_analytics_router(route_deps))

    # ROADMAP 2.5.0: governance registry API — maker-checker approvals for
    # tool enablement (the gateway consults these before high-risk calls).
    from app.routers.governance import build_router as build_governance_router

    app.include_router(build_governance_router(route_deps))

    # 43.3: /api/v2 — cursor envelopes, honoured Idempotency-Key, and the
    # transactional domain-event outbox. v1 keeps serving unchanged.
    from app.routers.v2 import build_router as build_v2_router

    app.include_router(build_v2_router(route_deps))

    # Phase 38 / ROADMAP 23.3: public server-to-server channel ingress uses
    # raw-body HMAC authentication instead of an operator API key.
    from app.routers.channels import router as channels_router

    app.include_router(channels_router)

    # Phase 21.1: mount the supervisor quality aggregator router. Mounted
    # after all inline routes so its prefix does not shadow any path.
    from app.quality_routes import router as quality_router

    app.include_router(quality_router)

    # Phase 23.1: mount the public Web Chat widget router (signed-token auth,
    # no API key required for the customer browser).
    from app.widget_routes import router as widget_router

    app.include_router(widget_router)

    # Backlog: mount the public CSAT survey router (one-time token link).
    from app.routers.csat import router as csat_router

    app.include_router(csat_router)

    # Phase 25.2: enrich the OpenAPI spec with per-endpoint summaries, tags,
    # and RBAC notes before the snapshot gate reads it.
    from app.openapi_meta import apply_openapi_metadata

    app.openapi = apply_openapi_metadata(app)

    return app


app = create_app()
