"""Phase 23: embeddable Web Chat channel.

Covers the ROADMAP_1_X section 9 acceptance criteria:
- 23.1 widget sessions created with a signed customer token (no API key),
  reusing the orchestrator and turn-job flow;
- 23.2 channel-level idempotency — replaying the same channel_message_id
  never creates a second turn;
- signed-token security (expired/forged tokens rejected, tenant binding);
- SSE stream endpoint serves progressive output for async turns.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.domain import ConversationStatus
from app.main import create_app
from app.widget_token import WidgetTokenError, sign_token, verify_token

ADMIN_KEY = "p23-admin-key-0001"


def _settings(db_path: Path) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"}}
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class WidgetTokenTests(unittest.TestCase):
    """23.1: signed token issue/verify lifecycle."""

    def test_round_trip(self) -> None:
        token = sign_token(secret="s3cret", tenant_id="demo", customer_ref="CUST-1")
        parsed = verify_token(secret="s3cret", token=token)
        self.assertEqual(parsed.tenant_id, "demo")
        self.assertEqual(parsed.customer_ref, "CUST-1")
        self.assertGreater(parsed.exp, parsed.iat)

    def test_wrong_secret_rejected(self) -> None:
        token = sign_token(secret="s3cret", tenant_id="demo")
        with self.assertRaises(WidgetTokenError):
            verify_token(secret="other-secret", token=token)

    def test_expired_token_rejected(self) -> None:
        import time

        token = sign_token(
            secret="s3cret", tenant_id="demo", ttl_seconds=10, now=int(time.time()) - 100
        )
        with self.assertRaises(WidgetTokenError):
            verify_token(secret="s3cret", token=token)

    def test_tampered_token_rejected(self) -> None:
        token = sign_token(secret="s3cret", tenant_id="demo")
        tampered = token[:-1] + ("0" if token[-1] != "0" else "1")
        with self.assertRaises(WidgetTokenError):
            verify_token(secret="s3cret", token=tampered)

    def test_missing_tenant_rejected(self) -> None:
        with self.assertRaises(WidgetTokenError):
            verify_token(secret="s3cret", token="abc.def")


class WidgetSessionTests(unittest.TestCase):
    """23.1: session creation is tenant-bound and anonymous-safe."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p23.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.settings: Settings = self.services.settings
        self.token = sign_token(
            secret=self.settings.widget_secret, tenant_id="demo", customer_ref="CUST-1001"
        )
        self.headers = {"X-Widget-Token": self.token}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_create_session_anonymous(self) -> None:
        response = self.client.post("/api/widget/sessions", json={}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["conversation"]["channel"], "web_chat")
        self.assertTrue(body["widget_token"])
        self.assertEqual(body["conversation"]["status"], "open")

    def test_create_session_missing_token_401(self) -> None:
        response = self.client.post("/api/widget/sessions", json={})
        self.assertEqual(response.status_code, 401)

    def test_create_session_forged_token_401(self) -> None:
        response = self.client.post(
            "/api/widget/sessions", json={}, headers={"X-Widget-Token": "forged.token"}
        )
        self.assertEqual(response.status_code, 401)

    def test_create_session_unknown_tenant_404(self) -> None:
        token = sign_token(secret=self.settings.widget_secret, tenant_id="ghost")
        response = self.client.post(
            "/api/widget/sessions", json={}, headers={"X-Widget-Token": token}
        )
        self.assertEqual(response.status_code, 404)

    def test_conversation_visible_in_operator_queue(self) -> None:
        created = self.client.post(
            "/api/widget/sessions", json={"customer_name": "Visitor"}, headers=self.headers
        ).json()
        conversation_id = created["conversation"]["id"]
        admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        detail = self.client.get(f"/api/conversations/{conversation_id}", headers=admin).json()
        self.assertEqual(detail["conversation"]["channel"], "web_chat")


