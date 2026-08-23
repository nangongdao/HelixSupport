"""Database core mixin (Phase 27.1, extracted from app/database.py)."""

from __future__ import annotations

# pyright: reportAttributeAccessIssue=false

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from queue import Empty, Queue
from threading import Lock
from typing import Any, Iterator

from app.cache import TTLCache
from app.db._util import (
    knowledge_search_document,
)


class DatabaseCoreMixin:
    def __init__(
        self,
        path: Path,
        pool_size: int = 4,
        busy_timeout_ms: int = 5000,
        knowledge_cache_ttl_seconds: int = 30,
        dashboard_cache_ttl_seconds: int = 5,
        cache_max_entries: int = 512,
        knowledge_search_candidate_cap: int = 200,
    ) -> None:
        if pool_size < 1:
            raise ValueError("pool_size must be positive")
        if busy_timeout_ms < 100:
            raise ValueError("busy_timeout_ms must be at least 100")
        self.path = path
        self.pool_size = pool_size
        self.busy_timeout_ms = busy_timeout_ms
        self.backend = "sqlite"  # overridden to "postgresql" by PostgresDatabase
        self._pool: Queue[sqlite3.Connection] = Queue(maxsize=pool_size)
        self._pool_lock = Lock()
        self._created_connections = 0
        self._closed = False
        self._fts_enabled = False
        self._fts_queries = 0
        self._fts_fallbacks = 0
        self._message_fts_enabled = False
        self._message_fts_queries = 0
        self._message_fts_fallbacks = 0
        self._knowledge_cache: TTLCache[str, list[dict[str, Any]]] = TTLCache(
            knowledge_cache_ttl_seconds, cache_max_entries
        )
        # ROADMAP 18.2b: short-lived cache for FTS hit results, keyed by
        # (tenant_id, knowledge version, normalized query, language, limit).
        # The tenant version bumps on every knowledge write so a stale hit can
        # never outlive the row it was computed from.
        self._knowledge_search_cache: TTLCache[
            tuple[str, int, str, str, int], list[dict[str, Any]]
        ] = TTLCache(knowledge_cache_ttl_seconds, cache_max_entries)
        self._knowledge_search_versions: dict[str, int] = {}
        # ROADMAP 18.2b: defensive cap on how many candidate rows a single
        # knowledge search may request. The FTS query already early-stops via
        # SQL LIMIT; this bounds callers that ask for an unbounded limit.
        self._knowledge_search_candidate_cap = max(1, knowledge_search_candidate_cap)
        self._dashboard_cache: TTLCache[str, dict[str, Any]] = TTLCache(
            dashboard_cache_ttl_seconds, cache_max_entries
        )
        self._tenant_exists_cache: set[str] = set()
        self._queue_watermarks: dict[str, str] = {}
        self._queue_revision = 0

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._acquire_connection()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            self._release_connection(connection)

    def _acquire_connection(self) -> sqlite3.Connection:
        if self._closed:
            raise RuntimeError("Database connection pool is closed")
        try:
            return self._pool.get_nowait()
        except Empty:
            pass

        with self._pool_lock:
            if self._closed:
                raise RuntimeError("Database connection pool is closed")
            if self._created_connections < self.pool_size:
                self._created_connections += 1
                should_create = True
            else:
                should_create = False
        if should_create:
            try:
                return self._new_connection()
            except Exception:
                with self._pool_lock:
                    self._created_connections -= 1
                raise
        try:
            return self._pool.get(timeout=self.busy_timeout_ms / 1000)
        except Empty as exc:
            raise sqlite3.OperationalError("Timed out waiting for a database connection") from exc

    def _new_connection(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.path,
            timeout=self.busy_timeout_ms / 1000,
            check_same_thread=False,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {self.busy_timeout_ms}")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA temp_store = MEMORY")
        connection.execute("PRAGMA cache_size = -20000")
        connection.execute("PRAGMA mmap_size = 268435456")
        connection.execute("PRAGMA wal_autocheckpoint = 1000")
        connection.create_function(
            "helix_search_terms",
            1,
            knowledge_search_document,
            deterministic=True,
        )
        return connection

    def _release_connection(self, connection: sqlite3.Connection) -> None:
        if self._closed:
            connection.close()
            with self._pool_lock:
                self._created_connections = max(0, self._created_connections - 1)
            return
        try:
            self._pool.put_nowait(connection)
        except Exception:
            connection.close()
            with self._pool_lock:
                self._created_connections = max(0, self._created_connections - 1)

    def close(self) -> None:
        with self._pool_lock:
            self._closed = True
        while True:
            try:
                connection = self._pool.get_nowait()
            except Empty:
                break
            connection.close()
            with self._pool_lock:
                self._created_connections = max(0, self._created_connections - 1)

    def performance_stats(self) -> dict[str, Any]:
        with self._pool_lock:
            pool = {
                "size": self.pool_size,
                "created": self._created_connections,
                "idle": self._pool.qsize(),
                "closed": self._closed,
            }
            search_versions = {
                tid: version for tid, version in self._knowledge_search_versions.items()
            }
        knowledge = self._knowledge_cache.stats()
        knowledge_search = self._knowledge_search_cache.stats()
        dashboard = self._dashboard_cache.stats()
        return {
            "pool": pool,
            "cache": {
                "knowledge": knowledge.__dict__,
                "knowledge_search": knowledge_search.__dict__,
                "dashboard": dashboard.__dict__,
                "knowledge_versions": search_versions,
            },
            "knowledge_search": {
                "fts5_enabled": self._fts_enabled,
                "fts_queries": self._fts_queries,
                "fallback_queries": self._fts_fallbacks,
            },
            "message_search": {
                "fts5_enabled": self._message_fts_enabled,
                "fts_queries": self._message_fts_queries,
                "fallback_queries": self._message_fts_fallbacks,
            },
        }

    def _invalidate_dashboard(self, tenant_id: str) -> None:
        self._dashboard_cache.invalidate(tenant_id)
        self._touch_queue_watermark(tenant_id)

    def _knowledge_version(self, tenant_id: str) -> int:
        with self._pool_lock:
            return self._knowledge_search_versions.get(tenant_id, 0)

    def _invalidate_knowledge(self, tenant_id: str) -> None:
        self._knowledge_cache.invalidate(tenant_id)
        # ROADMAP 18.2b: bump the tenant's knowledge version so any cached FTS
        # hit keyed on the old version is skipped on the next read.
        with self._pool_lock:
            self._knowledge_search_versions[tenant_id] = (
                self._knowledge_search_versions.get(tenant_id, 0) + 1
            )

    def _touch_queue_watermark(self, tenant_id: str, watermark: str | None = None) -> None:
        with self._pool_lock:
            self._queue_revision += 1
            self._queue_watermarks[tenant_id] = watermark or f"rev:{self._queue_revision}"

    def queue_revision(self, tenant_id: str) -> str:
        with self._pool_lock:
            cached = self._queue_watermarks.get(tenant_id)
            if cached:
                return cached
        watermark = self.conversation_watermark(tenant_id)
        with self._pool_lock:
            self._queue_watermarks[tenant_id] = watermark or f"empty:{tenant_id}"
            return self._queue_watermarks[tenant_id]

    def ping(self) -> bool:
        try:
            with self.connect() as connection:
                return connection.execute("SELECT 1").fetchone()[0] == 1
        except sqlite3.Error:
            return False
