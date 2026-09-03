from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


def _read_secrets_file(path: Path) -> str:
    """Read and validate a JSON secrets file (e.g. ``API_KEYS_FILE``)."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"secrets file is not readable: {path}") from exc
    try:
        json.loads(content or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"secrets file is not valid JSON: {path}") from exc
    return content


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    database_path: Path = Path("data/support.db")
    database_backend: str = "sqlite"
    database_url: str = ""
    # 42.2 HA/PITR: when False, app startup never executes DDL — the schema
    # must already be current (migrations run as a separate release job via
    # scripts/run_migrations.py) and startup only verifies readiness, so web
    # and worker processes can run with a least-privilege DB role that lacks
    # CREATE/ALTER.
    database_auto_migrate: bool = True
    openai_api_key: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_model: str = "gpt-4.1-mini"
    enable_llm: bool = False
    auto_escalate_threshold: float = 0.55
    auth_mode: str = "demo"
    demo_api_key: str = "helix-demo-key"
    api_keys_json: str = "{}"
    api_keys_file: Path | None = None
    rate_limit_per_minute: int = 120
    # Phase 28.4: independent, stricter limit for auth/sensitive endpoints
    # (login callback, refresh) to blunt brute-force and token-stuffing.
    auth_rate_limit_per_minute: int = 20
    # Phase 29.2: backpressure thresholds. When the per-tenant queued turn
    # depth (or global depth) exceeds these, new turn-jobs return 429 +
    # Retry-After instead of growing the backlog unboundedly.
    queue_depth_threshold: int = 1000
    tenant_concurrent_turn_cap: int = 50
    normal_sla_minutes: int = 120
    high_sla_minutes: int = 15
    idempotency_processing_timeout_seconds: int = 300
    turn_worker_enabled: bool = True
    turn_worker_concurrency: int = 1
    turn_job_poll_interval_ms: int = 200
    turn_job_lease_seconds: int = 300
    turn_job_max_attempts: int = 3
    turn_job_retry_base_seconds: int = 2
    turn_job_retention_days: int = 30
    turn_job_stream_enabled: bool = True
    turn_job_stream_pacing_ms: int = 10
    # ROADMAP 18.2c: SSE poll cadence for /api/turn-jobs/{id}/events. The
    # first poll happens immediately on connect, so this bounds the worst-case
    # TTFT transmission latency (lower = faster first token, more DB polls).
    turn_job_sse_poll_interval_ms: int = 100
    # Phase 20.5: cadence at which the turn worker emits SLA-breach events and
    # delivers due outbound webhook payloads (seconds).
    webhook_delivery_interval_seconds: int = 30
    claim_ttl_seconds: int = 900
    # ROADMAP 18.3: conversation archiving. The turn worker moves resolved
    # conversations closed more than ``conversation_archive_after_days`` ago
    # into the read-only archive tier, bounded per run by the batch size.
    conversation_archive_enabled: bool = True
    conversation_archive_after_days: int = 180
    conversation_archive_batch: int = 100
    conversation_archive_cadence_hours: int = 6
    database_pool_size: int = 4
    database_busy_timeout_ms: int = 5000
    knowledge_cache_ttl_seconds: int = 30
    dashboard_cache_ttl_seconds: int = 5
    cache_max_entries: int = 512
    cors_origins: tuple[str, ...] = ()
    docs_enabled: bool = True
    local_drafts_enabled: bool = True
    local_draft_ttl_minutes: int = 720
    queue_backend: str = "sqlite"
    redis_url: str = "redis://localhost:6379/0"
    enable_telemetry: bool = False
    enable_session_auth: bool = False
    # Phase 19.2: fraction of turns routed to the canary prompt version when a
    # canary is registered. 0 disables canary routing (active only); 1 sends
    # every eligible turn to canary. Bounded to [0, 1].
    prompt_canary_ratio: float = 0.0
    # Phase 23.1: shared secret for signing embeddable Web Chat customer
    # tokens. The widget endpoints verify every request against this secret.
    widget_secret: str = "helix-widget-dev-secret"
    # ROADMAP 17.3: CSP sources allowed to frame only the customer widget.
    # The operator workspace remains frame-ancestors 'none'.
    widget_frame_ancestors: tuple[str, ...] = ("'self'",)
    # Phase 38 / ROADMAP 23.3: signed inbound channel accounts. The file
    # source takes precedence so production secrets need not be placed in the
    # process environment or shell history.
    channel_webhooks_json: str = "{}"
    channel_webhooks_file: Path | None = None
    channel_webhook_replay_window_seconds: int = 300
    # Backlog (CSAT): public base URL prefix used to build absolute customer
    # survey links. When empty, resolution surfaces a relative path
    # (``/api/csat/{token}``); production should set this so emailed
    # survey links point at the right host.
    csat_base_url: str = ""
    # Backlog (多语言客服): the language the support team writes replies in.
    # Customer messages detected in another language get their replies
    # translated to that language when a model provider is configured.
    service_language: str = "zh"
    # Backlog (语音/富媒体消息): attachment storage and limits. Files live on
    # disk under ``attachment_storage_dir``; the per-file cap and the
    # per-tenant cumulative quota are enforced at upload time, and the
    # deterministic scanner (magic bytes + extension rules) runs unless
    # disabled.
    attachment_storage_dir: Path = Path("data/attachments")
    attachment_max_mb: int = 10
    attachment_quota_mb: int = 512
    attachment_scan_enabled: bool = True
    # 42.4 SEC-006: "sync" scans inline at upload (legacy behaviour);
    # "external" parks uploads in quarantine until an AV/CDR engine posts its
    # verdict — quarantined objects are neither downloadable nor bindable.
    attachment_scan_mode: str = "sync"
    # M0 SEC-002: source secret for DSR export-object encryption. Exports fail
    # closed (501) when unset; deployments must provide a long random value.
    dsr_export_secret: str = ""
    # Phase 41.4 (DATA): DSR SLA — requests must complete within this many
    # minutes of creation or they are flagged ``sla_breached`` on the queue.
    dsr_sla_minutes: int = 120
    # Phase 41.4 (DATA): tombstone-after-restore on startup. ``true`` re-applies
    # every recorded DSR deletion before the API serves requests, so a restored
    # old backup can never resurrect erased customer data.
    enable_data_protection: bool = True
    # M0 REL-001: deployment shape. ``local`` is a single-process dev box,
    # ``single`` a single-node production instance, ``multi`` a replicated
    # deployment (requires postgresql + redis + fail-closed queue).
    deployment_profile: str = "single"
    # M0 REL-001: what happens when the task queue backend is unreachable.
    # ``fallback`` (default) degrades to the SQLite queue with a warning;
    # ``fail_closed`` refuses to enqueue (503 + Retry-After) and never starts
    # the worker until the queue is reachable again.
    queue_failure_mode: str = "fallback"
    # Phase 42.1 / REL-001 split: which runtime role this process plays.
    # ``all`` (default, single-process dev/legacy) serves the HTTP API and runs
    # the turn worker + housekeeping in one process. ``web`` only serves the API
    # and never starts the worker/housekeeping loop (scale stateless web tier
    # without adding workers). ``worker`` still boots the app for assembly but
    # the public endpoint surface is a deploy-time network concern (cf. docs).
    process_role: str = "all"
    # Phase 41.3 / SEC-005: external audit anchoring. The turn worker signs
    # the audit-chain tip with an Ed25519 KMS stand-in and exports the claim
    # to WORM/object-lock storage on ``audit_anchor_cadence_hours``.  WORM is
    # a disk directory in development, an object-lock bucket in production.
    # 42.3 REL-002: cold-tier archive payloads (audit-retention archives)
    # are written as compressed partitions under this root; the database
    # keeps only slim manifest references. Swap the disk backend for S3/GCS
    # in production by pointing this at the mounted bucket.
    archive_object_dir: Path = Path("data/archive-objects")
    # 43.1: HMAC key for signed tenant configuration snapshots. Empty falls
    # back to the widget secret so development works out of the box.
    control_plane_secret: str = ""
    audit_anchor_enabled: bool = True
    audit_anchor_environment: str = "development"
    audit_anchor_key: str = ""  # base64 Ed25519 private key; empty => generated
    audit_anchor_cadence_hours: int = 24
    audit_anchor_batch: int = 1
    audit_worm_dir: Path = Path("data/anchors")
    # Phase 41.3 / SEC-005: trusted kids for anchor verification (comma list of
    # ``ed25519-<sha256[:12]>`` identifiers).  Empty trusts every embedded key
    # (dev default); production pins the KMS key identities here so a rotated
    # key does not silently replace the signer.
    audit_trusted_kids: tuple[str, ...] = ()
    # Phase 41.5 / AI-001: AI safety evaluation gate. Adversarial and golden
    # evaluation runs write an immutable report to ``eval_worm_dir`` (WORM
    # disk stand-in), and a prompt/model can only promote into canary routing
    # when the report clears the floors below; otherwise the canary promotion
    # is automatically blocked (see ADR-014).
    eval_worm_dir: Path = Path("data/eval-reports")
    eval_environment: str = "development"
    # Adverse-set pass-rate floor (adversarial safety set must be 100%).
    eval_adversarial_floor: float = 1.0
    # Core golden-set pass-rate floor.
    eval_golden_floor: float = 1.0
    # P95 latency budget in milliseconds for a single-turn request under eval.
    eval_p95_budget_ms: float = 2000.0
    # Estimated cost budget in USD for an eval run. The deterministic path
    # costs nothing, so the default (0.0) passes; reserved for live-model
    # providers.
    eval_cost_budget_usd: float = 0.0
    # Phase 41.5 / AI-001: write operations are denied by the tool gateway
    # unless explicitly confirmed by an operator (default human confirmation).
    require_write_confirmation: bool = True
    # Phase 43.2 / ROADMAP 43.2: per-tenant envelope encryption. The KMS
    # stand-in keeps versioned KEK files under ``envelope_kms_dir`` (swap for a
    # cloud-KMS adapter in production); ``envelope_enabled`` lets a deployment
    # opt out of assembling the cipher entirely (the API then reports 501 on
    # restricted-field operations rather than silently storing plaintext).
    # ``envelope_rewrap_cadence_hours`` is how often the turn-worker housekeeping
    # sweep re-wraps stored tenant DEKs onto the active KEK after a rotation.
    envelope_enabled: bool = True
    envelope_kms_dir: Path = Path("data/kms")
    envelope_rewrap_cadence_hours: int = 24
    # Phase 43.2 contract (a): PostgreSQL row-level tenant isolation. When
    # true, application connections bind the ambient tenant scope into a
    # transaction-scoped GUC (``app.tenant_id``) and fail closed when a code
    # path reaches the database with no scope at all. Requires the
    # postgresql backend; the RLS policies themselves are installed by the
    # migration release job / initialize() as the owner role (see app/rls.py).
    database_rls_enabled: bool = False
    # ROADMAP 43.5 item 4: online drift monitoring. The monitor compares
    # quality buckets and audit denial counters against the thresholds below;
    # a breach stops the affected canary (clears it back to draft) and audits
    # ``ai.drift_canary_stopped``. ``drift_enabled=False`` (the default)
    # disables the housekeeping sweep entirely — existing deployments are
    # unaffected until they opt in.
    drift_enabled: bool = False
    # Look-back window for signal collection, in days (quality buckets are
    # daily; denials are counted over created_at >= now - window).
    drift_window_days: int = 1
    # Minimum turns in the window before a rate-based signal may fire — a
    # 2-turn sample must not stop a rollout.
    drift_min_turns: int = 20
    # Escalation-rate ceiling (0 < x <= 1); None disables this signal.
    drift_max_escalation_rate: float | None = 0.6
    # Negative-feedback-rate ceiling (0 < x <= 1); None disables this signal.
    drift_max_negative_rate: float | None = 0.5
    # Absolute refusal counts per window (turn.model_denied /
    # turn.budget_exceeded / tool.denied); None disables that counter.
    drift_max_model_denials: int | None = 50
    drift_max_tool_denials: int | None = 50
    # ROADMAP 2.1.x: shadow traffic opt-in. When true, sampled v1 requests are
    # replayed to v2 endpoints; results are compared and logged to
    # shadow_traffic_comparisons for automated monitoring. The sampling rate
    # controls overhead (0.0 = disabled, 1.0 = shadow every request).
    shadow_traffic_enabled: bool = False
    shadow_traffic_sample_rate: float = 0.05

    @classmethod
    def from_env(cls) -> Settings:
        origins = tuple(
            origin.strip() for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin.strip()
        )
        # An unset variable keeps the "'self'" default; an explicitly empty
        # value parses to an empty tuple and fails validation in __post_init__
        # instead of silently reverting to the default.
        widget_frame_ancestors_env = os.getenv("WIDGET_FRAME_ANCESTORS")
        if widget_frame_ancestors_env is None:
            widget_frame_ancestors: tuple[str, ...] = ("'self'",)
        else:
            widget_frame_ancestors = tuple(
                "'self'" if source.strip() in {"self", "'self'"} else source.strip()
                for source in widget_frame_ancestors_env.split(",")
                if source.strip()
            )
        api_keys_file_raw = os.getenv("API_KEYS_FILE")
        channel_webhooks_file_raw = os.getenv("CHANNEL_WEBHOOKS_FILE")
        deployment_profile = os.getenv("DEPLOYMENT_PROFILE", "single").strip().lower()
        process_role = os.getenv("PROCESS_ROLE", "all").strip().lower()
        settings = cls(
            app_env=os.getenv("APP_ENV", "development").strip().lower(),
            database_path=Path(os.getenv("DATABASE_PATH", "data/support.db")),
            database_backend=os.getenv("DATABASE_BACKEND", "sqlite").strip().lower(),
            database_url=os.getenv("DATABASE_URL", ""),
            database_auto_migrate=_env_bool("DATABASE_AUTO_MIGRATE", True),
            openai_api_key=os.getenv("OPENAI_API_KEY") or None,
            openai_base_url=os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/"),
            openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            enable_llm=_env_bool("ENABLE_LLM", False),
            auto_escalate_threshold=float(os.getenv("AUTO_ESCALATE_THRESHOLD", "0.55")),
            auth_mode=os.getenv("AUTH_MODE", "demo").strip().lower(),
            demo_api_key=os.getenv("DEMO_API_KEY", "helix-demo-key"),
            api_keys_json=os.getenv("API_KEYS_JSON", "{}"),
            api_keys_file=Path(api_keys_file_raw) if api_keys_file_raw else None,
            rate_limit_per_minute=_env_int("RATE_LIMIT_PER_MINUTE", 120),
            auth_rate_limit_per_minute=_env_int("AUTH_RATE_LIMIT_PER_MINUTE", 20),
            queue_depth_threshold=_env_int("QUEUE_DEPTH_THRESHOLD", 1000),
            tenant_concurrent_turn_cap=_env_int("TENANT_CONCURRENT_TURN_CAP", 50),
            normal_sla_minutes=_env_int("NORMAL_SLA_MINUTES", 120),
            high_sla_minutes=_env_int("HIGH_SLA_MINUTES", 15),
            idempotency_processing_timeout_seconds=_env_int(
                "IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS", 300
            ),
            turn_worker_enabled=_env_bool("TURN_WORKER_ENABLED", True),
            turn_worker_concurrency=_env_int("TURN_WORKER_CONCURRENCY", 1),
            turn_job_poll_interval_ms=_env_int("TURN_JOB_POLL_INTERVAL_MS", 200),
            turn_job_lease_seconds=_env_int("TURN_JOB_LEASE_SECONDS", 300),
            turn_job_max_attempts=_env_int("TURN_JOB_MAX_ATTEMPTS", 3),
            turn_job_retry_base_seconds=_env_int("TURN_JOB_RETRY_BASE_SECONDS", 2),
            turn_job_retention_days=_env_int("TURN_JOB_RETENTION_DAYS", 30),
            turn_job_stream_enabled=_env_bool("TURN_JOB_STREAM_ENABLED", True),
            turn_job_stream_pacing_ms=_env_int("TURN_JOB_STREAM_PACING_MS", 10),
            turn_job_sse_poll_interval_ms=_env_int("TURN_JOB_SSE_POLL_INTERVAL_MS", 100),
            webhook_delivery_interval_seconds=_env_int("WEBHOOK_DELIVERY_INTERVAL_SECONDS", 30),
            claim_ttl_seconds=_env_int("CLAIM_TTL_SECONDS", 900),
            conversation_archive_enabled=_env_bool("CONVERSATION_ARCHIVE_ENABLED", True),
            conversation_archive_after_days=_env_int("CONVERSATION_ARCHIVE_AFTER_DAYS", 180),
            conversation_archive_batch=_env_int("CONVERSATION_ARCHIVE_BATCH", 100),
            conversation_archive_cadence_hours=_env_int("CONVERSATION_ARCHIVE_CADENCE_HOURS", 6),
            database_pool_size=_env_int("DATABASE_POOL_SIZE", 4),
            database_busy_timeout_ms=_env_int("DATABASE_BUSY_TIMEOUT_MS", 5000),
            knowledge_cache_ttl_seconds=_env_int("KNOWLEDGE_CACHE_TTL_SECONDS", 30),
            dashboard_cache_ttl_seconds=_env_int("DASHBOARD_CACHE_TTL_SECONDS", 5),
            cache_max_entries=_env_int("CACHE_MAX_ENTRIES", 512),
            cors_origins=origins,
            docs_enabled=_env_bool("DOCS_ENABLED", True),
            local_drafts_enabled=_env_bool("LOCAL_DRAFTS_ENABLED", True),
            local_draft_ttl_minutes=_env_int("LOCAL_DRAFT_TTL_MINUTES", 720),
            queue_backend=os.getenv("QUEUE_BACKEND", "sqlite").strip().lower(),
            redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
            enable_telemetry=_env_bool("ENABLE_TELEMETRY", False),
            enable_session_auth=_env_bool("ENABLE_SESSION_AUTH", False),
            prompt_canary_ratio=float(os.getenv("PROMPT_CANARY_RATIO", "0.0")),
            widget_secret=os.getenv("WIDGET_SECRET", "helix-widget-dev-secret"),
            widget_frame_ancestors=widget_frame_ancestors,
            channel_webhooks_json=os.getenv("CHANNEL_WEBHOOKS_JSON", "{}"),
            channel_webhooks_file=(
                Path(channel_webhooks_file_raw) if channel_webhooks_file_raw else None
            ),
            channel_webhook_replay_window_seconds=_env_int(
                "CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS", 300
            ),
            csat_base_url=os.getenv("CSAT_BASE_URL", "").rstrip("/"),
            service_language=os.getenv("SERVICE_LANGUAGE", "zh").strip().lower(),
            attachment_storage_dir=Path(os.getenv("ATTACHMENT_STORAGE_DIR", "data/attachments")),
            attachment_max_mb=_env_int("ATTACHMENT_MAX_MB", 10),
            attachment_quota_mb=_env_int("ATTACHMENT_QUOTA_MB", 512),
            attachment_scan_enabled=_env_bool("ATTACHMENT_SCAN_ENABLED", True),
            attachment_scan_mode=os.getenv("ATTACHMENT_SCAN_MODE", "sync").strip().lower(),
            dsr_export_secret=os.getenv("DSR_EXPORT_SECRET", ""),
            dsr_sla_minutes=_env_int("DSR_SLA_MINUTES", 120),
            enable_data_protection=_env_bool("ENABLE_DATA_PROTECTION", True),
            deployment_profile=deployment_profile,
            queue_failure_mode=os.getenv(
                "QUEUE_FAILURE_MODE", "fail_closed" if deployment_profile == "multi" else "fallback"
            )
            .strip()
            .lower(),
            process_role=process_role,
            audit_anchor_enabled=_env_bool("AUDIT_ANCHOR_ENABLED", True),
            audit_anchor_environment=os.getenv("AUDIT_ANCHOR_ENVIRONMENT", "development").strip(),
            audit_anchor_key=os.getenv("AUDIT_ANCHOR_KEY", ""),
            audit_anchor_cadence_hours=_env_int("AUDIT_ANCHOR_CADENCE_HOURS", 24),
            audit_anchor_batch=_env_int("AUDIT_ANCHOR_BATCH", 1),
            audit_worm_dir=Path(os.getenv("AUDIT_WORM_DIR", "data/anchors")),
            archive_object_dir=Path(os.getenv("ARCHIVE_OBJECT_DIR", "data/archive-objects")),
            control_plane_secret=os.getenv("CONTROL_PLANE_SECRET", ""),
            audit_trusted_kids=tuple(
                kid.strip() for kid in os.getenv("AUDIT_TRUSTED_KIDS", "").split(",") if kid.strip()
            ),
            eval_worm_dir=Path(os.getenv("EVAL_WORM_DIR", "data/eval-reports")),
            eval_environment=os.getenv("EVAL_ENVIRONMENT", "development").strip(),
            eval_adversarial_floor=float(os.getenv("EVAL_ADVERSARIAL_FLOOR", "1.0")),
            eval_golden_floor=float(os.getenv("EVAL_GOLDEN_FLOOR", "1.0")),
            eval_p95_budget_ms=float(os.getenv("EVAL_P95_BUDGET_MS", "2000.0")),
            eval_cost_budget_usd=float(os.getenv("EVAL_COST_BUDGET_USD", "0.0")),
            require_write_confirmation=_env_bool("REQUIRE_WRITE_CONFIRMATION", True),
            envelope_enabled=_env_bool("ENVELOPE_ENABLED", True),
            envelope_kms_dir=Path(os.getenv("ENVELOPE_KMS_DIR", "data/kms")),
            envelope_rewrap_cadence_hours=_env_int("ENVELOPE_REWRAP_CADENCE_HOURS", 24),
            database_rls_enabled=_env_bool("DATABASE_RLS_ENABLED", False),
            drift_enabled=_env_bool("DRIFT_ENABLED", False),
            drift_window_days=_env_int("DRIFT_WINDOW_DAYS", 1),
            drift_min_turns=_env_int("DRIFT_MIN_TURNS", 20),
            drift_max_escalation_rate=(
                float(os.environ["DRIFT_MAX_ESCALATION_RATE"])
                if os.getenv("DRIFT_MAX_ESCALATION_RATE")
                else None
            ),
            drift_max_negative_rate=(
                float(os.environ["DRIFT_MAX_NEGATIVE_RATE"])
                if os.getenv("DRIFT_MAX_NEGATIVE_RATE")
                else None
            ),
            drift_max_model_denials=(
                int(os.environ["DRIFT_MAX_MODEL_DENIALS"])
                if os.getenv("DRIFT_MAX_MODEL_DENIALS")
                else None
            ),
            drift_max_tool_denials=(
                int(os.environ["DRIFT_MAX_TOOL_DENIALS"])
                if os.getenv("DRIFT_MAX_TOOL_DENIALS")
                else None
            ),
            shadow_traffic_enabled=_env_bool("SHADOW_TRAFFIC_ENABLED", False),
            shadow_traffic_sample_rate=float(os.getenv("SHADOW_TRAFFIC_SAMPLE_RATE", "0.05")),
        )
        settings.validate()
        return settings

    @property
    def is_production(self) -> bool:
        return self.app_env in {"prod", "production"}

    @property
    def runs_turn_worker(self) -> bool:
        """Whether this process should run the turn worker + housekeeping.

        ``web`` is a stateless API tier: it never starts the worker loop, so
        scaling the web fleet does not silently add workers (ROADMAP §42.1).
        ``worker``/``all`` run the worker alongside the app assembly.
        """
        return self.process_role != "web"

    @property
    def effective_api_keys_json(self) -> str:
        """Resolved API-key configuration.

        ``API_KEYS_FILE`` points at a gitignored JSON secrets file and takes
        precedence over ``API_KEYS_JSON``, so production keys do not have to
        live in the process environment or shell history.  The file is read and
        validated on each access, so a rotated secret file is picked up at
        restart without a config change.
        """
        if self.api_keys_file is not None:
            return _read_secrets_file(self.api_keys_file)
        return self.api_keys_json

    @property
    def effective_channel_webhooks_json(self) -> str:
        if self.channel_webhooks_file is not None:
            return _read_secrets_file(self.channel_webhooks_file)
        return self.channel_webhooks_json

    def validate(self) -> None:
        if self.auth_mode not in {"demo", "api_key"}:
            raise ValueError("AUTH_MODE must be 'demo' or 'api_key'")
        if not 0 < self.auto_escalate_threshold <= 1:
            raise ValueError("AUTO_ESCALATE_THRESHOLD must be in (0, 1]")
        if self.rate_limit_per_minute < 1:
            raise ValueError("RATE_LIMIT_PER_MINUTE must be positive")
        if self.normal_sla_minutes < 1 or self.high_sla_minutes < 1:
            raise ValueError("SLA minute values must be positive")
        if self.idempotency_processing_timeout_seconds < 30:
            raise ValueError("IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS must be at least 30")
        if not 1 <= self.turn_worker_concurrency <= 8:
            raise ValueError("TURN_WORKER_CONCURRENCY must be between 1 and 8")
        if self.turn_job_poll_interval_ms < 10:
            raise ValueError("TURN_JOB_POLL_INTERVAL_MS must be at least 10")
        if self.turn_job_lease_seconds < self.idempotency_processing_timeout_seconds:
            raise ValueError(
                "TURN_JOB_LEASE_SECONDS must be at least IDEMPOTENCY_PROCESSING_TIMEOUT_SECONDS"
            )
        if not 1 <= self.turn_job_max_attempts <= 10:
            raise ValueError("TURN_JOB_MAX_ATTEMPTS must be between 1 and 10")
        if not 0 <= self.turn_job_retry_base_seconds <= 300:
            raise ValueError("TURN_JOB_RETRY_BASE_SECONDS must be between 0 and 300")
        if not 1 <= self.turn_job_retention_days <= 3650:
            raise ValueError("TURN_JOB_RETENTION_DAYS must be between 1 and 3650")
        if self.turn_job_stream_pacing_ms < 0:
            raise ValueError("TURN_JOB_STREAM_PACING_MS cannot be negative")
        if self.turn_job_sse_poll_interval_ms < 10:
            raise ValueError("TURN_JOB_SSE_POLL_INTERVAL_MS must be at least 10")
        if not 60 <= self.claim_ttl_seconds <= 86400:
            raise ValueError("CLAIM_TTL_SECONDS must be between 60 and 86400")
        if self.database_pool_size < 1:
            raise ValueError("DATABASE_POOL_SIZE must be positive")
        if self.database_busy_timeout_ms < 100:
            raise ValueError("DATABASE_BUSY_TIMEOUT_MS must be at least 100")
        if self.knowledge_cache_ttl_seconds < 1 or self.dashboard_cache_ttl_seconds < 1:
            raise ValueError("Cache TTL values must be positive")
        if self.cache_max_entries < 1:
            raise ValueError("CACHE_MAX_ENTRIES must be positive")
        if not 1 <= self.local_draft_ttl_minutes <= 10080:
            raise ValueError("LOCAL_DRAFT_TTL_MINUTES must be between 1 and 10080")
        if self.queue_backend not in {"sqlite", "redis"}:
            raise ValueError("QUEUE_BACKEND must be 'sqlite' or 'redis'")
        if self.attachment_scan_mode not in {"sync", "external"}:
            raise ValueError("ATTACHMENT_SCAN_MODE must be 'sync' or 'external'")
        if self.queue_backend == "redis" and not self.redis_url:
            raise ValueError("REDIS_URL is required when QUEUE_BACKEND is redis")
        if self.deployment_profile not in {"local", "single", "multi"}:
            raise ValueError("DEPLOYMENT_PROFILE must be 'local', 'single' or 'multi'")
        if self.queue_failure_mode not in {"fallback", "fail_closed"}:
            raise ValueError("QUEUE_FAILURE_MODE must be 'fallback' or 'fail_closed'")
        if self.process_role not in {"web", "worker", "all"}:
            raise ValueError("PROCESS_ROLE must be 'web', 'worker' or 'all'")
        if self.deployment_profile == "multi":
            if self.database_backend != "postgresql":
                raise ValueError("DEPLOYMENT_PROFILE=multi requires DATABASE_BACKEND=postgresql")
            if self.queue_backend != "redis":
                raise ValueError("DEPLOYMENT_PROFILE=multi requires QUEUE_BACKEND=redis")
            if self.queue_failure_mode != "fail_closed":
                raise ValueError("DEPLOYMENT_PROFILE=multi requires QUEUE_FAILURE_MODE=fail_closed")
        if self.audit_anchor_cadence_hours < 1:
            raise ValueError("AUDIT_ANCHOR_CADENCE_HOURS must be at least 1")
        if self.audit_anchor_batch < 1:
            raise ValueError("AUDIT_ANCHOR_BATCH must be positive")
        for kid in self.audit_trusted_kids:
            if not kid.startswith("ed25519-") or len(kid) != len("ed25519-") + 12:
                raise ValueError(
                    "AUDIT_TRUSTED_KIDS entries must be ed25519-<12 hex prefix> identifiers"
                )
        if not 0 < self.eval_adversarial_floor <= 1:
            raise ValueError("EVAL_ADVERSARIAL_FLOOR must be in (0, 1]")
        if not 0 < self.eval_golden_floor <= 1:
            raise ValueError("EVAL_GOLDEN_FLOOR must be in (0, 1]")
        if self.eval_p95_budget_ms < 0:
            raise ValueError("EVAL_P95_BUDGET_MS cannot be negative")
        if self.eval_cost_budget_usd < 0:
            raise ValueError("EVAL_COST_BUDGET_USD cannot be negative")
        if self.database_backend not in {"sqlite", "postgresql"}:
            raise ValueError("DATABASE_BACKEND must be 'sqlite' or 'postgresql'")
        if self.database_backend == "postgresql" and not self.database_url:
            raise ValueError("DATABASE_URL is required when DATABASE_BACKEND is postgresql")
        if self.database_rls_enabled and self.database_backend != "postgresql":
            raise ValueError("DATABASE_RLS_ENABLED requires DATABASE_BACKEND=postgresql")
        if self.enable_llm and not self.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required when ENABLE_LLM is true")
        if not 1 <= self.envelope_rewrap_cadence_hours <= 8760:
            raise ValueError("ENVELOPE_REWRAP_CADENCE_HOURS must be between 1 and 8760")
        if self.drift_window_days < 1:
            raise ValueError("DRIFT_WINDOW_DAYS must be at least 1")
        if self.drift_min_turns < 1:
            raise ValueError("DRIFT_MIN_TURNS must be at least 1")
        for name, rate in (
            ("DRIFT_MAX_ESCALATION_RATE", self.drift_max_escalation_rate),
            ("DRIFT_MAX_NEGATIVE_RATE", self.drift_max_negative_rate),
        ):
            if rate is not None and not 0 < rate <= 1:
                raise ValueError(f"{name} must be in (0, 1] or unset")
        for name, count in (
            ("DRIFT_MAX_MODEL_DENIALS", self.drift_max_model_denials),
            ("DRIFT_MAX_TOOL_DENIALS", self.drift_max_tool_denials),
        ):
            if count is not None and count < 1:
                raise ValueError(f"{name} must be at least 1 or unset")
        if not 1 <= self.drift_window_days <= 90:
            raise ValueError("DRIFT_WINDOW_DAYS must be between 1 and 90")
        if self.is_production and self.auth_mode != "api_key":
            raise ValueError("AUTH_MODE=api_key is required in production")
        if self.api_keys_file is not None:
            # Fails fast on an unreadable or malformed secrets file.
            _read_secrets_file(self.api_keys_file)
        if self.channel_webhooks_file is not None:
            _read_secrets_file(self.channel_webhooks_file)
        if self.is_production and self.effective_api_keys_json.strip() in {"", "{}"}:
            raise ValueError("API keys must configure at least one principal in production")
        if not 0 <= self.prompt_canary_ratio <= 1:
            raise ValueError("PROMPT_CANARY_RATIO must be between 0 and 1")
        if not 5 <= self.webhook_delivery_interval_seconds <= 3600:
            raise ValueError("WEBHOOK_DELIVERY_INTERVAL_SECONDS must be between 5 and 3600")
        if not 30 <= self.channel_webhook_replay_window_seconds <= 3600:
            raise ValueError("CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS must be between 30 and 3600")
        if self.conversation_archive_after_days < 7:
            raise ValueError("CONVERSATION_ARCHIVE_AFTER_DAYS must be at least 7")
        if self.conversation_archive_batch < 1:
            raise ValueError("CONVERSATION_ARCHIVE_BATCH must be positive")
        if self.conversation_archive_cadence_hours < 1:
            raise ValueError("CONVERSATION_ARCHIVE_CADENCE_HOURS must be at least 1")
        if not self.widget_frame_ancestors:
            raise ValueError("WIDGET_FRAME_ANCESTORS must contain at least one source")
        for source in self.widget_frame_ancestors:
            if source == "'self'":
                continue
            if any(char.isspace() for char in source) or any(char in source for char in ";,\"'\\"):
                raise ValueError("WIDGET_FRAME_ANCESTORS entries contain invalid characters")
            parsed = urlsplit(source)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError(
                    "WIDGET_FRAME_ANCESTORS entries must be 'self' or absolute http(s) origins"
                )
            if parsed.username is not None or parsed.password is not None:
                raise ValueError("WIDGET_FRAME_ANCESTORS entries must not include user info")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("WIDGET_FRAME_ANCESTORS entries contain an invalid port") from exc
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("WIDGET_FRAME_ANCESTORS entries must not include paths")
            if self.is_production and "*" in source:
                raise ValueError("WIDGET_FRAME_ANCESTORS cannot contain wildcards in production")
