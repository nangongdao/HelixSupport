from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.anchor_service import AnchorService
from app.assets import STATIC_ASSET_VERSION, VERSIONED_STATIC_CACHE_CONTROL
from app.audit_anchor import Ed25519KmsSigner
from app.audit_gap import AuditGapTracker, AuditUnavailableError
from app.channel_webhooks import InboundChannelRegistry
from app.config import Settings
from app.context import bind_tenant_scope, request_id_context
from app.database import Database
from app.db._util import utc_now
from app.deprecation import apply_deprecation_headers as _apply_deprecation_headers
from app.errors import (
    conflict_response,
    not_found_response,
    problem_response,
)
from app.jobs import TurnJobWorker
from app.model_provider import OpenAICompatibleProvider
from app.observability import RuntimeMetrics, configure_logging
from app.orchestrator import (
    ConversationOrchestrator,
    IdempotencyConflictError,
    InvalidTransitionError,
    TurnInProgressError,
)
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.queue import QueueUnavailableError, TaskQueue, create_task_queue
from app.retention import RetentionService
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
    Authenticator,
    AuthorizationError,
    Principal,
    SlidingWindowRateLimiter,
)
from app.session_auth import (
    OIDCAuthenticator,
    OIDCConfig,
)
from app.telemetry import configure_tracing, span
from app.telemetry import metrics as telemetry_metrics
from app.webhooks import WebhookService

configure_logging()
configure_tracing()
logger = logging.getLogger("helix")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
APP_VERSION = "1.4.0"


def _http_exception_code(status_code: int) -> str:
    """Map an HTTP status to a stable Problem Details code slug (Phase 25.1)."""
    return {
        400: "bad_request",
        401: "unauthorized",
        403: "forbidden",
        404: "not_found",
        409: "conflict",
        422: "unprocessable",
        429: "rate_limited",
    }.get(status_code, "http_error")


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


def _json_safe_errors(errors: Sequence[Any]) -> list[dict[str, Any]]:
    """Make FastAPI validation errors JSON-serializable (Phase 25.1).

    Pydantic's ``ctx`` may embed the exception object raised by a model
    validator (e.g. ``ValueError``), and its ``input`` field may carry the
    raw request body (``bytes`` when Content-Type was missing) — replace any
    non-serializable value with its string form so the 422 body renders.
    """
    cleaned: list[dict[str, Any]] = []
    for error in errors:
        item = dict(error)
        for key, value in item.items():
            item[key] = _json_safe_value(value)
        cleaned.append(item)
    return cleaned


def _json_safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _json_safe_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8", errors="replace")
        except Exception:
            return repr(value)
    return str(value)


