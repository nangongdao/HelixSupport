"""Versioned database migration framework.

Replaces the ad-hoc ``_ensure_column`` pattern with explicit, numbered
migration steps that are tracked in a ``schema_migrations`` table.

For databases already created by the legacy ``Database.initialize()``,
the runner detects the existing schema and marks all baseline migrations
as applied, so the transition is seamless.

Phase 41.6 (ARC-001) deep-module split: each numbered migration lives in
its own ``v{N:02d}_*.py`` module inside this package; the registry
infrastructure (decorator, runner, verification) stays here. The version
modules are imported at the bottom of this file so every migration is
registered exactly once.

Migration inventory (matching the 1.0/1.1 roadmap):
  1  - full baseline schema (idempotent; the legacy ``initialize()``
       executescript creates the same objects, so this is a no-op there)
  2  - ``conversations.sla_due_at`` (legacy column supplement)
  3  - ``conversations.labels_json`` (legacy column supplement)
  4  - ``conversations.first_response_at`` + monotonic ``seq`` columns and
       backfill for messages/audit_events (Phase 16/17)
  5  - ``turn_job_chunks`` streaming table (Phase 18)
  6  - ``prompt_versions`` registry (Phase 19.1)
  7  - tenant model policy columns + ``tenant_usage_daily`` (Phase 19.4)
  8  - outbound webhook endpoints/deliveries (Phase 20.5)
  9  - ``quality_daily`` aggregates + knowledge lifecycle columns (Phase 21)
  10 - tenant members + quota/metering columns (Phase 22.2/22.4)
  11 - ``messages.channel_message_id`` web-chat idempotency (Phase 23.2)
  12 - audit hash chain columns + backfill (Phase 28.3)
  13 - ``revoked_api_keys`` (Phase 28.2)
  14 - ``csat_surveys`` satisfaction surveys (backlog)
  15 - auto-routing: ``agent_groups``/``routing_rules`` (backlog)
  16 - ``sla_policies`` policy engine (backlog)
  17 - ``conversation_summaries`` operator summaries (backlog)
  18 - ``conversation_mentions`` + ``messages.reply_to`` (backlog: 坐席协作)
  19 - ``consumer_agents``/queues and tenant backpressure columns (backlog)
  20 - outbound report subscriptions (backlog: 报表导出与订阅)
  21 - SLA policy engine tables (backlog)
  22 - rich-media attachment registry (backlog: 语音/富媒体消息)
  23 - message pagination ``seq`` index (ROADMAP 18.2d)
  24 - conversation archive cold tier (ROADMAP 18.3)
  25 - CSAT summary partial index (backlog optimization)
  26 - durable audit retention archives (ROADMAP 18.3)
  27 - signed inbound channel threads, receipts, and async message ids (Phase 38)
  28 - one-time OIDC authorization transactions (M0 SEC-001)
  29 - DSR maker-checker workflow and encrypted exports (M0 SEC-002)
  30 - unified credential lifecycle registry (Phase 41 SEC-004)
  31 - external audit anchors + observable audit gaps (Phase 41.3 SEC-005)
  32 - data field registry + DSR SLA + deletion tombstones (Phase 41.4 DATA)
  33 - audit archive object store (Phase 42.3 REL-002)
  34 - attachment checksum columns (Phase 42.4 SEC-006)
  35 - turn job request context (Phase 42.6 observability v2)
  36 - tenant control plane policy versions (Phase 43.1)
  37 - domain event outbox + api idempotency keys (Phase 43.3)
  38 - per-tenant wrapped data-encryption keys (Phase 43.2)
  39 - webhook endpoint secret format discriminator (Phase 43.2)
  40 - tenant residency column (ROADMAP 43.4)
  41 - AI governance registry: eval datasets/runs, approvals, online
       feedback review (ROADMAP 43.5)
  42 - shadow traffic comparisons (ROADMAP 2.1.x)
  43 - cross-region async replication log (ROADMAP 2.2.x)
  44 - inference cost attribution (ROADMAP 2.3.x)
"""

