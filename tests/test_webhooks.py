"""Phase 20.5: outbound webhook delivery, API, and orchestration wiring.

Covers the contract the integration story depends on:
- The service layer registers tenant endpoints, emits deduplicated events,
  delivers with HMAC signatures, retries with back-off, dead-letters past
  max attempts, and recovers stuck deliveries after their lease expires.
- The API exposes registration/history with admin RBAC, tenant binding,
  and audit events.
- The orchestrator emits ``conversation.escalated``/``conversation.resolved``
  and the worker housekeeping scan emits ``conversation.sla_breached``
  exactly once per conversation.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database, utc_now
from app.jobs import TurnJobWorker
from app.main import create_app
from app.migrations import all_migrations, run_migrations
from app.orchestrator import ConversationOrchestrator
from app.webhooks import (
    EVENT_CONVERSATION_CREATED,
    EVENT_CONVERSATION_ESCALATED,
    EVENT_CONVERSATION_RESOLVED,
    EVENT_CONVERSATION_SLA_BREACHED,
    WebhookConfig,
    WebhookService,
    _sign,
)


def _public_resolve(_host: str, _port: int) -> list[str]:
    """Offline resolver: treat every hostname as a public unicast address."""
    return ["93.184.216.34"]


ADMIN_KEY = "webhook-admin-key-001"
OTHER_ADMIN_KEY = "webhook-other-admin-001"
OPERATOR_KEY = "webhook-operator-key-01"


class RecordingTransport:
    """Records every delivery POST; returns a configurable status."""

    def __init__(self, status: int = 200) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.status = status

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, str | None]:
        self.calls.append((url, dict(headers), body))
        return self.status, None


class WebhookServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        with self.database.connect() as conn:
            run_migrations(conn, all_migrations())
        self.database.ensure_tenant("tenant-1", "One")
        self.database.ensure_tenant("tenant-2", "Two")
        self.transport = RecordingTransport()
        self.webhooks = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _endpoint_id(self, tenant_id: str = "tenant-1") -> str:
        endpoint = self.webhooks.register_endpoint(
            tenant_id, "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )
        return endpoint["id"]

    def _deliveries(self, tenant_id: str, endpoint_id: str) -> list[dict[str, Any]]:
        return self.webhooks.list_deliveries(tenant_id, endpoint_id)

    # ------------------------------------------------------------- endpoints

    def test_register_requires_http_url(self) -> None:
        with self.assertRaises(ValueError):
            self.webhooks.register_endpoint("tenant-1", "ftp://nope", ["conversation.created"], "s")
        with self.assertRaises(ValueError):
            self.webhooks.register_endpoint("tenant-1", "not-a-url", ["conversation.created"], "s")

    def test_register_rejects_private_and_loopback_targets(self) -> None:
        blocked = (
            "http://127.0.0.1/hook",
            "http://localhost/hook",
            "http://[::1]/hook",
            "http://10.0.0.8/hook",
            "http://192.168.1.20/hook",
            "http://169.254.169.254/latest/meta-data",
            "http://metadata.google.internal/",
        )
        for url in blocked:
            with self.subTest(url=url), self.assertRaises(ValueError):
                self.webhooks.register_endpoint(
                    "tenant-1", url, ["conversation.created"], "secret-123"
                )
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", ["conversation.created"], "secret-123"
        )
        self.assertEqual(endpoint["url"], "https://example.com/hook")

    def test_deliver_dead_letters_private_url_already_stored(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-ssrf"
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_endpoints SET url=? WHERE id=?",
                ("http://127.0.0.1/hook", endpoint["id"]),
            )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results["dead"], 1)
        self.assertEqual(self.transport.calls, [])
        delivery = self._deliveries("tenant-1", endpoint["id"])[0]
        self.assertEqual(delivery["status"], "dead")

    def test_register_requires_events_and_secret(self) -> None:
        with self.assertRaises(ValueError):
            self.webhooks.register_endpoint("tenant-1", "https://x.example", [], "secret-123")
        with self.assertRaises(ValueError):
            self.webhooks.register_endpoint(
                "tenant-1", "https://x.example", ["conversation.created"], ""
            )

    def test_register_rejects_unsupported_events(self) -> None:
        with self.assertRaises(ValueError):
            self.webhooks.register_endpoint(
                "tenant-1", "https://x.example", ["conversation.magic"], "secret-123"
            )

    def test_list_and_delete_endpoint(self) -> None:
        endpoint_id = self._endpoint_id()
        listed = self.webhooks.list_endpoints("tenant-1")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], endpoint_id)
        self.assertEqual(listed[0]["events"], [EVENT_CONVERSATION_CREATED])
        self.assertTrue(self.webhooks.delete_endpoint("tenant-1", endpoint_id))
        self.assertEqual(self.webhooks.list_endpoints("tenant-1"), [])

    def test_delete_is_tenant_bound(self) -> None:
        endpoint_id = self._endpoint_id()
        # tenant-2 cannot delete tenant-1's endpoint and never sees it.
        self.assertFalse(self.webhooks.delete_endpoint("tenant-2", endpoint_id))
        self.assertEqual(self.webhooks.list_endpoints("tenant-2"), [])

    # ----------------------------------------------------------------- emit

    def test_emit_creates_one_delivery_per_endpoint(self) -> None:
        first = self._endpoint_id()
        second = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/other", [EVENT_CONVERSATION_CREATED], "secret-456"
        )["id"]
        enqueued = self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.assertEqual(enqueued, 2)
        self.assertEqual(len(self._deliveries("tenant-1", first)), 1)
        self.assertEqual(len(self._deliveries("tenant-1", second)), 1)

    def test_emit_skips_unsubscribed_events(self) -> None:
        endpoint_id = self._endpoint_id()
        enqueued = self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_RESOLVED, {"conversation_id": "c1"}, "evt-1"
        )
        self.assertEqual(enqueued, 0)
        self.assertEqual(self._deliveries("tenant-1", endpoint_id), [])

    def test_emit_does_not_cross_tenants(self) -> None:
        endpoint_id = self._endpoint_id("tenant-1")
        # tenant-2 has no endpoints; even if it did, they could not observe
        # tenant-1's events.
        emitted = self.webhooks.emit_event(
            "tenant-2", EVENT_CONVERSATION_CREATED, {"conversation_id": "c2"}, "evt-9"
        )
        self.assertEqual(emitted, 0)
        self.assertEqual(self._deliveries("tenant-1", endpoint_id), [])

    def test_emit_dedups_same_event_id(self) -> None:
        endpoint_id = self._endpoint_id()
        first = self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-dedup"
        )
        second = self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-dedup"
        )
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(self._deliveries("tenant-1", endpoint_id)), 1)
        self.assertEqual(self._deliveries("tenant-1", endpoint_id)[0]["event_id"], "evt-dedup")

    # --------------------------------------------------------------- deliver

    def test_deliver_success_is_signed_and_delivered(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 1, "retried": 0, "dead": 0})
        self.assertEqual(len(self.transport.calls), 1)
        url, headers, body = self.transport.calls[0]
        self.assertEqual(url, "https://example.com/hook")
        payload = json.loads(body.decode("utf-8"))
        self.assertEqual(payload["event_id"], "evt-1")
        self.assertEqual(payload["event_type"], EVENT_CONVERSATION_CREATED)
        self.assertEqual(payload["payload"], {"conversation_id": "c1"})
        timestamp = headers["X-Helix-Timestamp"]
        expected = _sign("shhh-secret", timestamp, body)
        self.assertEqual(headers["X-Helix-Signature"], expected)
        delivery = self._deliveries("tenant-1", endpoint["id"])[0]
        self.assertEqual(delivery["status"], "delivered")
        self.assertEqual(delivery["attempts"], 1)

    def test_deliver_retries_transient_then_dead_letters(self) -> None:
        service = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3, base_backoff_seconds=0.0),
            resolve_host=_public_resolve,
        )
        endpoint = service.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )
        service.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.transport.status = 503
        # Zero backoff keeps every attempt due immediately; three failures at
        # max_attempts=3 exhaust and dead-letter the delivery.
        service.deliver_pending()
        service.deliver_pending()
        service.deliver_pending()
        delivery = service.list_deliveries("tenant-1", endpoint["id"])[0]
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["attempts"], 3)
        self.assertEqual(delivery["last_response_code"], 503)

    def test_deliver_network_failure_is_transient(self) -> None:
        endpoint_id = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )["id"]
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.transport.status = 0
        results = self.webhooks.deliver_pending()
        self.assertEqual(results["retried"], 1)
        delivery = self._deliveries("tenant-1", endpoint_id)[0]
        self.assertEqual(delivery["status"], "pending")
        self.assertEqual(delivery["attempts"], 1)

    def test_deliver_respects_next_attempt_at_backoff(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "secret-123"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        self.transport.status = 503
        self.webhooks.deliver_pending()
        # Immediately after a retry the delivery is not due again.
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 0, "retried": 0, "dead": 0})
        self.assertEqual(len(self.transport.calls), 1)

    def test_deliver_reclaims_stuck_sending_after_lease(self) -> None:
        endpoint_id = self._endpoint_id()
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        delivery_id = self._deliveries("tenant-1", endpoint_id)[0]["id"]
        # Simulate a crashed instance: claim the row, leave it stale.
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET status='sending', updated_at=? WHERE id=?",
                ("2020-01-01T00:00:00+00:00", delivery_id),
            )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results["delivered"], 1)
        self.assertEqual(len(self.transport.calls), 1)

    def test_deliver_skips_fresh_sending_row(self) -> None:
        endpoint_id = self._endpoint_id()
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET status='sending', updated_at=? WHERE endpoint_id=?",
                (utc_now(), endpoint_id),
            )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results["delivered"], 0)
        self.assertEqual(self.transport.calls, [])

    # ------------------------------------------------------------------- sla

    def _overdue_conversation(self, tenant_id: str = "tenant-1") -> str:
        conv = self.database.create_conversation(
            tenant_id, "Overdue Customer", None, "web", "admin", 120
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE conversations SET sla_due_at=? WHERE id=?",
                ("2020-01-01T00:00:00+00:00", conv["id"]),
            )
        return conv["id"]

    def test_sla_breach_emitted_once_per_conversation(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_SLA_BREACHED], "secret-123"
        )
        conv_id = self._overdue_conversation()
        self.assertEqual(self.webhooks.check_sla_breaches(), 1)
        self.assertEqual(self.webhooks.check_sla_breaches(), 0)
        deliveries = self.webhooks.list_deliveries("tenant-1")
        self.assertEqual(len(deliveries), 1)
        delivery = deliveries[0]
        self.assertEqual(delivery["event_type"], EVENT_CONVERSATION_SLA_BREACHED)
        # The event id carries the breach deadline so a *new* breach episode
        # (reopened conversation with a fresh SLA window) is a distinct event.
        self.assertEqual(delivery["event_id"], f"sla_breach:{conv_id}:2020-01-01T00:00:00+00:00")
        with self.database.connect() as conn:
            payload = json.loads(
                conn.execute(
                    "SELECT payload_json FROM webhook_deliveries WHERE id=?",
                    (delivery["id"],),
                ).fetchone()["payload_json"]
            )
        self.assertEqual(payload["conversation_id"], conv_id)

    def test_sla_breach_skips_resolved_and_future(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_SLA_BREACHED], "secret-123"
        )
        overdue = self._overdue_conversation()
        current = self.database.create_conversation(
            "tenant-1", "Current Customer", None, "web", "admin", 120
        )
        self.assertEqual(self.webhooks.check_sla_breaches(), 1)
        # Resolving the overdue conversation removes it from the next scan;
        # the future-due conversation never appears.
        from app.domain import ConversationStatus

        self.database.transition_conversation(
            "tenant-1", overdue, [ConversationStatus.OPEN], ConversationStatus.RESOLVED
        )
        self.assertEqual(self.webhooks.check_sla_breaches(), 0)
        deliveries = self.webhooks.list_deliveries("tenant-1")
        self.assertEqual(len(deliveries), 1)
        self.assertNotIn(current["id"], {d["event_id"] for d in deliveries})

    def test_sla_breach_no_endpoints_emits_nothing(self) -> None:
        self._overdue_conversation()
        self.assertEqual(self.webhooks.check_sla_breaches(), 0)


class WebhookOrchestratorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        self.database.initialize()
        self.database.seed_demo()
        self.settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.transport = RecordingTransport()
        self.webhooks = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
        )
        self.orchestrator = ConversationOrchestrator(
            self.database, self.settings, webhook_service=self.webhooks
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _create_conversation(self) -> str:
        conv = self.database.create_conversation(
            "demo", "Webhook Customer", None, "web", "admin", 120
        )
        return conv["id"]

    def test_escalation_emits_event(self) -> None:
        self.webhooks.register_endpoint(
            "demo", "https://example.com/hook", [EVENT_CONVERSATION_ESCALATED], "secret-123"
        )
        conv_id = self._create_conversation()
        self.orchestrator.handle_customer_message(
            "demo", conv_id, "我要退款并投诉，给我转人工", "admin", "idem-webhook-esc"
        )
        deliveries = self.webhooks.list_deliveries("demo")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["event_type"], EVENT_CONVERSATION_ESCALATED)

    def test_resolve_emits_event(self) -> None:
        self.webhooks.register_endpoint(
            "demo", "https://example.com/hook", [EVENT_CONVERSATION_RESOLVED], "secret-123"
        )
        conv_id = self._create_conversation()
        self.orchestrator.resolve("demo", conv_id, "admin")
        deliveries = self.webhooks.list_deliveries("demo")
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["event_type"], EVENT_CONVERSATION_RESOLVED)

    def test_conversation_without_endpoint_emits_nothing(self) -> None:
        conv_id = self._create_conversation()
        self.orchestrator.resolve("demo", conv_id, "admin")
        self.assertEqual(self.webhooks.list_deliveries("demo"), [])

    def test_worker_housekeeping_delivers_and_scans(self) -> None:
        self.webhooks.register_endpoint(
            "demo",
            "https://example.com/hook",
            [EVENT_CONVERSATION_CREATED, EVENT_CONVERSATION_SLA_BREACHED],
            "secret-123",
        )
        worker = TurnJobWorker(
            self.database,
            self.orchestrator,
            enabled=False,
            webhook_service=self.webhooks,
            webhook_interval_seconds=5,
        )
        # Emit an event (no worker running) and deliver it via housekeeping.
        self.webhooks.emit_event(
            "demo", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        worker._webhook_housekeeping()
        self.assertEqual(len(self.transport.calls), 1)
        # Within the cadence the second pass delivers nothing new.
        worker._webhook_housekeeping()
        self.assertEqual(len(self.transport.calls), 1)
        # After the cadence elapses, a new event is picked up.
        worker._next_webhook_at = 0.0
        self.webhooks.emit_event(
            "demo", EVENT_CONVERSATION_CREATED, {"conversation_id": "c2"}, "evt-2"
        )
        worker._webhook_housekeeping()
        self.assertEqual(len(self.transport.calls), 2)


class WebhookApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
            OTHER_ADMIN_KEY: {
                "tenant_id": "other-tenant",
                "actor_id": "other.admin",
                "role": "admin",
            },
            OPERATOR_KEY: {
                "tenant_id": "demo",
                "actor_id": "operator.user",
                "role": "operator",
            },
        }
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=1000,
            docs_enabled=False,
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services
        self.services.webhooks._resolve_host = _public_resolve
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.other_headers = {"X-API-Key": OTHER_ADMIN_KEY, "X-Tenant-Id": "other-tenant"}
        self.operator_headers = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def _audit_types(self) -> list[str]:
        with self.services.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id='demo' "
                "ORDER BY created_at, seq"
            ).fetchall()
        return [r["event_type"] for r in rows]

    def test_register_list_delete(self) -> None:
        response = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "active")
        self.assertEqual(body["events"], ["conversation.created"])
        endpoint_id = body["id"]
        self.assertIn("webhook.registered", self._audit_types())

        listed = self.client.get("/api/webhooks", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)

        deleted = self.client.delete(f"/api/webhooks/{endpoint_id}", headers=self.headers)
        self.assertEqual(deleted.status_code, 204)
        self.assertIn("webhook.deleted", self._audit_types())
        self.assertEqual(self.client.get("/api/webhooks", headers=self.headers).json(), [])

    def test_invalid_endpoint_payload_422(self) -> None:
        response = self.client.post(
            "/api/webhooks",
            json={"url": "not-a-url", "events": ["conversation.magic"], "secret": "s"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_delete_unknown_404(self) -> None:
        response = self.client.delete("/api/webhooks/no-such-endpoint", headers=self.headers)
        self.assertEqual(response.status_code, 404, response.text)

    def test_operator_forbidden(self) -> None:
        response = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.operator_headers,
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(
            self.client.get("/api/webhooks", headers=self.operator_headers).status_code, 403
        )

    def test_cross_tenant_isolation(self) -> None:
        created = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.headers,
        ).json()
        # other-tenant cannot see or delete demo's endpoint.
        self.assertEqual(self.client.get("/api/webhooks", headers=self.other_headers).json(), [])
        deleted = self.client.delete(f"/api/webhooks/{created['id']}", headers=self.other_headers)
        self.assertEqual(deleted.status_code, 404)

    def test_delivery_history_api(self) -> None:
        endpoint = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.headers,
        ).json()
        self.services.webhooks.emit_event(
            "demo", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-api-1"
        )
        response = self.client.get("/api/webhooks/deliveries", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        deliveries = response.json()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["endpoint_id"], endpoint["id"])
        self.assertEqual(deliveries[0]["event_id"], "evt-api-1")
        self.assertEqual(deliveries[0]["status"], "pending")
        filtered = self.client.get(
            f"/api/webhooks/deliveries?endpoint_id={endpoint['id']}", headers=self.headers
        ).json()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(
            self.client.get(
                "/api/webhooks/deliveries?endpoint_id=no-such", headers=self.headers
            ).json(),
            [],
        )

    def test_create_conversation_emits_created_webhook(self) -> None:
        endpoint = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.headers,
        ).json()
        response = self.client.post(
            "/api/conversations",
            json={"customer_name": "Hook Customer", "channel": "web"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 201, response.text)
        deliveries = self.client.get("/api/webhooks/deliveries", headers=self.headers).json()
        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0]["endpoint_id"], endpoint["id"])
        self.assertEqual(deliveries[0]["event_type"], EVENT_CONVERSATION_CREATED)

    def test_delivery_exactly_once_semantics_via_api(self) -> None:
        # At-least-once delivery plus a consumer deduplication id: re-emitting
        # the same event id never grows the delivery table.
        self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "secret-123",
            },
            headers=self.headers,
        )
        for _ in range(3):
            self.services.webhooks.emit_event(
                "demo", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-exactly-once"
            )
        deliveries = self.client.get("/api/webhooks/deliveries", headers=self.headers).json()
        self.assertEqual(len(deliveries), 1)


if __name__ == "__main__":
    unittest.main()
