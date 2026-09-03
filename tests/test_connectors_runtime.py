"""Phase 20.1: circuit breaker, retry, and per-tenant connector isolation."""

from __future__ import annotations

import unittest

from app.connectors import CustomerLookup, CustomerProfile, OrderDetails, OrderLookup
from app.connectors_runtime import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpen,
    CircuitBreakerRegistry,
    CircuitState,
    ResilientCRMConnector,
    ResilientKnowledgeConnector,
    ResilientOrderConnector,
    TransientConnectorError,
)

_CONFIG = CircuitBreakerConfig(
    failure_threshold=3,
    recovery_timeout_seconds=60.0,
    half_open_max_calls=1,
    retry_max_attempts=3,
    retry_base_backoff_seconds=0.0,
)


class _FakeClock:
    """A controllable monotonic clock for deterministic breaker timing."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _StaticOrderConn:
    def __init__(self) -> None:
        self.calls = 0

    def lookup_order(self, tenant_id, customer_ref, order_id):
        self.calls += 1
        return OrderLookup(
            ok=True,
            code="ok",
            order=OrderDetails(id=order_id, status="运输中", eta="2026-07-20", tracking_code="SF1"),
        )


class _TransientOrderConn:
    def __init__(self, fail_times: int) -> None:
        self.fail_times = fail_times
        self.calls = 0

    def lookup_order(self, tenant_id, customer_ref, order_id):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise TransientConnectorError("timeout")
        return OrderLookup(
            ok=True,
            code="ok",
            order=OrderDetails(id=order_id, status="运输中", eta="x", tracking_code="t"),
        )


class _AlwaysTransientConn:
    def __init__(self) -> None:
        self.calls = 0

    def lookup_order(self, tenant_id, customer_ref, order_id):
        self.calls += 1
        raise TransientConnectorError("always")


class _FailingOrderConn:
    def __init__(self) -> None:
        self.calls = 0

    def lookup_order(self, tenant_id, customer_ref, order_id):
        self.calls += 1
        raise ValueError("boom")


class CircuitBreakerStateTests(unittest.TestCase):
    def test_starts_closed(self) -> None:
        breaker = CircuitBreaker(CircuitBreakerConfig())
        self.assertEqual(breaker.state, CircuitState.CLOSED)

    def test_opens_after_threshold_failures(self) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60.0)
        )
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)

    def test_open_fast_fails_before_call(self) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=60.0)
        )
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitBreakerOpen):
            breaker.before_call()

    def test_half_open_after_recovery(self) -> None:
        # recovery_timeout=10 => OPEN stays OPEN until the clock advances past
        # the recovery window, then a probe is allowed (HALF_OPEN).
        clock = _FakeClock()
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock)
        )
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        clock.advance(10.0)
        breaker.before_call()  # promotes to HALF_OPEN and allows the probe
        self.assertEqual(breaker.state, CircuitState.HALF_OPEN)

    def test_half_open_success_closes(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock)
        )
        breaker.record_failure()
        clock.advance(10.0)
        breaker.before_call()  # HALF_OPEN
        breaker.record_success()
        self.assertEqual(breaker.state, CircuitState.CLOSED)
        self.assertEqual(breaker.failure_count, 0)

    def test_half_open_failure_reopens(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=10.0, clock=clock)
        )
        breaker.record_failure()
        clock.advance(10.0)
        breaker.before_call()  # HALF_OPEN
        breaker.record_failure()  # failed probe re-opens
        self.assertEqual(breaker.state, CircuitState.OPEN)
        with self.assertRaises(CircuitBreakerOpen):
            breaker.before_call()

    def test_half_open_allows_one_probe(self) -> None:
        clock = _FakeClock()
        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=10.0,
                half_open_max_calls=1,
                clock=clock,
            )
        )
        breaker.record_failure()
        clock.advance(10.0)
        breaker.before_call()  # consumes the single half-open slot
        with self.assertRaises(CircuitBreakerOpen):
            breaker.before_call()

    def test_success_resets_failure_count(self) -> None:
        breaker = CircuitBreaker(
            CircuitBreakerConfig(failure_threshold=3, recovery_timeout_seconds=60.0)
        )
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_success()
        self.assertEqual(breaker.failure_count, 0)
        # Two more failures must not open (count was reset).
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.CLOSED)


class CircuitBreakerRegistryTests(unittest.TestCase):
    def test_isolates_per_tenant(self) -> None:
        registry = CircuitBreakerRegistry(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=60.0)
        )
        a = registry.get("tenant-a", "order")
        b = registry.get("tenant-b", "order")
        self.assertIsNot(a, b)
        a.record_failure()
        self.assertEqual(a.state, CircuitState.OPEN)
        self.assertEqual(b.state, CircuitState.CLOSED)

    def test_isolates_per_connector(self) -> None:
        registry = CircuitBreakerRegistry(
            CircuitBreakerConfig(failure_threshold=1, recovery_timeout_seconds=60.0)
        )
        order = registry.get("t1", "order")
        crm = registry.get("t1", "crm")
        self.assertIsNot(order, crm)
        order.record_failure()
        self.assertEqual(order.state, CircuitState.OPEN)
        self.assertEqual(crm.state, CircuitState.CLOSED)

    def test_same_key_returns_same_breaker(self) -> None:
        registry = CircuitBreakerRegistry(_CONFIG)
        self.assertIs(registry.get("t1", "order"), registry.get("t1", "order"))


class ResilientOrderConnectorTests(unittest.TestCase):
    def _registry(self) -> CircuitBreakerRegistry:
        return CircuitBreakerRegistry(_CONFIG)

    def test_passthrough_success(self) -> None:
        inner = _StaticOrderConn()
        conn = ResilientOrderConnector(inner, self._registry())
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        self.assertEqual(inner.calls, 1)

    def test_open_returns_unavailable_without_calling_inner(self) -> None:
        inner = _StaticOrderConn()
        registry = self._registry()
        conn = ResilientOrderConnector(inner, registry)
        breaker = registry.get("t1", "order")
        # Force open.
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "unavailable")
        self.assertEqual(inner.calls, 0)

    def test_transient_retry_then_success(self) -> None:
        inner = _TransientOrderConn(fail_times=2)
        conn = ResilientOrderConnector(inner, self._registry())
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertTrue(result.ok)
        self.assertEqual(inner.calls, 3)

    def test_transient_exhausted_returns_unavailable(self) -> None:
        inner = _AlwaysTransientConn()
        conn = ResilientOrderConnector(inner, self._registry())
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "unavailable")
        self.assertEqual(inner.calls, _CONFIG.retry_max_attempts)

    def test_non_transient_error_degrades_to_unavailable(self) -> None:
        registry = CircuitBreakerRegistry(_CONFIG)
        inner = _FailingOrderConn()
        conn = ResilientOrderConnector(inner, registry)
        result = conn.lookup_order("t1", "CUST-1", "ORD-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "unavailable")
        self.assertEqual(inner.calls, 1)
        self.assertEqual(registry.get("t1", "order").failure_count, 1)


class _StaticKnowledgeConn:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, tenant_id, query, *, limit=3):
        self.calls += 1
        return []  # knowledge hits are exercised via the empty-fallback path


class _AlwaysTransientKnowledgeConn:
    def __init__(self) -> None:
        self.calls = 0

    def search(self, tenant_id, query, *, limit=3):
        self.calls += 1
        raise TransientConnectorError("timeout")


class ResilientKnowledgeConnectorTests(unittest.TestCase):
    def test_open_returns_empty_list(self) -> None:
        inner = _AlwaysTransientKnowledgeConn()
        registry = CircuitBreakerRegistry(_CONFIG)
        conn = ResilientKnowledgeConnector(inner, registry)
        # First call: retries exhaust, records failures, returns [].
        conn.search("t1", "配送")
        # Force the breaker open for a clean fast-fail assertion.
        breaker = registry.get("t1", "knowledge")
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        self.assertEqual(breaker.state, CircuitState.OPEN)
        before = inner.calls
        result = conn.search("t1", "配送")
        self.assertEqual(result, [])
        self.assertEqual(inner.calls, before)  # fast-failed, inner not called


class _StaticCRMConn:
    def __init__(self, lookup: CustomerLookup) -> None:
        self.lookup = lookup
        self.calls = 0

    def resolve_customer(self, tenant_id, customer_ref):
        self.calls += 1
        return self.lookup


class _TransientCRMConn:
    def __init__(self) -> None:
        self.calls = 0

    def resolve_customer(self, tenant_id, customer_ref):
        self.calls += 1
        raise TransientConnectorError("timeout")


class ResilientCRMConnectorTests(unittest.TestCase):
    def test_passthrough_success(self) -> None:
        lookup = CustomerLookup(
            ok=True, code="ok", profile=CustomerProfile(customer_ref="CUST-1", name="林嘉")
        )
        conn = ResilientCRMConnector(_StaticCRMConn(lookup), CircuitBreakerRegistry(_CONFIG))
        result = conn.resolve_customer("t1", "CUST-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.code, "ok")
        assert result.profile is not None
        self.assertEqual(result.profile.customer_ref, "CUST-1")

    def test_open_returns_unavailable(self) -> None:
        inner = _TransientCRMConn()
        registry = CircuitBreakerRegistry(_CONFIG)
        conn = ResilientCRMConnector(inner, registry)
        breaker = registry.get("t1", "crm")
        breaker.record_failure()
        breaker.record_failure()
        breaker.record_failure()
        result = conn.resolve_customer("t1", "CUST-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "unavailable")
        self.assertIsNone(result.profile)
        self.assertEqual(inner.calls, 0)


if __name__ == "__main__":
    unittest.main()