@dataclass(frozen=True)
class AppServices:
    settings: Settings
    database: Database
    authenticator: Authenticator
    orchestrator: ConversationOrchestrator
    turn_worker: TurnJobWorker
    queue: TaskQueue
    limiter: SlidingWindowRateLimiter
    metrics: RuntimeMetrics
    inbound_channels: InboundChannelRegistry
    retention_service: RetentionService | None = None
    data_protection: Any | None = None
    webhooks: WebhookService | None = None
    quality: QualityService | None = None
    prompts: PromptRegistry | None = None
    copilot: Any | None = None
    reports: Any | None = None
    attachments: Any | None = None
    credential_store: Any | None = None
    credential_lifecycle: Any | None = None
    anchor_service: Any | None = None
    gap_tracker: Any | None = None
    control_plane: Any | None = None
    data_plane_config: Any | None = None
    envelope_cipher: Any | None = None
    outbox_consumer: Any | None = None
    drift_monitor: Any | None = None


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

    database: Any
    if settings.database_backend == "postgresql":
        from app.postgres_db import create_postgres_database

        database = create_postgres_database(
            settings.database_url,
            auto_migrate=settings.database_auto_migrate,
            rls_enabled=settings.database_rls_enabled,
        )
    else:
        database = Database(
            settings.database_path,
            pool_size=settings.database_pool_size,
            busy_timeout_ms=settings.database_busy_timeout_ms,
            knowledge_cache_ttl_seconds=settings.knowledge_cache_ttl_seconds,
            dashboard_cache_ttl_seconds=settings.dashboard_cache_ttl_seconds,
            cache_max_entries=settings.cache_max_entries,
        )
        if settings.database_auto_migrate:
            database.initialize()
        else:
            # 42.2 HA/PITR: startup runs with a least-privilege DB role — no
            # DDL here. Fail fast when the release job has not migrated the
            # schema to the current version instead of serving from a stale
            # or half-migrated database.
            from app.migrations import all_migrations, pending_migration_versions

            with database.connect() as connection:
                pending = pending_migration_versions(connection, all_migrations())
            if pending:
                raise RuntimeError(
                    "DATABASE_AUTO_MIGRATE=false but the schema is not current "
                    f"(pending migrations: {pending}); "
                    "run `python scripts/run_migrations.py` as the release job first"
                )

    # Phase 41.4 DATA: after a backup restore an old snapshot can resurrect
    # erased customer data; re-apply every recorded tombstone deletion before
    # the API starts serving.
    if settings.enable_data_protection:
        from app.context import maintenance_scope
        from app.privacy import DataProtectionService

        # 43.2: cross-tenant system sweep — explicit maintenance scope keeps
        # RLS-enforcing PostgreSQL connections working under an exempt role.
        with maintenance_scope("tombstone-restore-replay"):
            DataProtectionService(
                RetentionService(database, dsr_export_secret=settings.dsr_export_secret),
                sla_minutes=settings.dsr_sla_minutes,
            ).enforce_tombstones_after_restore()

    # Phase 41 / SEC-004: the credential registry is the single arbiter of the
    # lifecycle for every secret-bearing credential. Config-seeded API keys and
    # channel secrets are registered idempotently (fingerprints only); the
    # authenticator consults the persisted state on every request so a revoke
    # or expiry takes effect across instances immediately.
    from app.credentials import CredentialLifecycle, CredentialStore, register_configured_from_json

    credential_store = CredentialStore(database)
    credential_lifecycle = CredentialLifecycle(credential_store)
    inbound_channels = InboundChannelRegistry(
        settings.effective_channel_webhooks_json,
        settings.channel_webhook_replay_window_seconds,
        credential_store=credential_store,
    )
    try:
        register_configured_from_json(
            store=credential_store,
            type="api_key",
            tenant_id="",
            raw_json=settings.effective_api_keys_json,
            created_at=utc_now(),
        )
        register_configured_from_json(
            store=credential_store,
            type="channel",
            tenant_id="",
            raw_json=settings.effective_channel_webhooks_json,
            created_at=utc_now(),
        )
    except Exception:
        logger.exception("failed to seed credential registry; continuing in legacy mode")

    authenticator = Authenticator(settings, credential_store=credential_store)
    # Phase 28.2: seed the runtime-revoked credential set from persistence so
    # a revoked key stays disabled across restarts.
    try:
        authenticator.load_revoked(database.list_revoked_api_keys())
    except Exception:
        logger.exception("failed to load revoked API keys")
    for tenant_id in authenticator.configured_tenants | inbound_channels.configured_tenants:
        database.ensure_tenant(tenant_id)
    provider = OpenAICompatibleProvider(settings) if settings.enable_llm else None
    queue = create_task_queue(database, settings)
    webhook_service = WebhookService(database)
    orchestrator = ConversationOrchestrator(
        database, settings, provider, queue=queue, webhook_service=webhook_service
    )
    turn_worker = TurnJobWorker(
        database,
        orchestrator,
        queue=queue,
        enabled=settings.turn_worker_enabled,
        concurrency=settings.turn_worker_concurrency,
        poll_interval_ms=settings.turn_job_poll_interval_ms,
        lease_seconds=settings.turn_job_lease_seconds,
        retry_base_seconds=settings.turn_job_retry_base_seconds,
        retention_days=settings.turn_job_retention_days,
        stream_enabled=settings.turn_job_stream_enabled,
        stream_pacing_ms=settings.turn_job_stream_pacing_ms,
        webhook_service=webhook_service,
        webhook_interval_seconds=settings.webhook_delivery_interval_seconds,
        archive_enabled=settings.conversation_archive_enabled,
        archive_after_days=settings.conversation_archive_after_days,
        archive_batch=settings.conversation_archive_batch,
        archive_cadence_hours=settings.conversation_archive_cadence_hours,
    )
    # Backlog (AI 辅助坐席): operator copilot — suggestions, knowledge
    # recommendations, and tone rewrites, all model-first with deterministic
    # fallbacks; the orchestrator's LanguageService keeps suggestions in the
    # customer's language.
    from app.copilot import CopilotService

    quality_service = QualityService(database)
    # Backlog (报表导出与订阅): scheduled report generation reuses the
    # quality aggregates and the webhook delivery machinery.
    from app.reports import ReportService

    report_service = ReportService(database, quality_service, webhook_service)
    turn_worker.report_service = report_service

    # M0 SEC-002: hourly housekeeping prunes expired DSR export objects and
    # OIDC login transactions; failures are logged by the worker, never fatal.
    def _housekeeping_dsr_exports() -> None:
        if services.retention_service is not None:
            services.retention_service.prune_dsr_exports()

    def _housekeeping_oidc_transactions() -> None:
        if oidc_flow is not None:
            oidc_flow.store.prune_expired(int(time.time()))

    turn_worker.housekeeping.extend([_housekeeping_dsr_exports, _housekeeping_oidc_transactions])

    # Phase 41.3 / SEC-005: external audit anchoring.  The signer is an
    # Ed25519 key (KMS stand-in) whose private key is seeded from config (or
    # generated per run), and WORM is a disk directory (object-lock stand-in).
    # The turn-worker housekeeping window exports a fresh claim when due;
    # failures are logged and never fatal, so anchoring degrades but the
    # runtime keeps serving.
    from app.worm_store import DiskWormStore

    gap_tracker = AuditGapTracker()
    anchor_signer = Ed25519KmsSigner.generate()
    anchor_worm = DiskWormStore(settings.audit_worm_dir)
    anchor_service = AnchorService(
        database=database,
        worm_store=anchor_worm,
        signer=anchor_signer,
        environment=settings.audit_anchor_environment,
        cadence_hours=settings.audit_anchor_cadence_hours,
        batch=settings.audit_anchor_batch,
        trusted_kids=settings.audit_trusted_kids,
    )

    def _housekeeping_audit_anchor() -> None:
        anchor_service.anchor_if_due()

    if settings.audit_anchor_enabled:
        turn_worker.housekeeping.extend([_housekeeping_audit_anchor])

    # Phase 43.2 / ROADMAP 43.2: per-tenant envelope encryption for restricted
    # fields. The KMS stand-in is a versioned KEK directory (a cloud-KMS
    # adapter implements the same protocol in production); when disabled or
    # when the KEK directory cannot be provisioned, the cipher is absent and
    # restricted-field operations fail closed with 501/503 rather than
    # silently storing plaintext. The housekeeping sweep re-wraps every
    # tenant's stored DEKs onto the active KEK after a rotation; rows already
    # on the active KEK are skipped, so an idle sweep does no writes.
    from app.envelope_crypto import (
        DatabaseDekKeystore,
        DiskKeyManagementService,
        EnvelopeCryptoError,
        TenantEnvelopeCipher,
    )

    envelope_cipher: Any | None = None
    if settings.envelope_enabled:
        try:
            envelope_kms = DiskKeyManagementService(settings.envelope_kms_dir)
            envelope_kms.generate_initial()
            envelope_cipher = TenantEnvelopeCipher(
                envelope_kms, DatabaseDekKeystore(database), database=database
            )
        except Exception as exc:  # KMS stand-in failed to boot; fail closed.
            logger.warning("envelope.disabled: %s", exc)

    def _housekeeping_envelope_rewrap() -> None:
        if envelope_cipher is None:
            return
        for tenant_id in database.list_tenants():
            if not envelope_cipher.keystore.list_versions(tenant_id):
                continue
            try:
                envelope_cipher.rewrap_tenant_deks(tenant_id)
            except EnvelopeCryptoError as exc:
                logger.warning("envelope.rewrap tenant=%s failed: %s", tenant_id, exc)

    if envelope_cipher is not None:
        turn_worker.housekeeping.extend([_housekeeping_envelope_rewrap])
    # ROADMAP 43.2: webhook signing secrets are a restricted field -- when
    # envelope encryption is enabled, registration stores an envelope instead
    # of plaintext. If the cipher failed to boot, registration is refused
    # rather than silently downgrading to plaintext.
    webhook_service.envelope_cipher = envelope_cipher
    webhook_service.envelope_required = settings.envelope_enabled
    # ROADMAP 43.3: the outbox consumer drains domain events (written by the
    # v2 create path in the same transaction as the business row) and fans
    # them out to subscribed webhook endpoints. The housekeeping sweep runs
    # it on the turn worker's hourly cadence; failures are logged by the
    # worker and never fatal, and the (endpoint_id, event_id) constraint
    # keeps re-drains from duplicating deliveries.
    from app.outbox_consumer import WebhookEventConsumer

    outbox_consumer = WebhookEventConsumer(database, webhook_service)

    def _housekeeping_outbox_drain() -> None:
        if services.outbox_consumer is None:
            return
        try:
            services.outbox_consumer.drain_and_deliver()
        except Exception:
            logger.exception("outbox.drain_failed")

    turn_worker.housekeeping.extend([_housekeeping_outbox_drain])
    # Backlog (语音/富媒体消息): attachment storage/scan/quota service.
    from app.attachment_store import DiskAttachmentStore
    from app.attachments import AttachmentService

    attachment_store = DiskAttachmentStore(settings.attachment_storage_dir)
    attachment_service = AttachmentService(database, settings, store=attachment_store)
    # 42.3 REL-002: cold-tier archive payloads live as compressed partitions
    # on the object store; the database and retention service share it.
    from app.archive_store import ArchiveObjectStore

    archive_object_store = ArchiveObjectStore(settings.archive_object_dir)
    database.archive_object_store = archive_object_store
    retention_service = RetentionService(
        database,
        dsr_export_secret=settings.dsr_export_secret,
        archive_object_store=archive_object_store,
    )
    # 42.4 SEC-006: DSR deletions must also purge attachment objects.
    retention_service.attachment_store = attachment_store

    # 43.1: tenant control plane (signed config snapshots) + data-plane
    # last-known-good view. The signing secret falls back to the widget
    # secret in development; production sets CONTROL_PLANE_SECRET. A too-
    # short secret disables the plane rather than weakening signatures.
    from app.control_plane import DataPlaneConfig, TenantControlPlane

    control_plane_secret = settings.control_plane_secret or settings.widget_secret
    if len(control_plane_secret.encode("utf-8")) >= 32:
        control_plane = TenantControlPlane(database, control_plane_secret)
        data_plane_config = DataPlaneConfig(database, control_plane_secret)
    else:
        logger.warning(
            "control_plane.disabled: CONTROL_PLANE_SECRET (or widget secret) "
            "is shorter than 32 bytes"
        )
        control_plane = None
        data_plane_config = None

    # Phase 41.4 DATA: DSR SLA/approval board, deletion-proof tombstones, and a
    # deferred queue for large deletions (kept off the synchronous API path).
    from app.privacy import DataProtectionService

    data_protection = DataProtectionService(
        retention_service,
        sla_minutes=settings.dsr_sla_minutes,
    )

    def _housekeeping_data_protection() -> None:
        data_protection.flag_sla_breaches()
        data_protection.drain_deferred_jobs()

    turn_worker.housekeeping.extend([_housekeeping_data_protection])

    # ROADMAP 43.5: online drift monitoring. When enabled, the hourly sweep
    # compares quality buckets and refusal-audit counts against the DRIFT_*
    # thresholds and stops any canary of a breaching tenant (clears it back
    # to draft + audits ``ai.drift_canary_stopped``). Disabled by default;
    # failures are logged by the worker, never fatal.
    from app.drift_monitor import DriftMonitor

    drift_monitor = DriftMonitor(database, settings)

    def _housekeeping_drift_monitor() -> None:
        reports = drift_monitor.run_once()
        if reports:
            logger.warning(
                "drift.canaries_stopped %s",
                [{"tenant": r.tenant_id, "signals": [s.kind for s in r.signals]} for r in reports],
            )

    if settings.drift_enabled:
        turn_worker.housekeeping.extend([_housekeeping_drift_monitor])

    services = AppServices(
        settings=settings,
        database=database,
        authenticator=authenticator,
        orchestrator=orchestrator,
        turn_worker=turn_worker,
        limiter=SlidingWindowRateLimiter(settings.rate_limit_per_minute),
        metrics=RuntimeMetrics(),
        inbound_channels=inbound_channels,
        retention_service=retention_service,
        data_protection=data_protection,
        webhooks=webhook_service,
        quality=quality_service,
        prompts=PromptRegistry(database),
        copilot=CopilotService(database, provider, orchestrator.languages),
        reports=report_service,
        attachments=attachment_service,
        queue=queue,
        credential_store=credential_store,
        credential_lifecycle=credential_lifecycle,
        anchor_service=anchor_service,
        gap_tracker=gap_tracker,
        control_plane=control_plane,
        data_plane_config=data_plane_config,
        envelope_cipher=envelope_cipher,
        outbox_consumer=outbox_consumer,
        drift_monitor=drift_monitor,
    )

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

    @app.middleware("http")
    async def request_controls(request: Request, call_next: Callable[..., Any]) -> Response:
        supplied_id = request.headers.get("X-Request-Id", "")
        request_id = (
            supplied_id if REQUEST_ID_PATTERN.fullmatch(supplied_id) else f"req_{uuid4().hex}"
        )
        token = request_id_context.set(request_id)
        started = perf_counter()
        status_code = 500
        route = request.url.path
        with span(
            "http.request",
            method=request.method,
            path=request.url.path,
            request_id=request_id,
        ) as request_span:
            try:
                response = await call_next(request)
                status_code = response.status_code
                is_widget_document = request.url.path.rstrip("/") == "/widget"
                response.headers["X-Request-Id"] = request_id
                response.headers["X-Content-Type-Options"] = "nosniff"
                if not is_widget_document:
                    response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "no-referrer"
                response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
                frame_ancestors = (
                    " ".join(settings.widget_frame_ancestors) if is_widget_document else "'none'"
                )
                response.headers["Content-Security-Policy"] = (
                    "default-src 'self'; script-src 'self'; "
                    "style-src 'self'; "
                    "font-src 'self'; "
                    "img-src 'self' data:; connect-src 'self'; "
                    f"frame-ancestors {frame_ancestors}"
                )
                # 43.3: deprecated operations advertise their removal window on
                # every response (docs/API_POLICY.md §2). The route object in
                # the scope resolves templated paths ("/api/items/{id}") to the
                # registry's operation key shape.
                route_object_for_deprecation = request.scope.get("route")
                if route_object_for_deprecation is not None:
                    _apply_deprecation_headers(
                        f"{request.method} {getattr(route_object_for_deprecation, 'path', '')}",
                        response.headers,
                    )
                if request.url.path.startswith("/api/"):
                    response.headers["Cache-Control"] = "no-store"
                elif request.url.path in {
                    "/",
                    "/widget",
                    "/widget/",
                }:
                    response.headers["Cache-Control"] = "no-cache"
                elif request.url.path.startswith("/static/"):
                    response.headers["Cache-Control"] = (
                        VERSIONED_STATIC_CACHE_CONTROL
                        if request.query_params.get("v") == STATIC_ASSET_VERSION
                        else "no-cache"
                    )
                if settings.is_production:
                    response.headers["Strict-Transport-Security"] = (
                        "max-age=31536000; includeSubDomains"
                    )
                return response
            except Exception:
                logger.exception(
                    "request.failed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "route": request.url.path,
                    },
                )
                raise
            finally:
                duration_ms = int((perf_counter() - started) * 1000)
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", request.url.path)
                request_span.set_attribute("route", route)
                request_span.set_attribute("status_code", status_code)
                services.metrics.observe_request(route, status_code, duration_ms)
                telemetry_metrics.observe(
                    "http_request_duration_ms", duration_ms, route=route, method=request.method
                )
                telemetry_metrics.increment(
                    "http_requests_total",
                    route=route,
                    method=request.method,
                    status=str(status_code),
                )
                logger.info(
                    "request.completed",
                    extra={
                        "request_id": request_id,
                        "method": request.method,
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                )
                request_id_context.reset(token)

    @app.exception_handler(LookupError)
    async def lookup_error_handler(request: Request, exc: LookupError) -> JSONResponse:
        return not_found_response(request, str(exc))

    @app.exception_handler(TurnInProgressError)
    async def turn_in_progress_handler(request: Request, exc: TurnInProgressError) -> JSONResponse:
        return conflict_response(
            request,
            str(exc),
            code="turn_in_progress",
            headers={"Retry-After": "1"},
        )

    @app.exception_handler(IdempotencyConflictError)
    async def idempotency_conflict_handler(
        request: Request, exc: IdempotencyConflictError
    ) -> JSONResponse:
        return conflict_response(request, str(exc), code="idempotency_conflict")

    @app.exception_handler(InvalidTransitionError)
    async def transition_error_handler(
        request: Request, exc: InvalidTransitionError
    ) -> JSONResponse:
        return conflict_response(request, str(exc), code="invalid_transition")

    @app.exception_handler(QueueUnavailableError)
    async def queue_unavailable_handler(
        request: Request, exc: QueueUnavailableError
    ) -> JSONResponse:
        # M0 REL-001: fail-closed deployments answer 503 with a retry window
        # while the task queue backend is unreachable, instead of silently
        # accepting work that can never be processed.
        return problem_response(
            request,
            status_code=503,
            detail=str(exc),
            code="queue_unavailable",
            headers={"Retry-After": "30"},
        )

    @app.exception_handler(AuditUnavailableError)
    async def audit_unavailable_handler(
        request: Request, exc: AuditUnavailableError
    ) -> JSONResponse:
        # Phase 41.3 SEC-005: a high-risk mutation (security / permission /
        # credential / DSR) must never appear to succeed with its audit
        # evidence lost; the mutation itself rolled back, so answer fail-closed
        # with a retry window rather than a misleading success.
        return problem_response(
            request,
            status_code=503,
            detail=str(exc),
            code="audit_unavailable",
            headers={"Retry-After": "30"},
        )

    def _versioned_problem_response(request: Request, **kwargs: Any) -> JSONResponse:
        response = problem_response(request, **kwargs)
        # 43.3: v2 responses always disclose their API version, errors included.
        if request.url.path.startswith("/api/v2/"):
            response.headers["X-API-Version"] = "2.0"
        return response

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        # FastAPI's default HTTPException renders {"detail": ...}; route code
        # raises HTTPException for 400/401/403/404/409/422/429. Normalize every
        # one to Problem Details while keeping the detail field intact.
        return _versioned_problem_response(
            request,
            status_code=exc.status_code,
            detail=str(exc.detail),
            code=_http_exception_code(exc.status_code),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 422 with per-field details; the body retains FastAPI's structured
        # error list under "errors" plus the Problem Details envelope.
        return _versioned_problem_response(
            request,
            status_code=422,
            detail="Request validation failed",
            code="validation_error",
            errors=_json_safe_errors(exc.errors()),
        )

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

    oidc_config = OIDCConfig.from_env() if settings.enable_session_auth else None
    oidc_authenticator = OIDCAuthenticator(oidc_config) if oidc_config else None
    # M0 SEC-001: the verified authorization-code flow (one-time transactions,
    # PKCE, JWKS signature validation, roster-based identity mapping).
    oidc_flow = None
    if oidc_config:
        from app.oidc_flow import DatabaseOIDCTransactionStore, OIDCFlow

        oidc_flow = OIDCFlow(
            oidc_config,
            DatabaseOIDCTransactionStore(database),
            database=database,
        )

    # Phase 27.2: domain routers (extracted from create_app).
    from app.routers.admin import build_router as build_admin_router
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
