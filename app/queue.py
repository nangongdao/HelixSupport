"""Pluggable task queue abstraction for turn-job dispatch.

The current SQLite-backed queue works for single-node deployments.
This module defines a protocol and an in-process implementation so that
external backends (Redis, RabbitMQ, SQS) can be plugged in without
changing the orchestrator or worker.

The Redis backend is a genuine cross-instance durable queue: jobs are handed
out atomically from a dispatch list, leases expire and are recovered, retries
are scheduled with backoff, and the database row is reconciled by id.  The
database remains authoritative for per-conversation serialization and job
record; Redis owns dispatch and leases.

Configuration:
- ``QUEUE_BACKEND`` – ``sqlite`` (default) or ``redis``.
- ``REDIS_URL`` – connection string for the Redis backend.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class QueueUnavailableError(Exception):
    """The configured task queue backend is unreachable (M0 REL-001).

    Raised only when the deployment is fail-closed: callers must not enqueue,
    and the worker must not run, while the backend is down.  Mapped to HTTP
    503 with ``Retry-After`` by the API layer.
    """


@runtime_checkable
class TaskQueue(Protocol):
    """Protocol for durable async task dispatch."""

    # Readiness surface: every backend must report availability, a degraded
    # reason when unreachable, and the last successful probe.  Consumed by the
    # /health/ready endpoint, app startup worker gating (M0 REL-001) and the
    # operator diagnostics bundle.
    backend_name: str
    degraded_reason: str | None
    last_success_epoch: int

    def probe(self) -> bool: ...

    def is_ready(self) -> bool: ...

    def enqueue(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        actor_id: str,
        content: str,
        max_attempts: int = 3,
        channel_message_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Submit a job. Returns ``(job, replayed)`` — idempotent on the key."""
        ...

    def dequeue(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        """Claim the next available job. Returns None if queue is empty."""
        ...

    def complete(
        self,
        job_id: str,
        worker_id: str,
        response: dict[str, Any],
        lease_seconds: int,
    ) -> bool:
        """Mark a job as completed, gated on a fresh lease (REL-001)."""
        ...

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        retry_base_seconds: int,
        lease_seconds: int,
        retryable: bool = True,
    ) -> dict[str, Any] | None:
        """Mark a job as failed under a fresh lease; schedule retry if attempts remain."""
        ...

    def recover(self, lease_seconds: int) -> dict[str, int]:
        """Reclaim jobs whose claims (leases) have expired."""
        ...

    def retry(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        """Re-dispatch a terminal (failed) job. Returns the job or None."""
        ...

    def stats(self, tenant_id: str | None = None) -> dict[str, Any]:
        """Return queue depth and throughput metrics."""
        ...


class SQLiteTaskQueue:
    """In-process SQLite-backed queue. The current production default."""

    backend_name = "sqlite"

    def __init__(self, database: Any) -> None:
        self.database = database
        self.degraded_reason: str | None = None
        self.last_success_epoch: int = 0

    def probe(self) -> bool:
        """Return True when the queue can accept work (always true for SQLite)."""
        self.last_success_epoch = int(time.time())
        self.degraded_reason = None
        return True

    def is_ready(self) -> bool:
        return self.probe()

    def enqueue(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        actor_id: str,
        content: str,
        max_attempts: int = 3,
        channel_message_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Submit a job. Returns ``(job, replayed)``."""
        job, replayed = self.database.enqueue_turn_job(
            tenant_id,
            conversation_id,
            idempotency_key,
            actor_id,
            content,
            max_attempts,
            channel_message_id,
        )
        return job, replayed

    def dequeue(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        return self.database.claim_next_turn_job(worker_id, lease_seconds)

    def complete(
        self,
        job_id: str,
        worker_id: str,
        response: dict[str, Any],
        lease_seconds: int,
    ) -> bool:
        return self.database.complete_turn_job(job_id, worker_id, response, lease_seconds)

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        retry_base_seconds: int,
        lease_seconds: int,
        retryable: bool = True,
    ) -> dict[str, Any] | None:
        return self.database.fail_turn_job(
            job_id, worker_id, error_code, retry_base_seconds,
            retryable=retryable, lease_seconds=lease_seconds,
        )

    def recover(self, lease_seconds: int) -> dict[str, int]:
        return self.database.recover_turn_jobs(lease_seconds)

    def retry(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        return self.database.retry_turn_job(tenant_id, job_id)

    def stats(self, tenant_id: str | None = None) -> dict[str, Any]:
        return self.database.turn_job_stats(tenant_id)


# ---------------------------------------------------------------------------
# Redis backend
# ---------------------------------------------------------------------------

# Dispatch FIFO: LIST of JSON ``{"job_id", "conversation_id", "tenant_id"}``.
_DISPATCH = "helix:q:dispatch"
# Claimed jobs awaiting completion: HASH job_id -> dispatch JSON.
_PROCESSING = "helix:q:processing"
# Claim leases: ZSET job_id -> epoch expiry.
_LEASES = "helix:q:leases"
# Retry backoff: ZSET member = dispatch JSON, score = epoch available_at.
_DELAYED = "helix:q:delayed"

# Atomically hand out the first claimable job.  Each candidate is LPOP'd; a job
# already in ``processing`` (a stale dispatch entry) is dropped; otherwise it is
# claimed (HSETNX) and its lease recorded.  Returns the dispatch JSON or nil.
_CLAIM_LUA = """
local max_scan = tonumber(ARGV[3])
local scan = 0
while scan < max_scan do
    local raw = redis.call('LPOP', KEYS[1])
    if not raw then
        return nil
    end
    scan = scan + 1
    local job = cjson.decode(raw)
    if redis.call('HSETNX', KEYS[2], job.job_id, raw) == 1 then
        redis.call('ZADD', KEYS[3], tonumber(ARGV[1]) + tonumber(ARGV[2]), job.job_id)
        return raw
    end
end
return nil
"""


def _iso_to_epoch(value: str) -> int:
    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())


class RedisTaskQueue:
    """Redis-backed durable queue for multi-instance deployments.

    Requires ``redis>=5``.  Uses a LIST for dispatch, a HASH for in-flight jobs,
    a sorted set for claim leases, and a second sorted set for retry backoff.
    Cross-instance safety:

    * exactly one worker receives each dispatch entry (atomic ``HSETNX`` in the
      claim script);
    * the database claim-by-id reconciles the job row and enforces
      per-conversation serialization, so a job handed out for a busy
      conversation is released and re-queued;
    * leases expire and ``recover()`` re-dispatches abandoned jobs;
    * retries with backoff are scheduled in the ``delayed`` set.
    """

    def __init__(self, redis_client: Any, database: Any, fail_closed: bool = False) -> None:
        self.redis = redis_client
        self.database = database
        self.fail_closed = fail_closed
        self.backend_name = "redis"
        self.degraded_reason: str | None = None
        self.last_success_epoch: int = 0

    def probe(self) -> bool:
        """Ping Redis; updates ``degraded_reason`` and ``last_success_epoch``."""
        try:
            self.redis.ping()
            self.last_success_epoch = int(time.time())
            self.degraded_reason = None
            return True
        except Exception as exc:
            self.degraded_reason = f"redis unreachable: {exc}"
            return False

    def is_ready(self) -> bool:
        return self.probe()

    def _unavailable(self, exc: Exception) -> QueueUnavailableError:
        return QueueUnavailableError(
            f"Task queue backend ({self.backend_name}) is unavailable: {exc}"
        )

    def _guard(self, exc: Exception) -> None:
        """Fail closed on backend errors; otherwise re-raise unchanged."""
        self.degraded_reason = f"redis error: {exc}"
        if self.fail_closed:
            raise self._unavailable(exc) from exc
        raise exc

    @staticmethod
    def _dispatch_payload(job: dict[str, Any]) -> str:
        return json.dumps(
            {
                "job_id": job["id"],
                "conversation_id": job["conversation_id"],
                "tenant_id": job["tenant_id"],
            }
        )

    def enqueue(
        self,
        tenant_id: str,
        conversation_id: str,
        idempotency_key: str,
        actor_id: str,
        content: str,
        max_attempts: int = 3,
        channel_message_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        job, replayed = self.database.enqueue_turn_job(
            tenant_id,
            conversation_id,
            idempotency_key,
            actor_id,
            content,
            max_attempts,
            channel_message_id,
        )
        if not replayed:
            # Only new jobs enter the dispatch list; replays must not double-queue.
            try:
                self.redis.rpush(_DISPATCH, self._dispatch_payload(job))
            except Exception as exc:
                self._guard(exc)
        return job, replayed

    def _promote_delayed(self, now: int) -> None:
        try:
            due = self.redis.zrangebyscore(_DELAYED, 0, now)
        except Exception as exc:
            self._guard(exc)
            return
        if due:
            self.redis.rpush(_DISPATCH, *due)
            self.redis.zrem(_DELAYED, *due)

    def dequeue(self, worker_id: str, lease_seconds: int) -> dict[str, Any] | None:
        now = int(time.time())
        self._promote_delayed(now)
        try:
            raw = self.redis.eval(
                _CLAIM_LUA,
                3,
                _DISPATCH,
                _PROCESSING,
                _LEASES,
                str(now),
                str(lease_seconds),
                "64",
            )
        except Exception as exc:
            self._guard(exc)
            return None
        if not raw:
            return None
        ref = json.loads(raw)
        job = self.database.claim_turn_job_by_id(ref["job_id"], worker_id, lease_seconds)
        if job is None:
            # The database refused the claim (conversation serialised, lease
            # gating, or attempts exhausted): release the Redis claim and put
            # the job back so a later claim can retry.
            self.redis.zrem(_LEASES, ref["job_id"])
            self.redis.hdel(_PROCESSING, ref["job_id"])
            self.redis.rpush(_DISPATCH, raw)
            return None
        return job

    def complete(
        self,
        job_id: str,
        worker_id: str,
        response: dict[str, Any],
        lease_seconds: int,
    ) -> bool:
        ok = self.database.complete_turn_job(job_id, worker_id, response, lease_seconds)
        try:
            self._cleanup_claim(job_id)
        except Exception as exc:
            self._guard(exc)
        return ok

    def fail(
        self,
        job_id: str,
        worker_id: str,
        error_code: str,
        retry_base_seconds: int,
        lease_seconds: int,
        retryable: bool = True,
    ) -> dict[str, Any] | None:
        result = self.database.fail_turn_job(
            job_id, worker_id, error_code, retry_base_seconds,
            retryable=retryable, lease_seconds=lease_seconds,
        )
        try:
            self._cleanup_claim(job_id)
        except Exception as exc:
            self._guard(exc)
        if result and result["status"] == "queued":
            # Backoff: re-dispatch once the job becomes available again.
            payload = self._dispatch_payload(result)
            try:
                self.redis.zadd(_DELAYED, {payload: _iso_to_epoch(result["available_at"])})
            except Exception as exc:
                self._guard(exc)
        return result

    def recover(self, lease_seconds: int) -> dict[str, int]:
        now = int(time.time())
        self._promote_delayed(now)
        try:
            expired = self.redis.zrangebyscore(_LEASES, 0, now)
        except Exception as exc:
            self._guard(exc)
            return {"queued": 0, "failed": 0, "redis_requeued": 0, "reconciled": 0}
        requeued = 0
        for job_id in expired:
            raw = self.redis.hget(_PROCESSING, job_id)
            self.redis.zrem(_LEASES, job_id)
            self.redis.hdel(_PROCESSING, job_id)
            if raw:
                self.redis.rpush(_DISPATCH, raw)
                requeued += 1
        db_recovered = self.database.recover_turn_jobs(lease_seconds)
        try:
            reconciled = self._reconcile_orphans()
        except Exception as exc:
            self._guard(exc)
            reconciled = 0
        return {**db_recovered, "redis_requeued": requeued, "reconciled": reconciled}

    def _reconcile_orphans(self) -> int:
        """Re-push DB-``queued`` jobs that exist nowhere in Redis.

        A process killed between the DB ``enqueue_turn_job`` insert and the
        dispatch push strands a ``queued`` row forever: ``dequeue`` only ever
        pulls from Redis.  Every recover pass compensates for that window by
        pushing any stale-queued row that is absent from the dispatch list,
        the delayed set, and the processing hash.  Empty in normal operation,
        so the scan is nearly free.
        """
        stale = self.database.list_stale_queued_turn_jobs(limit=500)
        if not stale:
            return 0
        dispatch = set(self.redis.lrange(_DISPATCH, 0, -1))
        delayed = set(self.redis.zrange(_DELAYED, 0, -1))
        re_pushed = 0
        for row in stale:
            payload = self._dispatch_payload(row)
            if payload in dispatch or payload in delayed:
                continue
            if self.redis.hexists(_PROCESSING, row["id"]):
                continue
            self.redis.rpush(_DISPATCH, payload)
            dispatch.add(payload)
            re_pushed += 1
        return re_pushed

    def retry(self, tenant_id: str, job_id: str) -> dict[str, Any] | None:
        job = self.database.retry_turn_job(tenant_id, job_id)
        if job is not None:
            try:
                self.redis.rpush(_DISPATCH, self._dispatch_payload(job))
            except Exception as exc:
                self._guard(exc)
        return job

    def _cleanup_claim(self, job_id: str) -> None:
        self.redis.zrem(_LEASES, job_id)
        self.redis.hdel(_PROCESSING, job_id)

    def stats(self, tenant_id: str | None = None) -> dict[str, Any]:
        sqlite_stats = self.database.turn_job_stats(tenant_id)
        try:
            dispatch_depth = self.redis.llen(_DISPATCH)
            in_flight = self.redis.hlen(_PROCESSING)
        except Exception as exc:
            self._guard(exc)
            dispatch_depth = 0
            in_flight = 0
        return {
            **sqlite_stats,
            "redis_dispatch_depth": dispatch_depth,
            "redis_in_flight": in_flight,
        }


def create_task_queue(database: Any, settings: Any) -> TaskQueue:
    """Factory: select queue backend from configuration.

    M0 REL-001: with ``queue_failure_mode=fail_closed`` the app still boots
    when Redis is down — the readiness endpoint must be able to report the
    degraded queue — but the returned queue records ``degraded_reason`` and
    raises :class:`QueueUnavailableError` on every backend operation, so the
    API answers 503 + Retry-After and the worker stays down until the backend
    is reachable again.
    """
    failure_mode = getattr(settings, "queue_failure_mode", "fallback")
    fail_closed = failure_mode == "fail_closed"
    backend = getattr(settings, "queue_backend", "sqlite")
    if backend == "redis":
        try:
            import redis

            client = redis.from_url(settings.redis_url, decode_responses=True)
        except ImportError:
            if fail_closed:
                raise QueueUnavailableError(
                    "Task queue unavailable: redis package is not installed"
                ) from None
            logger.warning("redis package not installed; falling back to SQLite queue")
            return SQLiteTaskQueue(database)
        except Exception as exc:  # invalid URL, bad options, ...
            if fail_closed:
                raise QueueUnavailableError(
                    f"Task queue unavailable: cannot build Redis client: {exc}"
                ) from exc
            logger.warning("Redis client build failed (%s); falling back to SQLite queue", exc)
            return SQLiteTaskQueue(database)
        queue = RedisTaskQueue(client, database, fail_closed=fail_closed)
        if queue.probe():
            logger.info("Redis task queue connected: %s", settings.redis_url)
        elif fail_closed:
            logger.error(
                "Redis task queue unavailable (%s); queue is fail-closed",
                queue.degraded_reason,
            )
        else:
            logger.warning(
                "Redis unavailable (%s); falling back to SQLite queue", queue.degraded_reason
            )
            return SQLiteTaskQueue(database)
        return queue
    return SQLiteTaskQueue(database)
