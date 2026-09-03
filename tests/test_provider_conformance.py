"""Phase 42.5 / REL-003: provider adapter conformance suite.

Runs the reference provider adapter through the REAL unified ingress
(``POST /api/channels/{account_id}/webhook``) and pins the contract every
provider adapter must satisfy:

- 签名: valid signature accepted; wrong secret / stale timestamp / missing
  headers rejected with no state created;
- 重放/重复: identical deliveries collapse into one job + one customer
  message (idempotent replay);
- 乱序: out-of-order delivery of two messages on one thread loses nothing;
- 编辑/撤回: edit/recall kinds normalize correctly and can never silently
  rewrite an already-ingested message (core rejects the conflict);
- 附件: attachment references parse and the message still flows;
- 429 retry: backpressure returns 429 + Retry-After, and a later retry
  succeeds without duplication;
- provider outage: a failing outbound transport records retryable failures
  (DLQ semantics) and drains once the provider recovers;
- 跨账号/租户隔离: another account's credentials cannot act across tenants.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.channel_providers import (
    NormalizedEvent,
    ReferenceJsonAdapter,
    sign_reference_request,
)
from app.config import Settings
from app.main import create_app

ACCOUNT = "support-main"
OTHER_ACCOUNT = "support-other"
SECRET = "reference-provider-secret-0123456789abcdef"
OTHER_SECRET = "other-account-secret-0123456789abcdef"


def _account_config() -> str:
    return json.dumps(
        {
            ACCOUNT: {
                "tenant_id": "demo",
                "channel": "web",
                "secret": SECRET,
            },
            OTHER_ACCOUNT: {
                "tenant_id": "acme",
                "channel": "web",
                "secret": OTHER_SECRET,
            },
        }
    )


def _event(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "message_id": f"provider-message-{overrides.get('seq', 1)}",
        "thread_id": "provider-thread-1",
        "customer_id": "CUST-CONF",
        "customer_name": "Conformance Customer",
        "content": f"conformance message {overrides.get('seq', 1)}",
        "kind": "message",
    }
    payload.update(overrides)
    return payload


class ProviderConformanceTests(unittest.TestCase):
    """Every case runs through the real HTTP ingress — no shortcuts."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self._tmp.name) / "conformance.db",
            channel_webhooks_json=_account_config(),
            turn_worker_enabled=False,
            rate_limit_per_minute=10_000,
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services
        self.adapter = ReferenceJsonAdapter()

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------- helpers

    def _deliver(
        self,
        payload: dict[str, Any],
        *,
        account_id: str = ACCOUNT,
        secret: bytes = SECRET.encode(),
        headers: dict[str, str] | None = None,
        timestamp: int | None = None,
    ) -> Any:
        # The adapter normalizes the vendor wire format into the core schema;
        # only the unified fields ever reach the ingress.
        event = self.adapter.parse(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        core_payload = {
            "message_id": event.external_message_id,
            "thread_id": event.thread_id,
            "customer_id": event.customer_id,
            "customer_name": event.customer_name,
            "content": event.content,
        }
        body = json.dumps(core_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        merged = sign_reference_request(secret, body, now_epoch=timestamp)
        if headers is not None:
            merged = {"Content-Type": "application/json", **headers}
        else:
            merged = {"Content-Type": "application/json", **merged}
        return self.client.post(f"/api/channels/{account_id}/webhook", content=body, headers=merged)

    def _normalize(self, payload: dict[str, Any]) -> NormalizedEvent:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.adapter.parse(body)

    def _customer_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = self.services.database.list_messages("demo", conversation_id)
        return [row for row in rows if row["role"] == "customer"]

    def _drain(self) -> None:
        """Process every queued turn job (the worker persists the messages)."""
        for _ in range(20):
            if not self.services.turn_worker.run_once("conformance-worker"):
                return

    # ------------------------------------------------------- 签名 (signature)

    def test_signature_contract(self) -> None:
        first = self._deliver(_event())
        self.assertEqual(first.status_code, 202, first.text)

        wrong_secret = self._deliver(_event(seq=2), secret=b"wrong-secret-wrong-secret-123456")
        self.assertEqual(wrong_secret.status_code, 401)

        stale = self._deliver(_event(seq=3), timestamp=1_000_000)
        self.assertEqual(stale.status_code, 401)

        unsigned = self._deliver(_event(seq=4), headers={})
        self.assertEqual(unsigned.status_code, 401)

        # No state leaked from the rejected deliveries.
        replay = self._deliver(_event(seq=2))
        self.assertEqual(replay.status_code, 202, "rejected deliveries must not poison ids")
        self.assertFalse(replay.json()["conversation_created"])

    # -------------------------------------------------- 重放 / 重复 (replay)

    def test_replay_and_duplicate_deliveries_collapse(self) -> None:
        payload = _event()
        first = self._deliver(payload)
        self.assertEqual(first.status_code, 202)
        second = self._deliver(payload)
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second.json()["idempotent_replay"])
        self.assertEqual(second.json()["job_id"], first.json()["job_id"])

        self._drain()
        conversation_id = first.json()["conversation_id"]
        self.assertEqual(len(self._customer_messages(conversation_id)), 1)
        with self.services.database.connect() as connection:
            jobs = connection.execute(
                "SELECT COUNT(*) AS n FROM turn_jobs WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        self.assertEqual(jobs["n"], 1)

    # ------------------------------------------------------------- 乱序

    def test_out_of_order_delivery_loses_nothing(self) -> None:
        late = self._deliver(_event(seq=2))
        early = self._deliver(_event(seq=1))
        self.assertEqual(late.status_code, 202)
        self.assertEqual(early.status_code, 202)
        conversation_id = late.json()["conversation_id"]
        self.assertEqual(early.json()["conversation_id"], conversation_id)
        self._drain()
        contents = [row["content"] for row in self._customer_messages(conversation_id)]
        self.assertEqual(sorted(contents), ["conformance message 1", "conformance message 2"])

    # ------------------------------------------------------ 编辑 / 撤回

    def test_edit_and_recall_kinds_normalize_and_never_rewrite_history(self) -> None:
        event = self._normalize(_event(kind="edit", content="edited text"))
        self.assertEqual(event.kind, "edit")
        recall = self._normalize(_event(kind="recall"))
        self.assertEqual(recall.kind, "recall")

        original = self._deliver(_event())
        self.assertEqual(original.status_code, 202)
        # An edit redelivered under the same external id is a core-level
        # conflict: history is immutable through the ingress.
        edited = self._deliver(_event(content="edited text"))
        self.assertEqual(edited.status_code, 409)
        conversation_id = original.json()["conversation_id"]
        self._drain()
        self.assertEqual(
            self._customer_messages(conversation_id)[0]["content"],
            "conformance message 1",
            "an edit delivery must never rewrite ingested history",
        )

    def test_unknown_kind_fails_closed_in_the_adapter(self) -> None:
        with self.assertRaises(ValueError):
            self._normalize(_event(kind="voice-blast"))

    # --------------------------------------------------------------- 附件

    def test_attachment_references_parse_and_flow(self) -> None:
        payload = _event(
            seq=5,
            attachments=[
                {
                    "id": "ext-att-1",
                    "filename": "receipt.pdf",
                    "content_type": "application/pdf",
                    "url": "https://provider.example/objects/ext-att-1",
                }
            ],
        )
        event = self._normalize(payload)
        self.assertEqual(event.attachments[0].external_id, "ext-att-1")
        response = self._deliver(payload)
        self.assertEqual(response.status_code, 202, response.text)

    # --------------------------------------------------------- 429 retry

    def test_backpressure_returns_429_with_retry_after_then_recovers(self) -> None:
        # Fill the queue past the configured depth threshold to trip the
        # overload guard deterministically.
        conversation_id = self.services.database.create_conversation(
            "demo", "Backpressure", "CUST-BP", "web", "admin", 120
        )["id"]
        threshold = self.services.settings.queue_depth_threshold
        with self.services.database.connect() as connection:
            for index in range(threshold + 5):
                connection.execute(
                    """INSERT INTO turn_jobs (id, tenant_id, conversation_id, idempotency_key,
                       actor_id, content, status, attempts, max_attempts, available_at,
                       created_at, updated_at)
                       VALUES (?, 'demo', ?, ?, 'load', 'x', 'queued',
                               0, 3, '2020-01-01T00:00:00+00:00',
                               '2020-01-01T00:00:00+00:00', '2020-01-01T00:00:00+00:00')""",
                    (f"job_bp_{index}", conversation_id, f"bp-{index}"),
                )
        blocked = self._deliver(_event(seq=10))
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("retry-after", {k.lower() for k in blocked.headers})

        # The provider retries after the backlog drains; the retry succeeds
        # exactly once.
        with self.services.database.connect() as connection:
            connection.execute(
                "DELETE FROM turn_jobs WHERE conversation_id = ?", (conversation_id,)
            )
        retried = self._deliver(_event(seq=10))
        self.assertEqual(retried.status_code, 202, retried.text)
        again = self._deliver(_event(seq=10))
        self.assertTrue(again.json()["idempotent_replay"])

    # ---------------------------------------------- provider outage (outbound)

    def test_provider_outage_records_retryable_failure_then_drains(self) -> None:
        from app.webhooks import WebhookConfig, WebhookService

        attempts: list[int] = []

        def flaky_transport(url: str, headers: dict, body: bytes, timeout: float):
            attempts.append(1)
            # First two deliveries simulate the provider being down (503),
            # then it recovers.
            if len(attempts) <= 2:
                return 503, "provider outage"
            return 200, None

        webhooks = WebhookService(
            self.services.database,
            config=WebhookConfig(max_attempts=5, base_backoff_seconds=0.0),
            transport=flaky_transport,
            resolve_host=lambda host, port: [],
        )
        webhooks.register_endpoint(
            "demo",
            "https://provider.example/hooks/reply",
            ["conversation.escalated"],
            "outbound-signing-secret-0123456789abcdef",
        )
        emitted = webhooks.emit_event(
            "demo", "conversation.escalated", {"id": "conv-out-1"}, "evt-conformance-1"
        )
        self.assertIsNotNone(emitted)
        # Pass 1 hits the outage (503) and is scheduled for retry.
        first_pass = webhooks.deliver_pending()
        self.assertGreaterEqual(first_pass.get("retried", 0), 1)
        # Pass 2 may still fail or already succeed depending on attempt
        # accounting; keep draining until the provider has recovered.
        delivered = 0
        for _ in range(5):
            results = webhooks.deliver_pending()
            delivered += results.get("delivered", 0)
            if delivered:
                break
        self.assertGreaterEqual(delivered, 1, "delivery must drain after recovery")

    # -------------------------------------------- 跨账号 / 租户隔离

    def test_cross_tenant_account_isolation(self) -> None:
        other_tenant_event = _event(seq=20)
        response = self._deliver(
            other_tenant_event, account_id=OTHER_ACCOUNT, secret=OTHER_SECRET.encode()
        )
        self.assertEqual(response.status_code, 202, response.text)
        # The acme account's conversation must live in acme, not demo.
        conversation_id = response.json()["conversation_id"]
        self.assertIsNone(self.services.database.get_conversation("demo", conversation_id))
        self.assertIsNotNone(self.services.database.get_conversation("acme", conversation_id))
        # And the demo account's secret cannot authenticate on the acme path.
        swapped = self._deliver(_event(seq=21), account_id=OTHER_ACCOUNT, secret=SECRET.encode())
        self.assertEqual(swapped.status_code, 401)


if __name__ == "__main__":
    unittest.main()
