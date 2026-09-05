"""Resilient connector runtime: circuit breaker, retry, and per-tenant isolation.

Phase 20.1: external systems fail, and a failure must not break conversation
correctness. This module wraps the connector contracts from
:mod:`app.connectors` with a uniform runtime guard:

- **Circuit breaker**: after ``failure_threshold`` consecutive failures the
  breaker opens and fast-fails subsequent calls; after
  ``recovery_timeout_seconds`` it enters half-open and lets one probe through;
  a probe success closes it, a probe failure re-opens it. State is isolated
  per ``(tenant_id, connector)`` so one tenant's flaky CRM cannot trip
  another tenant's.
- **Retry**: transient errors (``TransientConnectorError`` -- timeouts and
  network blips) are retried with exponential back-off. Only idempotent
  reads are retried; the connector contracts here are all read-only.
- **Degradation**: when the breaker is open (or retries exhausted) the
  wrapper returns a sentinel "unavailable" result instead of raising, so
  Order/CRM escalate to a human and Knowledge can fall back to the built-in
  FTS retrieval -- never a crash, never a cross-customer leak.

The sandbox connectors never raise transient errors, so wrapping them is
behaviour-preserving (the existing 211+ tests stay green). Real connectors
(HTTP) raise ``TransientConnectorError`` on timeout/network to engage the
guard.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any

from app.connectors import (
    CRMConnector,
    CustomerLookup,
    KnowledgeConnector,
    KnowledgeHit,
    OrderConnector,
    OrderLookup,
)

logger = logging.getLogger(__name__)


class TransientConnectorError(RuntimeError):
    """A transient connector failure (timeout, network) that retry can absorb."""


class CircuitBreakerOpen(RuntimeError):
    """Raised when a call is attempted against an open circuit."""


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass(frozen=True)
class CircuitBreakerConfig:
    """Configuration for a circuit breaker (Phase 20.1)."""

    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1
    retry_max_attempts: int = 3
    retry_base_backoff_seconds: float = 0.05
    # Injectable clock so tests can advance time deterministically without
    # sleeping; production leaves the default (time.monotonic).
    clock: Callable[[], float] = time.monotonic

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be positive")
        if self.recovery_timeout_seconds < 0:
            raise ValueError("recovery_timeout_seconds cannot be negative")
        if self.half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be positive")
        if self.retry_max_attempts < 1:
            raise ValueError("retry_max_attempts must be positive")
        if self.retry_base_backoff_seconds < 0:
            raise ValueError("retry_base_backoff_seconds cannot be negative")


class CircuitBreaker:
    """A single circuit-breaker state machine (Phase 20.1).

    States: closed -> open (after ``failure_threshold`` consecutive failures)
    -> half-open (after ``recovery_timeout_seconds``) -> closed (probe
    success) or open (probe failure). Thread-safe.
    """

    def __init__(self, config: CircuitBreakerConfig) -> None:
        self.config = config
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at = 0.0
        self._half_open_in_flight = 0
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_recover()
            return self._state

    @property
    def failure_count(self) -> int:
        with self._lock:
            return self._failures

    def _maybe_recover(self) -> None:
        # Caller holds the lock. Promote OPEN -> HALF_OPEN once the recovery
        # timeout has elapsed so a single probe call is allowed through.
        if (
            self._state == CircuitState.OPEN
            and self.config.clock() - self._opened_at >= self.config.recovery_timeout_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._half_open_in_flight = 0

    def before_call(self) -> None:
        """Raise ``CircuitBreakerOpen`` if the call must be fast-failed."""
        with self._lock:
            self._maybe_recover()
            if self._state == CircuitState.OPEN:
                raise CircuitBreakerOpen("circuit is open")
            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_in_flight >= self.config.half_open_max_calls:
                    raise CircuitBreakerOpen("half-open probe already in flight")
                self._half_open_in_flight += 1

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # A successful probe closes the circuit.
                self._state = CircuitState.CLOSED
                self._half_open_in_flight = 0
            self._failures = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                # A failed probe re-opens the circuit immediately.
                self._state = CircuitState.OPEN
                self._opened_at = self.config.clock()
                self._half_open_in_flight = 0
                return
            self._failures += 1
            if (
                self._state == CircuitState.CLOSED
                and self._failures >= self.config.failure_threshold
            ):
                # Only the CLOSED -> OPEN transition stamps the cooldown start.
                # Late failures while already OPEN (a call that passed
                # before_call before another thread tripped the breaker) must
                # not extend the cooldown indefinitely.
                self._state = CircuitState.OPEN
                self._opened_at = self.config.clock()

    def reset(self) -> None:
        """Force the breaker back to closed (for tests / manual ops)."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._half_open_in_flight = 0


class CircuitBreakerRegistry:
    """Per-tenant, per-connector breaker isolation (Phase 20.1)."""

    def __init__(self, config: CircuitBreakerConfig | None = None) -> None:
        self.config = config or CircuitBreakerConfig()
        self._breakers: dict[tuple[str, str], CircuitBreaker] = {}
        self._lock = Lock()

    def get(self, tenant_id: str, connector_name: str) -> CircuitBreaker:
        key = (tenant_id, connector_name)
        with self._lock:
            breaker = self._breakers.get(key)
            if breaker is None:
                breaker = CircuitBreaker(self.config)
                self._breakers[key] = breaker
            return breaker

    def states(self) -> dict[str, dict[str, str]]:
        """Snapshot of all breakers by tenant -> connector -> state (ops only)."""
        with self._lock:
            snapshot: dict[str, dict[str, str]] = {}
            for (tenant_id, name), breaker in self._breakers.items():
                snapshot.setdefault(tenant_id, {})[name] = breaker.state.value
            return snapshot