from __future__ import annotations

import importlib
import logging
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.database import utc_now

logger = logging.getLogger(__name__)

MIGRATION_FN = Callable[[sqlite3.Connection], None]


@dataclass(frozen=True)
class Migration:
    """A single schema migration step.

    ``phase`` implements the 42.2 expand/migrate/contract discipline:
    ``expand`` adds tables/columns/indexes without touching existing readers
    or writers, ``migrate`` backfills data idempotently, and ``contract``
    removes schema only after every deployed version can no longer use it.
    Migrations registered before the discipline (v1–v32) carry ``None`` and
    are grandfathered by :func:`check_migration_phases`.
    """

    version: int
    description: str
    up: MIGRATION_FN
    down: MIGRATION_FN | None = None
    phase: str | None = None


@dataclass
class MigrationResult:
    applied: list[int] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    current_version: int = 0


def _ensure_migrations_table(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            description TEXT NOT NULL,
            applied_at TEXT NOT NULL
        )
        """
    )


def _applied_versions(connection: sqlite3.Connection) -> set[int]:
    rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def _detect_legacy_version(connection: sqlite3.Connection) -> int | None:
    """Detect schema state for databases created before the migration framework.

    Returns the highest migration version whose schema footprint is already
    present, or ``None`` for a fresh database.
    """
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if "conversations" not in tables:
        return None

    legacy_columns = {
        row[1] for row in connection.execute("PRAGMA table_info(conversations)").fetchall()
    }
    if "first_response_at" in legacy_columns:
        return 3
    if "labels_json" in legacy_columns:
        return 2
    if "sla_due_at" in legacy_columns:
        return 1
    return 0


def _mark_applied(connection: sqlite3.Connection, migration: Migration) -> None:
    connection.execute(
        "INSERT INTO schema_migrations (version, description, applied_at) VALUES (?, ?, ?)",
        (migration.version, migration.description, utc_now()),
    )


def run_migrations(
    connection: sqlite3.Connection,
    migrations: list[Migration],
) -> MigrationResult:
    """Apply pending migrations in order.

    For legacy databases (created by ``Database.initialize()``), detects the
    existing schema state and marks all already-applied migrations as done
    without re-executing them.
    """
    _ensure_migrations_table(connection)
    applied = _applied_versions(connection)
    result = MigrationResult()

    if not applied:
        legacy = _detect_legacy_version(connection)
        if legacy is not None:
            for migration in migrations:
                if migration.version <= legacy:
                    _mark_applied(connection, migration)
                    result.skipped.append(migration.version)
            applied = {m.version for m in migrations if m.version <= legacy}

    for migration in migrations:
        if migration.version in applied:
            continue
        logger.info("Applying migration %d: %s", migration.version, migration.description)
        migration.up(connection)
        _mark_applied(connection, migration)
        result.applied.append(migration.version)

    result.current_version = max(m.version for m in migrations) if migrations else 0
    return result


# ---------------------------------------------------------------------------
# Migration registration
# ---------------------------------------------------------------------------
_MIGRATIONS: list[Migration] = []


def migration(
    version: int, description: str, phase: str | None = None
) -> Callable[[MIGRATION_FN], MIGRATION_FN]:
    """Register a migration step.

    ``phase`` is one of :data:`PHASES` (42.2 expand/migrate/contract); it is
    optional for the grandfathered v1–v32 chain and required for new versions
    (enforced by :func:`check_migration_phases`).
    """

    def decorator(fn: MIGRATION_FN) -> MIGRATION_FN:
        _MIGRATIONS.append(
            Migration(version=version, description=description, up=fn, down=None, phase=phase)
        )
        _MIGRATIONS.sort(key=lambda m: m.version)
        return fn

    return decorator


def all_migrations() -> list[Migration]:
    return list(_MIGRATIONS)


def verify_migration_chain(migrations: list[Migration] | None = None) -> list[str]:
    """Validate the registered migration chain (Phase 27.4 governance).

    Returns a list of problems: duplicate versions, non-contiguous version
    numbers (a gap means a migration was skipped or mis-numbered), and empty
    chains. An empty list means the chain is well-formed. Called by the
    release gate so a mis-registered migration fails before any database runs
    it.
    """
    chain = migrations if migrations is not None else _MIGRATIONS
    problems: list[str] = []
    versions = [m.version for m in chain]
    if not versions:
        return ["no migrations registered"]
    if len(versions) != len(set(versions)):
        problems.append(
            f"duplicate migration versions: {sorted({v for v in versions if versions.count(v) > 1})}"
        )
    expected = list(range(1, max(versions) + 1))
    missing = [v for v in expected if v not in versions]
    if missing:
        problems.append(f"non-contiguous migration versions, missing: {missing}")
    return problems


# 42.2 expand/migrate/contract discipline. Migrations up to this version were
# registered before the discipline existed and are grandfathered without a
# phase; every migration at or above it must declare one.
PHASE_REQUIRED_FROM_VERSION = 33
PHASES = ("expand", "migrate", "contract")


def check_migration_phases(migrations: list[Migration] | None = None) -> list[str]:
    """Validate the expand/migrate/contract annotations (42.2).

    Rules: the phase value must be one of :data:`PHASES`; every migration from
    :data:`PHASE_REQUIRED_FROM_VERSION` on must declare a phase; and a
    ``contract`` migration must be preceded by an ``expand`` migration (you
    cannot remove what was never added additively). Returns a list of
    problems; an empty list means the discipline holds.
    """
    chain = migrations if migrations is not None else _MIGRATIONS
    problems: list[str] = []
    for m in chain:
        if m.phase is not None and m.phase not in PHASES:
            problems.append(
                f"migration {m.version}: unknown phase {m.phase!r}, allowed: {list(PHASES)}"
            )
        if m.version >= PHASE_REQUIRED_FROM_VERSION and m.phase is None:
            problems.append(
                f"migration {m.version}: phase is required "
                f"(one of {list(PHASES)}) since version {PHASE_REQUIRED_FROM_VERSION}"
            )
        if m.phase == "contract" and not any(
            other.phase == "expand" and other.version < m.version for other in chain
        ):
            problems.append(
                f"migration {m.version}: contract phase requires an earlier expand migration"
            )
    return problems


def check_expand_additivity(migrations: list[Migration] | None = None) -> list[str]:
    """Enforce that every ``expand`` migration is SQL-additive (42.2 discipline).

    The phase gate (:func:`check_migration_phases`) validates *annotations*;
    this gate validates the *runtime effect*. An ``expand`` migration must
    only create new objects (tables, indexes, views, triggers) or add columns
    — never drop, rename, or rewrite existing schema or data, since the N-1
    binary keeps reading the same database during a rolling upgrade.

    Implementation is execution-based, not syntactic: the whole chain runs
    against a scratch in-memory SQLite database, and after every migration
    the table set and per-table column set must be a superset of the state
    before it. A migration that drops a table or a column — regardless of
    how it spells the SQL (direct execute, ``executescript``, or a helper
    like :func:`_ensure_column`) — fails the superset check. Trigger bodies
    and index changes are not compared: a trigger may legitimately mutate
    its own table, and an expand may add or replace an index without
    breaking the N-1 binary.

    Returns a list of problems; an empty list means every expand migration
    is additive on a real backend.
    """
    chain = migrations if migrations is not None else _MIGRATIONS
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    problems: list[str] = []
    try:
        _ensure_migrations_table(connection)
        for migration in chain:
            before = _schema_tables(connection)
            try:
                migration.up(connection)
            except Exception as error:
                problems.append(
                    f"migration {migration.version} ({migration.phase}): "
                    f"execution failed on scratch backend: {type(error).__name__}: {error}"
                )
                continue
            after = _schema_tables(connection)
            if migration.phase == "expand":
                for table, columns in before.items():
                    if table not in after:
                        problems.append(
                            f"migration {migration.version} ({migration.phase}): "
                            f"dropped table {table} — not additive for N/N+1 coexistence"
                        )
                        continue
                    for column, column_type in columns.items():
                        if column not in after[table]:
                            problems.append(
                                f"migration {migration.version} ({migration.phase}): "
                                f"removed column {column} from {table} — "
                                f"not additive for N/N+1 coexistence"
                            )
                            continue
                        # A type change is a table-rebuild (SQLite rewrites the
                        # row storage), which reads differently under the N-1
                        # binary — same breakage as a column drop.
                        if column_type != after[table][column]:
                            problems.append(
                                f"migration {migration.version} ({migration.phase}): "
                                f"changed type of {table}.{column} "
                                f"({column_type} -> {after[table][column]}) — "
                                f"not additive for N/N+1 coexistence"
                            )
    finally:
        connection.close()
    return problems


def _schema_tables(connection: sqlite3.Connection) -> dict[str, dict[str, str]]:
    """Table name -> {column name: declared type} for the current schema."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    return {
        table: {
            row[1]: (row[2] or "")
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        for table in tables
    }