class WidgetChannelIdempotencyTests(unittest.TestCase):
    """23.2: replaying a channel_message_id never creates a second turn."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p23i.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.settings: Settings = self.services.settings
        self.token = sign_token(
            secret=self.settings.widget_secret, tenant_id="demo", customer_ref="CUST-1001"
        )
        self.headers = {"X-Widget-Token": self.token}
        self.session = self.client.post(
            "/api/widget/sessions", json={"customer_name": "Replayer"}, headers=self.headers
        ).json()
        self.conversation_id = self.session["conversation"]["id"]
        # Session operations require the fresh conversation-bound token. The
        # bootstrap token remains valid only for creating a new session.
        self.headers = {"X-Widget-Token": self.session["widget_token"]}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _send(self, content: str, channel_message_id: str) -> dict:
        return self.client.post(
            f"/api/widget/sessions/{self.conversation_id}/messages",
            json={"content": content, "channel_message_id": channel_message_id},
            headers=self.headers,
        ).json()

    def test_replay_returns_original_turn_no_duplicate(self) -> None:
        first = self._send("ORD-10482 到哪了", "ch-msg-1")
        self.assertFalse(first["idempotent_replay"])
        self.assertEqual(first["conversation"]["status"], "open")
        replay = self._send("ORD-10482 到哪了", "ch-msg-1")
        self.assertTrue(replay["idempotent_replay"])
        # Only one customer message persisted for that channel id.
        messages = self.client.get(
            f"/api/widget/sessions/{self.conversation_id}/messages", headers=self.headers
        ).json()
        customer_messages = [m for m in messages if m["role"] == "customer"]
        self.assertEqual(len(customer_messages), 1)

    def test_different_channel_ids_create_separate_turns(self) -> None:
        self._send("ORD-10482 到哪了", "ch-a")
        self._send("退货政策是什么", "ch-b")
        messages = self.client.get(
            f"/api/widget/sessions/{self.conversation_id}/messages", headers=self.headers
        ).json()
        customer_messages = [m for m in messages if m["role"] == "customer"]
        self.assertEqual(len(customer_messages), 2)

    def test_async_mode_enqueues_job_and_is_replay_safe(self) -> None:
        first = self.client.post(
            f"/api/widget/sessions/{self.conversation_id}/messages?async_mode=true",
            json={"content": "保修多久", "channel_message_id": "ch-async-1"},
            headers=self.headers,
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["status"], "queued")
        replay = self.client.post(
            f"/api/widget/sessions/{self.conversation_id}/messages?async_mode=true",
            json={"content": "保修多久", "channel_message_id": "ch-async-1"},
            headers=self.headers,
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["idempotent_replay"])

    def test_async_worker_persists_channel_message_id(self) -> None:
        queued = self.client.post(
            f"/api/widget/sessions/{self.conversation_id}/messages?async_mode=true",
            json={"content": "异步消息持久化", "channel_message_id": "ch-worker-id"},
            headers=self.headers,
        )
        self.assertEqual(queued.status_code, 200, queued.text)
        self.assertTrue(self.services.turn_worker.run_once("phase23-channel-id-worker"))
        messages = self.services.database.list_messages("demo", self.conversation_id)
        customer = [row for row in messages if row["role"] == "customer"]
        self.assertEqual(len(customer), 1)
        self.assertEqual(customer[0]["channel_message_id"], "ch-worker-id")

    def test_stream_endpoint_serves_after_async_turn(self) -> None:
        job = self.client.post(
            f"/api/widget/sessions/{self.conversation_id}/messages?async_mode=true",
            json={"content": "配送一般多久能到", "channel_message_id": "ch-stream-1"},
            headers=self.headers,
        ).json()
        self.assertEqual(job["status"], "queued")
        stream = self.client.get(
            f"/api/widget/sessions/{self.conversation_id}/stream", headers=self.headers
        )
        self.assertEqual(stream.status_code, 200)
        self.assertIn("text/event-stream", stream.headers["content-type"])

    def test_session_token_cannot_read_another_conversation(self) -> None:
        other = self.client.post(
            "/api/widget/sessions",
            json={"customer_name": "Other"},
            headers={"X-Widget-Token": self.token},
        ).json()["conversation"]["id"]
        response = self.client.get(f"/api/widget/sessions/{other}/messages", headers=self.headers)
        self.assertEqual(response.status_code, 404)

    def test_internal_notes_are_not_exposed_to_widget(self) -> None:
        self.client.post(
            f"/api/conversations/{self.conversation_id}/notes",
            json={"content": "private operator note"},
            headers={"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"},
        )
        response = self.client.get(
            f"/api/widget/sessions/{self.conversation_id}/messages", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("private operator note", response.text)

    def test_history_exposes_only_the_conversation_status_header(self) -> None:
        transitioned = self.services.database.transition_conversation(
            "demo",
            self.conversation_id,
            [ConversationStatus.OPEN],
            ConversationStatus.WAITING_HUMAN,
        )
        self.assertIsNotNone(transitioned)
        response = self.client.get(
            f"/api/widget/sessions/{self.conversation_id}/messages", headers=self.headers
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-conversation-status"], "waiting_human")
        self.assertIsInstance(response.json(), list)


if __name__ == "__main__":
    unittest.main()
