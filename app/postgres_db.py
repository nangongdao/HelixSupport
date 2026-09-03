"""PostgreSQL persistence backend.

Enables multi-instance deployments by pointing every application instance at a
shared PostgreSQL database.

Rather than reimplementing the ~60 query methods of :class:`app.database.Database`
(a second copy that inevitably drifts out of sync with the SQLite original),
this backend *subclasses* it and swaps out only the dialect-specific layer:

* :class:`app.pg_dialect.PostgresConnection` adapts psycopg2 to the
  ``sqlite3`` connection surface, translating placeholders and SQLite-isms.
* :mod:`app.pg_compat` installs server-side functions and triggers that
  emulate the SQLite built-ins the query layer relies on.

Everything else — tenant scoping, optimistic concurrency, idempotency guards,
the queue, dashboards — is inherited verbatim, so both backends share one
implementation and one set of tests.

Configuration:
- ``DATABASE_BACKEND=postgresql`` selects this backend.
- ``DATABASE_URL`` is a libpq DSN or ``postgresql://`` URL.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
from typing import Any, Iterator

from app.context import current_scope_mode, current_tenant, maintenance_scope
from app.database import Database
from app.pg_compat import (
    install_archive_trgm_search,
    install_archive_updated_sort_index,
    install_csat_summary_index,
    install_functions,
    install_ordering_columns,
    install_trgm_search,
    install_triggers,
    install_updated_sort_index,
)
from app.pg_dialect import PostgresConnection
from app.rls import RLS_TABLES, TENANT_CONTEXT_GUC, TenantContextError, install_rls

try:
    import psycopg2

    _PSYCOPG2_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised via patching in tests
    _PSYCOPG2_AVAILABLE = False

logger = logging.getLogger(__name__)

# Advisory lock key serialising schema initialization across processes
# (multi-instance boots; see :meth:`PostgresDatabase.initialize`).
_SCHEMA_INIT_LOCK = 9173001

__all__ = [
    "PostgresConnectionPool",
    "PostgresDatabase",
    "create_postgres_database",
]


class PostgresConnectionPool:
    """Thread-safe pool of adapted PostgreSQL connections.

    Mirrors the lazy-growth strategy of the SQLite pool: connections are
    created on demand up to ``pool_size`` and reused thereafter.
    """

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 8,
        connect_timeout_seconds: int = 10,
    ) -> None:
        if not _PSYCOPG2_AVAILABLE:
            raise ImportError(
                "psycopg2 is required for the PostgreSQL backend (pip install psycopg2-binary)"
            )
        if pool_size < 1:
            raise ValueError("pool_size must be positive")

        self._url = url
        self.pool_size = pool_size
        self._connect_timeout = connect_timeout_seconds
        self._pool: Queue[PostgresConnection] = Queue(maxsize=pool_size)
        self._lock = Lock()
        self._created = 0
        self._closed = False

    @property
    def url(self) -> str:
        return self._url

    def _new_connection(self) -> PostgresConnection:
        raw = psycopg2.connect(self._url, connect_timeout=self._connect_timeout)
        raw.autocommit = False
        return PostgresConnection(raw)

    def _acquire(self) -> PostgresConnection:
        if self._closed:
            raise RuntimeError("Database connection pool is closed")
        try:
            return self._pool.get_nowait()
        except Empty:
            pass

        with self._lock:
            if self._closed:
                raise RuntimeError("Database connection pool is closed")
            should_create = self._created < self.pool_size
            if should_create:
                self._created += 1
        if should_create:
            try:
                return self._new_connection()
            except Exception:
                with self._lock:
                    self._created -= 1
                raise
        try:
            return self._pool.get(timeout=self._connect_timeout)
        except Empty as exc:
            raise sqlite3.OperationalError("Timed out waiting for a database connection") from exc

    def _release(self, connection: PostgresConnection) -> None:
        if self._closed:
            connection.close()
            with self._lock:
                self._created = max(0, self._created - 1)
            return
        try:
            self._pool.put_nowait(connection)
        except Exception:
            connection.close()
            with self._lock:
                self._created = max(0, self._created - 1)

    @contextmanager
    def connect(self) -> Iterator[PostgresConnection]:
        connection = self._acquire()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._release(connection)

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                "size": self.pool_size,
                "created": self._created,
                "idle": self._pool.qsize(),
                "closed": self._closed,
            }

    def close(self) -> None:
        with self._lock:
            self._closed = True
        while True:
            try:
                connection = self._pool.get_nowait()
            except Empty:
                break
            connection.close()
            with self._lock:
                self._created = max(0, self._created - 1)


class PostgresDatabase(Database):
    """PostgreSQL-backed persistence layer.

    Inherits every query method from :class:`app.database.Database`; only
    connection management and the handful of SQLite-only features are
    overridden.
    """

    def __init__(
        self,
        url: str,
        *,
        pool_size: int = 8,
        knowledge_cache_ttl_seconds: int = 30,
        dashboard_cache_ttl_seconds: int = 5,
        cache_max_entries: int = 512,
    ) -> None:
        # Reuse the parent's cache/bookkeeping setup, then swap the pool.
        super().__init__(
            Path("postgresql"),
            pool_size=pool_size,
            knowledge_cache_ttl_seconds=knowledge_cache_ttl_seconds,
            dashboard_cache_ttl_seconds=dashboard_cache_ttl_seconds,
            cache_max_entries=cache_max_entries,
        )
        self._pg_pool = PostgresConnectionPool(url, pool_size=pool_size)
        self.url = url
        self.backend = "postgresql"
        # Phase 43.2 contract (a): when enabled, every connection binds the
        # ambient tenant scope into a transaction-scoped GUC so RLS policies
        # filter rows even if application WHERE predicates were lost. Off by
        # default; flipped on via DATABASE_RLS_ENABLED / create_postgres_database.
        self.rls_enforce_context = False

    # -- connection management -------------------------------------------
    @contextmanager
    def connect(self) -> Iterator[Any]:
        """Yield an adapted PostgreSQL connection.

        Overrides the parent's SQLite pool, including its ``mkdir`` of the
        database directory, which has no meaning here.
        """
        with self._pg_pool.connect() as connection:
            self._bind_tenant_context(connection)
            yield connection

    def _bind_tenant_context(self, connection: Any) -> None:
        """Bind the ambient tenant scope to this transaction (43.2).

        ``tenant`` scope -> ``set_config('app.tenant_id', ..., true)``, i.e.
        transaction-local: it disappears at commit/rollback so a pooled
        connection never leaks context into its next user. ``maintenance``
        scope intentionally binds nothing and requires a role RLS does not
        cover (owner / BYPASSRLS). No scope at all fails loudly — silently
        seeing zero rows would hide bugs instead of surfacing them.
        """
        if not self.rls_enforce_context:
            return
        mode = current_scope_mode()
        if mode == "tenant":
            tenant = current_tenant()
            assert tenant is not None  # guaranteed by the "tenant" mode
            connection.execute(f"SELECT set_config('{TENANT_CONTEXT_GUC}', ?, true)", (tenant,))
        elif mode is None:
            raise TenantContextError(
                "row-level security is enforced: database access requires "
                "app.context.tenant_scope() or maintenance_scope()"
            )

    def install_row_level_security(self, *, force_owner: bool = False) -> None:
        """Create/refresh the tenant-isolation policies (43.2).

        Runs as the migration/owner role during initialization or the
        release job; idempotent. ``force_owner=True`` additionally subjects
        the table owner to RLS for deployments that do not provision a
        separate least-privilege application role.
        """
        with self._pg_pool.connect() as connection:
            install_rls(connection, force_owner=force_owner)
        logger.info(
            "PostgreSQL row-level tenant security installed (%d tables, force_owner=%s)",
            len(RLS_TABLES),
            force_owner,
        )

    def close(self) -> None:
        self._pg_pool.close()

    # -- SQLite-only features ---------------------------------------------
    def _acquire_audit_chain_write_lock(self, connection: Any) -> None:
        """PostgreSQL: serialise chain-tail reads with an advisory lock.

        Multi-instance / multi-worker audit appends must never read the same
        chain tail and fork it; ``pg_advisory_xact_lock`` is a transaction
        scope lock that is released at commit (see
        :meth:`app.database.DatabaseAuditMixin._acquire_audit_chain_write_lock`
        for the SQLite ``BEGIN IMMEDIATE`` equivalent).
        """
        connection.execute("SELECT pg_advisory_xact_lock(?)", (9163831,))

    def _initialize_knowledge_fts(self, connection: Any) -> None:
        """No-op: FTS5 is SQLite-specific.

        Leaving ``_fts_enabled`` false makes the inherited query methods use
        their LIKE-based fallback path.
        """
        self._fts_enabled = False

    def _initialize_message_fts(self, connection: Any) -> None:
        """No-op: see :meth:`_initialize_knowledge_fts`."""
        self._message_fts_enabled = False

    def initialize(self) -> None:
        """Create the schema and install PostgreSQL compatibility objects.

        Scalar function shims must exist *before* the parent's schema setup
        runs, because its backfill queries call them; the monotonic ``seq``
        columns and the triggers can only exist once the tables do, so
        installation happens in three passes.

        The whole pass is gated by a session-level advisory lock so two
        processes booting at once (``uvicorn --workers N``, or a rolling
        restart overlapping a fresh instance) never run the ``conversations``
        backfill concurrently — two full-table ``UPDATE``s on the same rows in
        different orders deadlock and kill one of the booting workers.
        """
        # 43.2: schema DDL/backfills are cross-tenant system work — run them
        # under an explicit maintenance scope so RLS-enforcing connections
        # fail loudly instead of silently filtering the backfill reads.
        with maintenance_scope("schema-init"):
            self._initialize_locked()

    def _initialize_locked(self) -> None:
        with self._pg_pool.connect() as gate:
            gate.execute("SELECT pg_advisory_lock(?)", (_SCHEMA_INIT_LOCK,))
            try:
                with self._pg_pool.connect() as connection:
                    install_functions(connection.raw)
                super().initialize()
                with self._pg_pool.connect() as connection:
                    install_ordering_columns(connection.raw)
                    install_triggers(connection.raw)
                    # ROADMAP 18.2: trigram index so the shared LIKE message-search
                    # fallback is index-backed on PostgreSQL.  Best-effort; see
                    # :func:`app.pg_compat.install_trgm_search`.
                    install_trgm_search(connection.raw)
                    # ROADMAP 18.5: updated-sort index covering the shared
                    # ``ORDER BY updated_at DESC, id DESC`` (queue paging +
                    # message-search fast path).
                    install_updated_sort_index(connection.raw)
                    # ROADMAP 18.3: mirror the hot-tier message-search and
                    # updated-sort indexes onto the archive tables so cold
                    # reverse-lookup is index-backed too.  Best-effort for the
                    # trigram one; see :func:`app.pg_compat.install_archive_trgm_search`.
                    install_archive_trgm_search(connection.raw)
                    install_archive_updated_sort_index(connection.raw)
                    # Backlog (CSAT summary): partial index covering the admin
                    # summary's tenant + responded_at date aggregate. Mirrors
                    # migration 25 on the SQLite side; idempotent.
                    install_csat_summary_index(connection.raw)
            finally:
                gate.execute("SELECT pg_advisory_unlock(?)", (_SCHEMA_INIT_LOCK,))

    def performance_stats(self) -> dict[str, Any]:
        stats = super().performance_stats()
        stats["pool"] = self._pg_pool.stats()
        stats["backend"] = "postgresql"
        return stats


def create_postgres_database(
    url: str, *, auto_migrate: bool = True, rls_enabled: bool = False, **kwargs: Any
) -> PostgresDatabase:
    """Create a PostgreSQL-backed database.

    With ``auto_migrate=True`` (default) the full schema initialization runs —
    DDL, functions, triggers, and indexes. With ``auto_migrate=False`` (42.2
    HA posture) no DDL executes: the caller must have run the migration
    release job (``scripts/run_migrations.py``) and startup only verifies
    readiness against ``schema_migrations``.

    With ``rls_enabled=True`` (43.2) connections bind the ambient tenant scope
    per transaction (fail closed without one) and the tenant-isolation RLS
    policies are installed right after initialization.
    """
    database = PostgresDatabase(url, **kwargs)
    database.rls_enforce_context = rls_enabled
    if auto_migrate:
        database.initialize()
        if rls_enabled:
            database.install_row_level_security()
    return database