def pending_migration_versions(
    connection: sqlite3.Connection,
    migrations: list[Migration] | None = None,
) -> list[int]:
    """Return migration versions not yet applied to ``connection``.

    Read-only: used by the startup readiness check when
    ``DATABASE_AUTO_MIGRATE=false`` (42.2) so web/worker processes can verify
    the release job migrated the schema without ever executing DDL. A missing
    ``schema_migrations`` table means the database was never initialized, so
    every version is reported as pending.
    """
    chain = migrations if migrations is not None else _MIGRATIONS
    try:
        applied = _applied_versions(connection)
    except Exception:
        # Table absent (fresh/unmigrated database). The exception type is
        # backend-specific (sqlite3.OperationalError vs psycopg errors), so
        # any failure to read the registry counts as "nothing applied".
        return sorted(m.version for m in chain)
    return sorted(m.version for m in chain if m.version not in applied)


def migration_schema_version(connection: sqlite3.Connection) -> int:
    """Return the highest applied migration version (0 for a fresh DB)."""
    _ensure_migrations_table(connection)
    row = connection.execute("SELECT MAX(version) AS v FROM schema_migrations").fetchone()
    if row is None:
        return 0
    value = row["v"] if hasattr(row, "keys") else row[0]
    return int(value) if value is not None else 0


