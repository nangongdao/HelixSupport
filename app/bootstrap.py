"""Application assembly (Phase 27.2 home).

The 2.x era accreted ~390 lines of service construction onto
``create_app`` — database bootstrap, the credential registry, the
orchestrator/worker/cost-attribution core, audit anchoring, envelope
encryption, the outbox consumer, attachment/archive stores, retention,
the control plane, drift/shadow monitors and the cell registry.
:func:`build_application` runs that assembly verbatim, in the original
order (the housekeeping closures still bind ``services`` late, exactly
as when they closed over the not-yet-assigned local), and returns an
:class:`ApplicationContext`. ``create_app`` keeps only the startup
guards, the FastAPI instance, lifespan wiring and routing.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.anchor_service import AnchorService
from app.audit_anchor import Ed25519KmsSigner
from app.ai_governance import AiGovernanceService
from app.audit_gap import AuditGapTracker
from app.channel_webhooks import InboundChannelRegistry
from app.config import Settings
from app.database import Database
from app.db._util import utc_now
from app.jobs import TurnJobWorker
from app.queue import TaskQueue, create_task_queue
from app.model_provider import OpenAICompatibleProvider
from app.observability import RuntimeMetrics
from app.oidc_flow import DatabaseOIDCTransactionStore, OIDCFlow
from app.orchestrator import ConversationOrchestrator
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.retention import RetentionService
from app.session_auth import OIDCAuthenticator, OIDCConfig
from app.security import (
    Authenticator,
    SlidingWindowRateLimiter,
)
from app.webhooks import WebhookService

logger = logging.getLogger("helix")


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
    ai_governance: Any | None = None
    cell_registry: Any | None = None
    cost_attribution: Any | None = None


@dataclass(frozen=True)
class ApplicationContext:
    """Everything create_app wires into the FastAPI instance."""

    database: Any
    queue: Any
    provider: Any
    authenticator: Any
    orchestrator: Any
    turn_worker: Any
    webhook_service: Any
    services: Any
    cell_registry: Any
    oidc_config: Any
    oidc_authenticator: Any
    oidc_flow: Any


def build_application(settings: Settings) -> ApplicationContext:
    """Assemble every infrastructure service; see module docstring."""
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

    # ROADMAP 2.3.x: inference cost attribution — per-inference cost rows and
    # daily tenant/provider/model aggregates for the cost dashboard API.
    from app.cost_attribution import CostAttributionService

    cost_attribution_service = CostAttributionService(database)

    # ROADMAP 2.5.0: the AI governance registry reaches production here —
    # it powers high-risk tool approvals in the gateway, and the capability
    # secret turns on token verification for gateway-mediated tool calls.
    ai_governance = AiGovernanceService(database)
    if settings.is_production and not settings.capability_secret:
        logger.warning(
            "capability_secret.unset: tool capability tokens are not verified; "
            "set CAPABILITY_SECRET to enable the full tool delegation chain"
        )
    orchestrator = ConversationOrchestrator(
        database,
        settings,
        provider,
        queue=queue,
        webhook_service=webhook_service,
        cost_attribution=cost_attribution_service,
        capability_secret=settings.capability_secret,
        governance_service=ai_governance,
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

    # OIDC / BFF session authentication (M0 SEC-001): the verified
    # authorization-code flow (one-time transactions, PKCE, JWKS signature
    # validation, roster-based identity mapping). Constructed here so the
    # housekeeping closure below binds a name this scope actually assigns
    # (in create_app it bound a late local assigned further down).
    oidc_config = OIDCConfig.from_env() if settings.enable_session_auth else None
    oidc_authenticator = OIDCAuthenticator(oidc_config) if oidc_config else None
    oidc_flow = None
    if oidc_config:
        oidc_flow = OIDCFlow(
            oidc_config,
            DatabaseOIDCTransactionStore(database),
            database=database,
        )

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
    # SEC-005: the anchor signing key must survive restarts, otherwise the kid
    # rotates on every boot and previously written anchors stop verifying.
    # AUDIT_ANCHOR_KEY is an optional base64-encoded raw Ed25519 private key;
    # when unset (dev), a fresh key is generated per run.
    if settings.audit_anchor_key:
        anchor_signer = Ed25519KmsSigner.from_encoded(settings.audit_anchor_key)
    else:
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

    drift_monitor = DriftMonitor(database, settings, cost_service=cost_attribution_service)

    def _housekeeping_drift_monitor() -> None:
        reports = drift_monitor.run_once()
        if reports:
            logger.warning(
                "drift.canaries_stopped %s",
                [{"tenant": r.tenant_id, "signals": [s.kind for s in r.signals]} for r in reports],
            )

    if settings.drift_enabled:
        turn_worker.housekeeping.extend([_housekeeping_drift_monitor])

    # ROADMAP 2.1.x: shadow traffic health monitor. When shadow traffic is
    # enabled, the hourly housekeeping sweep aggregates v1/v2 comparisons over
    # a 24-hour window and logs an alert when mismatch rate or latency
    # regressions breach thresholds. Failures are logged by the worker, never
    # fatal.
    from app.shadow_monitor import monitor_shadow_traffic

    def _housekeeping_shadow_monitor() -> None:
        try:
            monitor_shadow_traffic(database, settings)
        except Exception:
            logger.exception("shadow.monitor_failed")

    if settings.shadow_traffic_enabled:
        turn_worker.housekeeping.extend([_housekeeping_shadow_monitor])

    # ROADMAP 2.2.1: multi-cell registry. When cell_registry_config is set,
    # build a CellRegistry from the config dict and start periodic health checks.
    from app.cell_router import CellRegistry, CellSpec

    cell_registry: CellRegistry | None = None
    if settings.cell_registry_config:
        cells = {
            cell_id: CellSpec(
                cell_id=cell_id,
                db_url=spec["db_url"],
                redis_url=spec["redis_url"],
                health_url=spec["health_url"],
                region=spec["region"],
                capacity_tier=spec.get("capacity_tier", "default"),
            )
            for cell_id, spec in settings.cell_registry_config.items()
        }
        cell_registry = CellRegistry(cells)

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
        copilot=CopilotService(
            database, provider, orchestrator.languages, cost_attribution_service
        ),
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
        cell_registry=cell_registry,
        cost_attribution=cost_attribution_service,
        ai_governance=ai_governance,
    )

    return ApplicationContext(
        database=database,
        queue=queue,
        provider=provider,
        authenticator=authenticator,
        orchestrator=orchestrator,
        turn_worker=turn_worker,
        webhook_service=webhook_service,
        services=services,
        cell_registry=cell_registry,
        oidc_config=oidc_config,
        oidc_authenticator=oidc_authenticator,
        oidc_flow=oidc_flow,
    )