def _resilient_call(
    fn: Callable[[], Any],
    registry: CircuitBreakerRegistry,
    tenant_id: str,
    connector_name: str,
    config: CircuitBreakerConfig,
    fallback: Callable[[], Any],
) -> Any:
    """Run ``fn`` behind the breaker with retry; degrade to ``fallback``.

    ``before_call`` runs before *every* attempt, so a HALF_OPEN probe failure
    that re-opens the circuit also gates the retry that follows it (the retry
    cannot hammer an open circuit), and a probe success closes the circuit.
    """
    breaker = registry.get(tenant_id, connector_name)
    for attempt in range(config.retry_max_attempts):
        try:
            breaker.before_call()
        except CircuitBreakerOpen:
            # The circuit is open (fast-fail) or tripped while we were waiting
            # to retry. Degrade now instead of hammering a hot circuit.
            logger.info(
                "connector.circuit_open",
                extra={"connector": connector_name, "tenant_id": tenant_id},
            )
            return fallback()
        try:
            result = fn()
            breaker.record_success()
            return result
        except TransientConnectorError:
            breaker.record_failure()
            if attempt < config.retry_max_attempts - 1:
                if config.retry_base_backoff_seconds > 0:
                    time.sleep(config.retry_base_backoff_seconds * (2**attempt))
                continue
            logger.warning(
                "connector.transient_exhausted",
                extra={"connector": connector_name, "tenant_id": tenant_id},
            )
            return fallback()
        except Exception:
            # Non-transient integration bugs (4xx, schema errors) still trip the
            # breaker, but must degrade to the sentinel instead of 500-ing a turn.
            breaker.record_failure()
            logger.warning(
                "connector.non_transient",
                extra={"connector": connector_name, "tenant_id": tenant_id},
                exc_info=True,
            )
            return fallback()
    return fallback()


class ResilientOrderConnector:
    """Order connector with circuit breaker + retry (Phase 20.1)."""

    def __init__(
        self,
        inner: OrderConnector,
        registry: CircuitBreakerRegistry,
        config: CircuitBreakerConfig | None = None,
        *,
        connector_name: str = "order",
    ) -> None:
        self.inner = inner
        self.registry = registry
        self.config = config or registry.config
        self.connector_name = connector_name

    def lookup_order(self, tenant_id: str, customer_ref: str | None, order_id: str) -> OrderLookup:
        return _resilient_call(
            lambda: self.inner.lookup_order(tenant_id, customer_ref, order_id),
            self.registry,
            tenant_id,
            self.connector_name,
            self.config,
            fallback=lambda: OrderLookup(ok=False, code="unavailable"),
        )


class ResilientKnowledgeConnector:
    """Knowledge connector with circuit breaker + retry (Phase 20.1).

    On degradation returns an empty hit list so the orchestrator falls back
    to the built-in FTS retrieval path.
    """

    def __init__(
        self,
        inner: KnowledgeConnector,
        registry: CircuitBreakerRegistry,
        config: CircuitBreakerConfig | None = None,
        *,
        connector_name: str = "knowledge",
    ) -> None:
        self.inner = inner
        self.registry = registry
        self.config = config or registry.config
        self.connector_name = connector_name

    def draft_article(
        self,
        tenant_id: str,
        *,
        title: str,
        content: str,
        tags: list[str],
        category: str,
        source_url: str,
        language: str | None,
        actor_id: str,
    ) -> dict[str, Any]:
        """Governed write through the breaker: degradation must FAIL LOUDLY.

        Unlike ``search`` (which degrades to an empty hit list so the agent
        can fall back), a mutating call that silently "succeeded" without
        writing would corrupt the audit trail — a CircuitBreakerOpen raises.
        """
        breaker = self.registry.get(tenant_id, self.connector_name)
        breaker.before_call()
        return self.inner.draft_article(
            tenant_id,
            title=title,
            content=content,
            tags=tags,
            category=category,
            source_url=source_url,
            language=language,
            actor_id=actor_id,
        )

    def search(self, tenant_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]:
        return _resilient_call(
            lambda: self.inner.search(tenant_id, query, limit=limit),
            self.registry,
            tenant_id,
            self.connector_name,
            self.config,
            fallback=list,
        )


class ResilientCRMConnector:
    """CRM connector with circuit breaker + retry (Phase 20.1)."""

    def __init__(
        self,
        inner: CRMConnector,
        registry: CircuitBreakerRegistry,
        config: CircuitBreakerConfig | None = None,
        *,
        connector_name: str = "crm",
    ) -> None:
        self.inner = inner
        self.registry = registry
        self.config = config or registry.config
        self.connector_name = connector_name

    def resolve_customer(self, tenant_id: str, customer_ref: str) -> CustomerLookup:
        return _resilient_call(
            lambda: self.inner.resolve_customer(tenant_id, customer_ref),
            self.registry,
            tenant_id,
            self.connector_name,
            self.config,
            fallback=lambda: CustomerLookup(ok=False, code="unavailable"),
        )