def _ensure_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> None:
    """Add ``column`` to ``table`` when missing, tolerating absent tables.

    Legacy test fixtures (e.g. ``test_legacy_database_is_auto_detected``)
    may contain only a subset of the full schema; skipping those tables keeps
    the migration chain safe on partial databases.
    """
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if table not in tables:
        return
    columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _create_seq_trigger_if_table_exists(
    connection: sqlite3.Connection, table: str, trigger: str
) -> None:
    """Create a ``seq = rowid`` fill trigger only when the table exists."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    if table not in tables:
        return
    connection.execute(
        f"""
        CREATE TRIGGER IF NOT EXISTS {trigger}
        AFTER INSERT ON {table}
        BEGIN
            UPDATE {table} SET seq = rowid WHERE id = NEW.id;
        END;
        """
    )


# ---------------------------------------------------------------------------
# Per-version modules (Phase 41.6 / ARC-001 deep-module split). Each module
# registers exactly one migration via the @migration decorator; the registry
# sanity gate (verify_migration_registry) cross-checks module ownership.
# ---------------------------------------------------------------------------
_VERSION_MODULES = [
    "v01_baseline_schema",
    "v02_conversation_sla_deadline",
    "v03_conversation_labels_projection",
    "v04_first_response_timestamp_and_monotonic_seq",
    "v05_turn_job_chunks_streaming_table",
    "v06_prompt_version_registry",
    "v07_tenant_model_policy_and_daily_usage",
    "v08_outbound_webhook_endpoints_and_deliveries",
    "v09_quality_aggregates_and_knowledge_lifecycle",
    "v10_tenant_members_and_quota_metering",
    "v11_channel_message_idempotency_for_web_chat",
    "v12_audit_hash_chain",
    "v13_revoked_api_keys",
    "v14_csat_satisfaction_surveys",
    "v15_auto_routing_rules_and_agent_groups",
    "v16_sla_policy_engine",
    "v17_conversation_summaries",
    "v18_operator_collaboration_mentions_and_threads",
    "v19_conversation_and_knowledge_article_language",
    "v20_long_cycle_tickets",
    "v21_report_subscriptions",
    "v22_rich_media_attachments",
    "v23_message_pagination_seq_index_roadmap_18_2d",
    "v24_conversation_archive_cold_tier_roadmap_18_3",
    "v25_csat_summary_partial_index",
    "v26_durable_audit_retention_archives_roadmap_18_3",
    "v27_signed_inbound_channel_webhook_durability_roadma",
    "v28_oidc_one_time_auth_transactions_m0_sec_001",
    "v29_dsr_maker_checker_workflow_and_encrypted_exports",
    "v30_unified_credential_lifecycle_registry_phase_41_s",
    "v31_external_audit_anchors_observable_audit_gaps_pha",
    "v32_data_field_registry_dsr_sla_deletion_tombstones_",
    "v33_audit_archive_object_store",
    "v34_attachment_checksum",
    "v35_turn_job_request_context",
    "v36_tenant_control_plane",
    "v37_domain_outbox_api_idempotency",
    "v38_tenant_deks_envelope_encryption",
    "v39_webhook_secret_format",
    "v40_tenant_residency_column",
    "v41_ai_governance_registry",
    "v42_shadow_traffic_comparisons",
    "v43_replication_log",
    "v44_inference_costs",
]

for _module_name in _VERSION_MODULES:
    importlib.import_module(f"{__name__}.{_module_name}")


def verify_migration_registry(package_dir: Path | None = None) -> list[str]:
    """Sanity-check the per-version module split (Phase 41.6 / ARC-001).

    Cross-checks the filesystem layout of this package against the registry:
    every registered migration must live in the version module named for it
    (``v{N:02d}_*.py``), and every version module present in the package must
    register exactly one migration with the matching version. Returns a list
    of problems (empty list means the registry is sound); used by the
    ``scripts/verify_migration_registry.py`` release gate so a mis-registered
    migration fails before any database runs it.
    """
    problems: list[str] = []
    package_dir = package_dir or Path(__file__).resolve().parent

    registered = {m.version: m for m in all_migrations()}
    module_files = {
        int(path.stem.split("_")[0][1:]): path.name
        for path in package_dir.glob("v*_*.py")
        if re.fullmatch(r"v\d{2}_[A-Za-z0-9_]+\.py", path.name)
    }

    for version, migration in sorted(registered.items()):
        module_name = migration.up.__module__
        expected_prefix = f"{__name__}.v{version:02d}_"
        if not module_name.startswith(expected_prefix):
            problems.append(
                f"migration {version} registered from {module_name}, expected "
                f"{__name__}.v{version:02d}_*"
            )
        if version not in module_files:
            problems.append(f"migration {version} has no version module file")
        else:
            module_files.pop(version, None)

    for version, filename in sorted(module_files.items()):
        problems.append(f"version module {filename} registers no migration {version}")

    problems.extend(verify_migration_chain(list(all_migrations())))
    return problems
