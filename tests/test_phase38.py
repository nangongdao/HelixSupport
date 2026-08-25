"""Phase 38 / ROADMAP 23.3 signed formal-channel webhook coverage."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.channel_webhooks import (
    ChannelWebhookAuthError,
    ChannelWebhookConfigError,
    InboundChannelRegistry,
)
from app.config import Settings
from app.database import Database
from app.domain import ConversationStatus
from app.main import create_app
from app.migrations import all_migrations, migration_schema_version

SECRET = "phase38-channel-secret-with-at-least-32-bytes"
OTHER_SECRET = "phase38-other-secret-with-at-least-32-bytes"


def _account_config() -> str:
    return json.dumps(
        {
            "support-main": {
                "tenant_id": "demo",
                "channel": "formal_chat",
                "secret": SECRET,
            },
            "support-other": {
                "tenant_id": "other-tenant",
                "channel": "formal_chat",
                "secret": OTHER_SECRET,
            },
        }
    )


def _signed_headers(secret: str, body: bytes, timestamp: int | None = None) -> dict[str, str]:
    signed_at = int(time.time()) if timestamp is None else timestamp
    digest = hmac.new(
        secret.encode("utf-8"),
        str(signed_at).encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Helix-Timestamp": str(signed_at),
        "X-Helix-Signature": f"sha256={digest}",
    }


def _payload(
    *,
    message_id: str = "provider-message-1",
    thread_id: str = "provider-thread-1",
    customer_id: str = "CUST-1001",
    content: str = "配送一般多久能到",
) -> dict[str, str]:
    return {
        "event": "message.created",
        "message_id": message_id,
        "thread_id": thread_id,
        "customer_id": customer_id,
        "customer_name": "Channel Customer",
        "content": content,
    }


class ChannelWebhookAuthenticationTests(unittest.TestCase):
    def test_registry_validates_config_and_authenticates_raw_body(self) -> None:
        registry = InboundChannelRegistry(_account_config(), replay_window_seconds=300)
        body = b'{"message_id":"m1"}'
        timestamp = 1_800_000_000
        headers = _signed_headers(SECRET, body, timestamp)
        account = registry.authenticate(
            "support-main",
            headers["X-Helix-Timestamp"],
            headers["X-Helix-Signature"],
            body,
            now=timestamp,
        )
        self.assertEqual(account.tenant_id, "demo")
        self.assertEqual(account.channel, "formal_chat")
        self.assertNotIn(SECRET, repr(account))

    def test_unknown_bad_and_stale_signatures_share_auth_failure(self) -> None:
        registry = InboundChannelRegistry(_account_config(), replay_window_seconds=300)
        body = b"{}"
        now = 1_800_000_000
        valid = _signed_headers(SECRET, body, now)
        cases = [
            ("unknown", valid["X-Helix-Timestamp"], valid["X-Helix-Signature"]),
            ("support-main", str(now), "sha256=" + "0" * 64),
            (
                "support-main",
                str(now - 301),
                _signed_headers(SECRET, body, now - 301)["X-Helix-Signature"],
            ),
            (
                "support-main",
                str(now + 301),
                _signed_headers(SECRET, body, now + 301)["X-Helix-Signature"],
            ),
        ]
        for account_id, timestamp, signature in cases:
            with self.subTest(account_id=account_id, timestamp=timestamp):
                with self.assertRaisesRegex(
                    ChannelWebhookAuthError, "Invalid webhook authentication"
                ):
                    registry.authenticate(account_id, timestamp, signature, body, now=now)

    def test_invalid_account_config_fails_closed(self) -> None:
        invalid = json.dumps(
            {"bad/account": {"tenant_id": "demo", "channel": "web", "secret": "short"}}
        )
        with self.assertRaises(ChannelWebhookConfigError):
            InboundChannelRegistry(invalid)

    def test_channel_secrets_file_takes_precedence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "channels.json"
            path.write_text(_account_config(), encoding="utf-8")
            settings = Settings(
                channel_webhooks_json="not-json",
                channel_webhooks_file=path,
            )
            registry = InboundChannelRegistry(settings.effective_channel_webhooks_json)
            self.assertEqual(registry.account_count, 2)


class ChannelPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self._tmp.name) / "channels.db", pool_size=8)
        self.database.initialize()
        self.database.ensure_tenant("channel-concurrency")

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_concurrent_first_delivery_maps_to_one_conversation(self) -> None:
        def create() -> tuple[str, bool]:
            conversation, created = self.database.get_or_create_channel_conversation(
                "channel-concurrency",
                "account-1",
                "thread-1",
                "Concurrent Customer",
                "CUST-CONCURRENT",
                "formal_chat",
                "channel:account-1",
                120,
            )
            return conversation["id"], created

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _index: create(), range(8)))
        self.assertEqual(len({conversation_id for conversation_id, _ in results}), 1)
        self.assertEqual(sum(1 for _, created in results if created), 1)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS n FROM channel_threads WHERE tenant_id = ?",
                ("channel-concurrency",),
            ).fetchone()
        self.assertEqual(row["n"], 1)

    def test_channel_thread_tenant_guard_rejects_cross_tenant_reference(self) -> None:
        conversation = self.database.create_conversation(
            "channel-concurrency", "Guarded", "CUST-G", "formal_chat", "test", 120
        )
        self.database.ensure_tenant("other-channel-tenant")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.database.connect() as connection:
                connection.execute(
                    """INSERT INTO channel_threads
                    (tenant_id, account_id, external_thread_id, conversation_id,
                     created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        "other-channel-tenant",
                        "account-1",
                        "thread-guard",
                        conversation["id"],
                        "2026-08-18T00:00:00+00:00",
                        "2026-08-18T00:00:00+00:00",
                    ),
                )

    def test_migration_27_installs_channel_tables_and_job_message_id(self) -> None:
        with self.database.connect() as connection:
            tables = {
                row["name"]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(turn_jobs)").fetchall()
            }
            self.assertEqual(migration_schema_version(connection), 41)
            self.assertEqual(max(migration.version for migration in all_migrations()), 41)
        self.assertIn("channel_threads", tables)
        self.assertIn("channel_webhook_receipts", tables)
        self.assertIn("channel_message_id", columns)


class ChannelWebhookEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self._tmp.name) / "phase38.db",
            channel_webhooks_json=_account_config(),
            turn_worker_enabled=False,
            rate_limit_per_minute=10_000,
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def _post(
        self,
        payload: dict[str, str],
        *,
        account_id: str = "support-main",
        secret: str = SECRET,
        timestamp: int | None = None,
    ) -> Any:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return self.client.post(
            f"/api/channels/{account_id}/webhook",
            content=body,
            headers=_signed_headers(secret, body, timestamp),
        )

    def test_signed_message_replay_is_one_job_and_one_durable_turn(self) -> None:
        payload = _payload()
        first = self._post(payload)
        self.assertEqual(first.status_code, 202, first.text)
        first_body = first.json()
        self.assertTrue(first_body["conversation_created"])
        self.assertFalse(first_body["idempotent_replay"])
        audit_types = {
            row["event_type"]
            for row in self.services.database.list_audit("demo", first_body["conversation_id"])
        }
        self.assertIn("conversation.created", audit_types)
        self.assertIn("turn_job.queued", audit_types)

        replay = self._post(payload)
        self.assertEqual(replay.status_code, 202, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["job_id"], first_body["job_id"])

        self.assertTrue(self.services.turn_worker.run_once("phase38-test-worker"))
        completed = self._post(payload)
        self.assertEqual(completed.status_code, 202, completed.text)
        self.assertEqual(completed.json()["status"], "completed")
        self.assertTrue(completed.json()["idempotent_replay"])

        conversation_id = first_body["conversation_id"]
        messages = self.services.database.list_messages("demo", conversation_id)
        customer_messages = [row for row in messages if row["role"] == "customer"]
        self.assertEqual(len(customer_messages), 1)
        self.assertEqual(customer_messages[0]["channel_message_id"], payload["message_id"])
        with self.services.database.connect() as connection:
            jobs = connection.execute(
                "SELECT COUNT(*) AS n FROM turn_jobs WHERE tenant_id=? AND conversation_id=?",
                ("demo", conversation_id),
            ).fetchone()
        self.assertEqual(jobs["n"], 1)

    def test_message_id_conflicts_across_body_or_thread(self) -> None:
        self.assertEqual(self._post(_payload()).status_code, 202)
        changed_body = self._post(_payload(content="changed content"))
        self.assertEqual(changed_body.status_code, 409, changed_body.text)
        changed_thread = self._post(_payload(thread_id="provider-thread-2"))
        self.assertEqual(changed_thread.status_code, 409, changed_thread.text)

    def test_thread_cannot_change_customer_identity(self) -> None:
        first = self._post(_payload())
        self.assertEqual(first.status_code, 202, first.text)
        second = self._post(_payload(message_id="provider-message-2", customer_id="CUST-OTHER"))
        self.assertEqual(second.status_code, 409, second.text)

    def test_bad_unknown_and_stale_auth_create_no_conversation(self) -> None:
        payload = _payload()
        bad = self._post(payload, secret=OTHER_SECRET)
        unknown = self._post(payload, account_id="not-configured")
        stale = self._post(payload, timestamp=int(time.time()) - 301)
        self.assertEqual([bad.status_code, unknown.status_code, stale.status_code], [401, 401, 401])
        details = {response.json()["detail"] for response in (bad, unknown, stale)}
        self.assertEqual(details, {"Invalid webhook authentication"})
        with self.services.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM channel_threads").fetchone()
        self.assertEqual(count["n"], 0)

    def test_request_body_cannot_override_account_tenant(self) -> None:
        payload = _payload()
        payload["tenant_id"] = "other-tenant"
        rejected = self._post(payload)
        self.assertEqual(rejected.status_code, 422, rejected.text)
        with self.services.database.connect() as connection:
            count = connection.execute("SELECT COUNT(*) AS n FROM channel_threads").fetchone()
        self.assertEqual(count["n"], 0)

    def test_queue_backpressure_rejects_before_creating_channel_state(self) -> None:
        with patch(
            "app.routers.channels.backpressure_reason",
            return_value="Queue overloaded (test)",
        ):
            rejected = self._post(_payload())
        self.assertEqual(rejected.status_code, 429, rejected.text)
        self.assertEqual(rejected.headers["Retry-After"], "30")
        with self.services.database.connect() as connection:
            threads = connection.execute("SELECT COUNT(*) AS n FROM channel_threads").fetchone()
            receipts = connection.execute(
                "SELECT COUNT(*) AS n FROM channel_webhook_receipts"
            ).fetchone()
        self.assertEqual(threads["n"], 0)
        self.assertEqual(receipts["n"], 0)

    def test_accounts_are_tenant_bound_even_with_same_external_ids(self) -> None:
        first = self._post(_payload())
        second = self._post(_payload(), account_id="support-other", secret=OTHER_SECRET)
        self.assertEqual(first.status_code, 202, first.text)
        self.assertEqual(second.status_code, 202, second.text)
        self.assertNotEqual(first.json()["conversation_id"], second.json()["conversation_id"])
        first_conversation = self.services.database.get_conversation(
            "demo", first.json()["conversation_id"]
        )
        second_conversation = self.services.database.get_conversation(
            "other-tenant", second.json()["conversation_id"]
        )
        self.assertIsNotNone(first_conversation)
        self.assertIsNotNone(second_conversation)

    def test_new_message_reopens_mapped_resolved_conversation(self) -> None:
        first = self._post(_payload())
        conversation_id = first.json()["conversation_id"]
        resolved = self.services.database.transition_conversation(
            "demo",
            conversation_id,
            [ConversationStatus.OPEN],
            ConversationStatus.RESOLVED,
        )
        self.assertIsNotNone(resolved)
        second = self._post(_payload(message_id="provider-message-2"))
        self.assertEqual(second.status_code, 202, second.text)
        self.assertEqual(second.json()["conversation_id"], conversation_id)
        conversation = self.services.database.get_conversation("demo", conversation_id)
        self.assertEqual(conversation["status"], "open")

    def test_reopening_a_thread_respects_active_conversation_quota(self) -> None:
        first = self._post(_payload())
        self.assertEqual(first.status_code, 202, first.text)
        conversation_id = first.json()["conversation_id"]
        conversation = self.services.database.get_conversation("demo", conversation_id)
        assert conversation is not None
        resolved = self.services.database.transition_conversation(
            "demo",
            conversation_id,
            [conversation["status"]],
            ConversationStatus.RESOLVED,
        )
        self.assertIsNotNone(resolved)
        self.services.database.set_tenant_quota("demo", conversation_quota=1)
        self.services.database.create_conversation(
            "demo", "Active Customer", "CUST-ACTIVE", "web", "test", 120
        )

        blocked = self._post(_payload(message_id="provider-message-2"))
        self.assertEqual(blocked.status_code, 429, blocked.text)
        self.assertIn("Conversation quota exceeded", blocked.json()["detail"])
        unchanged = self.services.database.get_conversation("demo", conversation_id)
        assert unchanged is not None
        self.assertEqual(unchanged["status"], ConversationStatus.RESOLVED)
        self.assertIsNone(
            self.services.database.get_channel_webhook_receipt(
                "demo", "support-main", "provider-message-2"
            )
        )


if __name__ == "__main__":
    unittest.main()
