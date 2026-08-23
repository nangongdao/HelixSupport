"""Phase 43.3 consumer side: drain outbox events into webhook delivery.

``DomainEventOutbox.record`` is write-side (v2 create commits the business
row and its domain event in one transaction); ``WebhookEventConsumer`` is the
missing consumer — claimed events are fanned out to every subscribed endpoint
via ``WebhookService.emit_event``, which reuses the ``(endpoint_id,
event_id)`` unique constraint as the consumer-side deduplication id. A replay,
a racing worker, or a handler failure that releases the claim must never
produce a duplicate delivery.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import Database
from app.event_outbox import DomainEventOutbox
from app.main import create_app
from app.migrations import all_migrations, run_migrations
from app.outbox_consumer import WebhookEventConsumer
from app.webhooks import EVENT_CONVERSATION_CREATED, WebhookConfig, WebhookService, _sign

ADMIN_KEY = "admin-key-0123456789"


def _public_resolve(_host: str, _port: int) -> list[str]:
    """Offline resolver: treat every hostname as a public unicast address."""
    return ["93.184.216.34"]


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


class FlakyWebhookService:
    """emit_event fails once (transient), then behaves like the real one."""

    def __init__(self, real: WebhookService) -> None:
        self.real = real
        self.fail_next = False

    def emit_event(
        self, tenant_id: str, event_type: str, payload: dict[str, Any], event_id: str
    ) -> int:
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("transient outbox fan-out failure")
        return self.real.emit_event(tenant_id, event_type, payload, event_id)


class WebhookEventConsumerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.database = Database(root / "consumer.db", pool_size=2)
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
        self.consumer = WebhookEventConsumer(self.database, self.webhooks)
        self.outbox = DomainEventOutbox(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _record(self, tenant_id: str, conversation_id: str, api: str = "v2") -> str:
        return self.outbox.record(
            tenant_id,
            "helix.conversation.created",
            {
                "conversation_id": conversation_id,
                "channel": "web",
                "customer_name": "Consumer Customer",
                "customer_verified": True,
                "source_api": api,
            },
        )

    def test_drain_fans_event_to_subscribed_endpoint(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh"
        )
        event_id = self._record("tenant-1", "conv-1")
        result = self.consumer.drain_and_deliver()
        self.assertEqual(
            result,
            {"published": 1, "deliveries": 1, "delivered": 1, "no_endpoint": 0},
        )
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT event_id, event_type, payload_json FROM webhook_deliveries"
            ).fetchone()
        self.assertEqual(row["event_id"], event_id)
        self.assertEqual(row["event_type"], EVENT_CONVERSATION_CREATED)
        self.assertEqual(json.loads(row["payload_json"])["conversation_id"], "conv-1")
        self.assertEqual(self.consumer.pending_count(), 0)

    def test_replay_drain_delivers_nothing_again(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh"
        )
        self._record("tenant-1", "conv-1")
        self.assertEqual(self.consumer.drain_and_deliver()["published"], 1)
        # Consumer-side dedup: a replayed drain re-claims nothing and the
        # (endpoint_id, event_id) constraint would no-op even if it did.
        self.assertEqual(
            self.consumer.drain_and_deliver(),
            {"published": 0, "deliveries": 0, "delivered": 0, "no_endpoint": 0},
        )

    def test_handler_failure_releases_claim_and_retry_dedupes(self) -> None:
        # The drain claims the row before the handler; a handler failure
        # releases the claim so the event is retried, and the retry must not
        # duplicate the delivery.
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh"
        )
        self._record("tenant-1", "conv-1")
        flaky = FlakyWebhookService(self.webhooks)
        flaky.fail_next = True
        consumer = WebhookEventConsumer(self.database, flaky)
        with self.assertRaises(RuntimeError):
            consumer.drain_and_deliver()
        self.assertEqual(consumer.pending_count(), 1, "claim must be released")
        # Retry now succeeds and creates exactly one delivery.
        self.assertEqual(consumer.drain_and_deliver()["deliveries"], 1)
        with self.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM webhook_deliveries"
            ).fetchone()["n"]
        self.assertEqual(int(count), 1)
        self.assertEqual(consumer.pending_count(), 0)

    def test_event_without_subscriber_is_published_not_retried(self) -> None:
        # No endpoint subscribes on tenant-2; the event is still published —
        # at-least-once delivery must not retry an event no endpoint wants.
        self._record("tenant-2", "conv-2")
        result = self.consumer.drain_and_deliver()
        self.assertEqual(
            result,
            {"published": 1, "deliveries": 0, "delivered": 0, "no_endpoint": 1},
        )
        self.assertEqual(self.consumer.pending_count(), 0)

    def test_full_chain_signs_and_delivers_with_event_id(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        event_id = self._record("tenant-1", "conv-3")
        self.consumer.drain_and_deliver()
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 1, "retried": 0, "dead": 0})
        url, headers, body = self.transport.calls[0]
        self.assertEqual(url, "https://example.com/hook")
        timestamp = headers["X-Helix-Timestamp"]
        self.assertEqual(headers["X-Helix-Signature"], _sign("shhh-secret", timestamp, body))
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT event_id, status FROM webhook_deliveries WHERE endpoint_id = ?",
                (endpoint["id"],),
            ).fetchone()
        self.assertEqual(row["event_id"], event_id)
        self.assertEqual(row["status"], "delivered")

    def test_multiple_events_drain_in_order_all_endpoints(self) -> None:
        self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh"
        )
        first = self._record("tenant-1", "conv-a")
        second = self._record("tenant-1", "conv-b")
        result = self.consumer.drain_and_deliver()
        self.assertEqual(result["published"], 2)
        self.assertEqual(result["deliveries"], 2)
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_id FROM webhook_deliveries ORDER BY created_at"
            ).fetchall()
        self.assertEqual([row["event_id"] for row in rows], [first, second])


class OutboxConsumerRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.settings = Settings(
            database_path=root / "runtime.db",
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    ADMIN_KEY: {
                        "tenant_id": "demo",
                        "actor_id": "admin",
                        "role": "admin",
                    }
                }
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            turn_worker_enabled=False,
            envelope_kms_dir=root / "kms",
        )
        self.client = TestClient(create_app(self.settings))
        self.services = cast(Any, self.client.app).state.services

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def _headers(self) -> dict[str, str]:
        return {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def test_outbox_consumer_assembled_with_housekeeping_drain(self) -> None:
        consumer = self.services.outbox_consumer
        self.assertIsInstance(consumer, WebhookEventConsumer)
        names = [cb.__name__ for cb in self.services.turn_worker.housekeeping]
        self.assertIn("_housekeeping_outbox_drain", names)

    def test_v2_create_flows_to_webhook_delivery_via_drain(self) -> None:
        registered = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://example.com/hook",
                "events": ["conversation.created"],
                "secret": "shhh-secret",
            },
            headers=self._headers(),
        )
        self.assertEqual(registered.status_code, 201, registered.text)
        created = self.client.post(
            "/api/v2/conversations",
            json={"customer_name": "Drained Customer", "channel": "web"},
            headers=self._headers(),
        )
        self.assertEqual(created.status_code, 201, created.text)
        conversation_id = created.json()["id"]

        consumer = self.services.outbox_consumer
        self.assertGreaterEqual(consumer.pending_count(), 1)
        result = consumer.drain_and_deliver()
        self.assertGreaterEqual(result["published"], 1)
        self.assertGreaterEqual(result["deliveries"], 1)
        self.assertEqual(consumer.pending_count(), 0)

        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT event_type, payload_json FROM webhook_deliveries WHERE tenant_id = 'demo'"
            ).fetchone()
        self.assertEqual(row["event_type"], EVENT_CONVERSATION_CREATED)
        self.assertEqual(
            json.loads(row["payload_json"])["conversation_id"], conversation_id
        )


if __name__ == "__main__":
    unittest.main()
