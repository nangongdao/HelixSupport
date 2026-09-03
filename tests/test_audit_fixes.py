"""Regression tests for bugs found during the Phase 19/20/21 audit.

Each test reproduces a concrete defect that the audit agents confirmed and
that the pre-fix code exhibited; the tests pin the fixed behavior so the
defects cannot silently return.

Covered fixes:
- CircuitBreaker: late failures while OPEN no longer extend the cooldown;
  retries re-check the breaker (no un-gated retry past an OPEN/HALF_OPEN
  probe failure).
- PromptRegistry: ``set_canary`` on the active version is rejected instead
  of silently disabling the active channel.
- HTTP connectors: 200 with an empty/invalid payload is ``not_found``, never
  a fabricated success.
- Webhook delivery: per-row claim/POST/outcome transactions (a failing row no
  longer rolls back already-delivered rows); permanent 4xx dead-letters
  immediately instead of retrying to exhaustion; a reopened conversation that
  breaches SLA twice emits a second event; deleting an endpoint dead-letters
  its pending deliveries.
- Knowledge gaps: a negative-rated message whose citations metadata is a
  JSON string or malformed JSON no longer 500s the supervisor endpoint.
- Tenant model policy: a prompt version whose model_ref is outside the
  tenant's allow-list degrades to the deterministic path and audits
  ``turn.model_denied``.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from app.connectors_http import HttpConnectorConfig, HttpCRMConnector, HttpOrderConnector
from app.connectors_runtime import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerRegistry,
    ResilientOrderConnector,
    TransientConnectorError,
)
from app.database import Database
from app.prompts import PromptRegistry
from app.webhooks import (
    EVENT_CONVERSATION_CREATED,
    EVENT_CONVERSATION_SLA_BREACHED,
    WebhookConfig,
    WebhookService,
)

ADMIN_KEY = "audit-admin-key-001"
VIEWER_KEY = "audit-viewer-key-001"
OTHER_ADMIN_KEY = "audit-other-admin-001"


def _public_resolve(_host: str, _port: int) -> list[str]:
    return ["93.184.216.34"]


# ---------------------------------------------------------------------------
# CircuitBreaker audit fixes
# ---------------------------------------------------------------------------


class CircuitBreakerAuditTests(unittest.TestCase):
    def test_late_failure_in_open_does_not_extend_cooldown(self) -> None:
        clock = {"now": 0.0}

        def fake_clock() -> float:
            return clock["now"]

        breaker = CircuitBreaker(
            CircuitBreakerConfig(
                failure_threshold=2,
                recovery_timeout_seconds=30.0,
                clock=fake_clock,
            )
        )
        # Trip the breaker (2 failures) at t=0.
        breaker.before_call()
        breaker.record_failure()
        breaker.before_call()
        breaker.record_failure()
        self.assertEqual(breaker.state, "open")
        # A late failure arrives at t=20 while OPEN (a call that passed
        # before_call before the breaker tripped).
        clock["now"] = 20.0
        breaker.record_failure()
        # Cooldown must still be anchored to the original trip: at t=30 the
        # breaker recovers even though the late failure landed at t=20.
        clock["now"] = 30.0
        self.assertEqual(breaker.state, "half_open")

    def test_retry_rechecks_breaker_after_probe_failure(self) -> None:
        """A failed half-open probe re-opens the circuit; the retry must not
        run against the open circuit and then succeed into an OPEN breaker."""
        clock = {"now": 0.0}

        def fake_clock() -> float:
            return clock["now"]

        calls = {"n": 0}

        def flaky_then_ok() -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise TransientConnectorError("blip")
            return "ok"

        registry = CircuitBreakerRegistry(
            CircuitBreakerConfig(
                failure_threshold=1,
                recovery_timeout_seconds=1.0,
                retry_max_attempts=3,
                retry_base_backoff_seconds=0.0,
                clock=fake_clock,
            )
        )
        connector = ResilientOrderConnector(
            _FakeOrderConnector(flaky_then_ok), registry, registry.config
        )
        # First call: probe fails -> circuit opens; retries are blocked.
        clock["now"] = 0.0
        result = connector.lookup_order("t", "c", "ORD-1")
        self.assertEqual(result.code, "unavailable")
        # The breaker is OPEN, so a healthy call fast-fails.
        result = connector.lookup_order("t", "c", "ORD-1")
        self.assertEqual(result.code, "unavailable")
        # After recovery the next probe succeeds and closes the circuit.
        clock["now"] = 2.0
        result = connector.lookup_order("t", "c", "ORD-1")
        self.assertEqual(result.code, "ok")
        self.assertEqual(registry.get("t", "order").state, "closed")


class _FakeOrderConnector:
    def __init__(self, behavior: Any) -> None:
        self._behavior = behavior

    def lookup_order(self, tenant_id: str, customer_ref: str | None, order_id: str) -> Any:
        from app.connectors import OrderDetails, OrderLookup

        try:
            value = self._behavior()
        except TransientConnectorError:
            raise
        if value == "ok":
            return OrderLookup(
                ok=True,
                code="ok",
                order=OrderDetails(id=order_id, status="shipped", eta=None, tracking_code=None),
            )
        raise TransientConnectorError("boom")


# ---------------------------------------------------------------------------
# PromptRegistry canary guard
# ---------------------------------------------------------------------------


class PromptCanaryGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "prompts.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")
        self.registry = PromptRegistry(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_set_canary_on_active_version_rejected(self) -> None:
        v1 = self.registry.create_version(
            None, "triage_prompt", "1.0", "body 10", "model-a", "admin"
        )
        self.registry.activate(None, v1.id, "admin")
        with self.assertRaises(ValueError):
            self.registry.set_canary(None, v1.id, "admin")
        # The active channel must still resolve.
        active, channel = self.registry.resolve_prompt("demo", "triage_prompt", "conv-1", 0.0)
        self.assertIsNotNone(active)
        self.assertEqual(channel, "active")

    def test_canary_flow_still_works_with_separate_version(self) -> None:
        v1 = self.registry.create_version(
            None, "triage_prompt", "1.0", "body 10", "model-a", "admin"
        )
        v2 = self.registry.create_version(
            None, "triage_prompt", "2.0", "body 20", "model-b", "admin"
        )
        self.registry.activate(None, v1.id, "admin")
        self.registry.set_canary(None, v2.id, "admin")
        active, channel = self.registry.resolve_prompt("demo", "triage_prompt", "conv-1", 0.0)
        self.assertEqual(channel, "active")
        assert active is not None
        self.assertEqual(active.version, "1.0")


# ---------------------------------------------------------------------------
# HTTP connector payload validation
# ---------------------------------------------------------------------------


class HttpConnectorPayloadTests(unittest.TestCase):
    def test_order_200_empty_payload_is_not_found(self) -> None:
        config = HttpConnectorConfig(base_url="https://orders.example.com")
        connector = HttpOrderConnector(config, transport=lambda *a, **k: (200, {}))
        result = connector.lookup_order("t", "CUST-1", "ORD-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")

    def test_crm_200_empty_payload_is_not_found(self) -> None:
        config = HttpConnectorConfig(base_url="https://crm.example.com")
        connector = HttpCRMConnector(config, transport=lambda *a, **k: (200, {}))
        result = connector.resolve_customer("t", "CUST-1")
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "not_found")

    def test_order_200_valid_payload_still_ok(self) -> None:
        config = HttpConnectorConfig(base_url="https://orders.example.com")
        connector = HttpOrderConnector(
            config,
            transport=lambda *a, **k: (
                200,
                {"order": {"id": "ORD-1", "status": "shipped", "eta": "2026-01-02"}},
            ),
        )
        result = connector.lookup_order("t", "CUST-1", "ORD-1")
        self.assertTrue(result.ok)
        assert result.order is not None
        self.assertEqual(result.order.status, "shipped")


# ---------------------------------------------------------------------------
# Webhook delivery audit fixes
# ---------------------------------------------------------------------------


class _RaisingAfterTransport:
    """Succeeds for the first call, then raises for subsequent calls."""

    def __init__(self, ok_status: int = 200) -> None:
        self.calls = 0
        self.ok_status = ok_status

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, str | None]:
        self.calls += 1
        if self.calls == 1:
            return self.ok_status, None
        raise RuntimeError("transport exploded")


class WebhookAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "webhooks.db"
        self.database = Database(self.db_path, pool_size=2)
        with self.database.connect() as conn:
            from app.migrations import all_migrations, run_migrations

            run_migrations(conn, all_migrations())
        self.database.ensure_tenant("tenant-1", "One")
        self.service = WebhookService(
            self.database,
            config=WebhookConfig(max_attempts=5, base_backoff_seconds=0.0),
            resolve_host=_public_resolve,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _register(self) -> str:
        return self.service.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )["id"]

    def test_one_failure_does_not_rollback_delivered_row(self) -> None:
        endpoint_id = self._register()
        # Emit two deliveries for the same endpoint.
        self.service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c2"}, "evt-2"
        )
        transport = _RaisingAfterTransport()
        service = WebhookService(
            self.database,
            transport=transport,
            config=WebhookConfig(max_attempts=5, base_backoff_seconds=0.0),
            resolve_host=_public_resolve,
        )
        service.deliver_pending()
        deliveries = service.list_deliveries("tenant-1", endpoint_id)
        by_event = {d["event_id"]: d["status"] for d in deliveries}
        # The first delivery succeeded (and stays delivered); the second hit
        # the transport exception and is claimed as sending (recovered later).
        self.assertEqual(by_event["evt-1"], "delivered")

    def test_permanent_4xx_dead_letters_immediately(self) -> None:
        endpoint_id = self._register()
        self.service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        transport = _StatusTransport(400)
        service = WebhookService(
            self.database,
            transport=transport,
            config=WebhookConfig(max_attempts=5, base_backoff_seconds=0.0),
            resolve_host=_public_resolve,
        )
        service.deliver_pending()
        delivery = service.list_deliveries("tenant-1", endpoint_id)[0]
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["attempts"], 1)
        self.assertEqual(delivery["last_response_code"], 400)

    def test_transient_5xx_still_retries_to_exhaustion(self) -> None:
        endpoint_id = self._register()
        self.service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        # max_attempts is snapshotted on the delivery row at emit time, so the
        # delivering service must carry the same config (setUp uses 5).
        transport = _StatusTransport(503)
        service = WebhookService(
            self.database,
            transport=transport,
            config=WebhookConfig(max_attempts=5, base_backoff_seconds=0.0),
            resolve_host=_public_resolve,
        )
        for _ in range(5):
            service.deliver_pending()
        delivery = service.list_deliveries("tenant-1", endpoint_id)[0]
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["attempts"], 5)
        self.assertEqual(delivery["last_response_code"], 503)

    def test_sla_rebreach_after_reopen_emits_second_event(self) -> None:
        endpoint = self.service.register_endpoint(
            "tenant-1",
            "https://example.com/hook",
            [EVENT_CONVERSATION_SLA_BREACHED],
            "secret-123",
        )
        overdue = self.database.create_conversation("tenant-1", "C", None, "web", "admin", 120)
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE conversations SET sla_due_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (overdue["id"],),
            )
            conn.commit()
        self.assertEqual(self.service.check_sla_breaches(), 1)
        # Resolve, reopen with a fresh SLA window, breach again.
        from app.config import Settings
        from app.orchestrator import ConversationOrchestrator

        orchestrator = ConversationOrchestrator(
            self.database, Settings(database_path=self.db_path, auth_mode="demo")
        )
        orchestrator.resolve("tenant-1", overdue["id"], "admin")
        orchestrator.reopen("tenant-1", overdue["id"], "admin")
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE conversations SET sla_due_at = '2021-01-01T00:00:00+00:00' WHERE id = ?",
                (overdue["id"],),
            )
            conn.commit()
        self.assertEqual(self.service.check_sla_breaches(), 1)
        deliveries = self.service.list_deliveries("tenant-1", endpoint["id"])
        self.assertEqual(len(deliveries), 2)
        event_ids = {d["event_id"] for d in deliveries}
        self.assertEqual(len(event_ids), 2)

    def test_delete_endpoint_dead_letters_pending_deliveries(self) -> None:
        endpoint_id = self._register()
        self.service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.assertTrue(self.service.delete_endpoint("tenant-1", endpoint_id))
        delivery = self.service.list_deliveries("tenant-1", endpoint_id)[0]
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["last_error"], "endpoint deleted")
        # Nothing is delivered after deletion.
        self.service.deliver_pending()
        delivery = self.service.list_deliveries("tenant-1", endpoint_id)[0]
        self.assertEqual(delivery["status"], "dead")


class _StatusTransport:
    def __init__(self, status: int) -> None:
        self.status = status

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, str | None]:
        return self.status, None


# ---------------------------------------------------------------------------
# Knowledge gaps malformed-JSON robustness
# ---------------------------------------------------------------------------


class KnowledgeGapsRobustnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gaps.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _seed_negative_feedback(self, metadata_json: str) -> None:
        from app.database import utc_now

        now = utc_now()
        conv = self.database.create_conversation("demo", "C", "CUST-1", "web", "admin", 120)
        with self.database.connect() as conn:
            conn.execute(
                "INSERT INTO messages (id, tenant_id, conversation_id, role, author, "
                "content, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "msg-gaps-1",
                    "demo",
                    conv["id"],
                    "assistant",
                    "knowledge",
                    "answer",
                    metadata_json,
                    now,
                ),
            )
            conn.execute(
                "INSERT INTO feedback (id, tenant_id, conversation_id, message_id, "
                "actor, rating, reason, created_at, updated_at) "
                "VALUES ('fb-gaps-1', 'demo', ?, 'msg-gaps-1', 'admin', -1, NULL, ?, ?)",
                (conv["id"], now, now),
            )
            conn.commit()

    def test_string_citations_does_not_500(self) -> None:
        self._seed_negative_feedback('{"citations": "not-an-array"}')
        gaps = self.database.list_knowledge_gaps("demo")
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]["message_id"], "msg-gaps-1")

    def test_malformed_metadata_does_not_500(self) -> None:
        self._seed_negative_feedback("this is not json")
        gaps = self.database.list_knowledge_gaps("demo")
        self.assertEqual(len(gaps), 1)

    def test_real_citations_excluded(self) -> None:
        self._seed_negative_feedback(
            '{"citations": [{"id": "kb-1", "title": "t", "url": "u", "version": "1"}]}'
        )
        self.assertEqual(self.database.list_knowledge_gaps("demo"), [])


# ---------------------------------------------------------------------------
# Tenant model policy enforcement (allowed_models)
# ---------------------------------------------------------------------------


class AllowedModelsEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "policy.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_model_ref_outside_allowlist_denied(self) -> None:
        from app.config import Settings
        from app.orchestrator import ConversationOrchestrator

        self.database.set_tenant_model_policy("demo", ["allowed-model"], None)
        registry = PromptRegistry(self.database)
        v1 = registry.create_version(
            None, "triage_prompt", "1.0", "body 10", "blocked-model", "admin"
        )
        registry.activate(None, v1.id, "admin")
        orchestrator = ConversationOrchestrator(
            self.database, Settings(database_path=self.db_path, auth_mode="demo")
        )
        conv = self.database.create_conversation("demo", "C", None, "web", "admin", 120)
        # No model provider configured -> the rules path is used regardless,
        # but the model-denied audit must still be emitted when a prompt with
        # a disallowed model_ref is resolved.
        orchestrator.handle_customer_message(
            "demo", conv["id"], "配送一般多久能到？", "admin", "idem-policy-1"
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id='demo' "
                "AND event_type='turn.model_denied'"
            ).fetchall()
        self.assertEqual(len(rows), 1)

    def test_model_ref_in_allowlist_not_denied(self) -> None:
        from app.config import Settings
        from app.orchestrator import ConversationOrchestrator

        self.database.set_tenant_model_policy("demo", ["allowed-model"], None)
        registry = PromptRegistry(self.database)
        v1 = registry.create_version(
            None, "triage_prompt", "1.0", "body 10", "allowed-model", "admin"
        )
        registry.activate(None, v1.id, "admin")
        orchestrator = ConversationOrchestrator(
            self.database, Settings(database_path=self.db_path, auth_mode="demo")
        )
        conv = self.database.create_conversation("demo", "C", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            "demo", conv["id"], "配送一般多久能到？", "admin", "idem-policy-2"
        )
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id='demo' "
                "AND event_type='turn.model_denied'"
            ).fetchall()
        self.assertEqual(len(rows), 0)


if __name__ == "__main__":
    unittest.main()
