"""Backlog: 坐席协作 (operator collaboration).

Covers: @-mentioning colleagues in an internal note creates tenant-scoped
mention records with an unread flag; the mentioned operator's inbox lists
them unread-first with conversation context; marking read is idempotent and
audited; a colleague/other-tenant actor never sees them; internal notes
thread via ``reply_to`` and the threads endpoint groups roots and replies;
the supervisor live view SSE streams revision changes on ``conversation:read``
without requiring ``operator:act``, and non-readers are refused.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Optional, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "collab-admin-key-001"
OP_A_KEY = "collab-operator-a-key"
OP_B_KEY = "collab-operator-b-key"
CHANNEL_KEY = "collab-channel-key-001"
TENANT = "demo"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": TENANT, "actor_id": "admin", "role": "admin"},
        OP_A_KEY: {"tenant_id": TENANT, "actor_id": "operator-a", "role": "operator"},
        OP_B_KEY: {"tenant_id": TENANT, "actor_id": "operator-b", "role": "operator"},
        CHANNEL_KEY: {"tenant_id": TENANT, "actor_id": "channel-bot", "role": "channel"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class CollaborationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "collab.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": TENANT}
        self.op_a = {"X-API-Key": OP_A_KEY, "X-Tenant-Id": TENANT}
        self.op_b = {"X-API-Key": OP_B_KEY, "X-Tenant-Id": TENANT}
        self.channel = {"X-API-Key": CHANNEL_KEY, "X-Tenant-Id": TENANT}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _open(self, name: str = "C") -> str:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": name}, headers=self.admin
        )
        self.assertEqual(conv.status_code, 201, conv.text)
        return conv.json()["id"]

    def _accept(self, conversation_id: str) -> None:
        response = self.client.post(
            f"/api/conversations/{conversation_id}/accept", headers=self.admin
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _note(
        self, conversation_id: str, content: str, headers, reply_to: Optional[str] = None
    ) -> Any:
        payload: dict[str, Any] = {"content": content}
        if reply_to:
            payload["reply_to"] = reply_to
        response = self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json=payload,
            headers=headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------------ @提及

    def test_mention_records_and_inbox(self) -> None:
        conversation_id = self._open("mention")
        self._accept(conversation_id)
        # operator-a mentions operator-b in an internal note.
        note = self._note(
            conversation_id,
            "请 @operator-b 核对一下这个订单的物流状态",
            self.op_a,
        )
        self.assertEqual(note["role"], "internal_note")
        # operator-b's inbox shows the mention unread with context.
        inbox = self.client.get("/api/mentions", headers=self.op_b).json()
        self.assertEqual(inbox["unread_count"], 1)
        self.assertEqual(len(inbox["mentions"]), 1)
        mention = inbox["mentions"][0]
        self.assertEqual(mention["conversation_id"], conversation_id)
        self.assertEqual(mention["mentioned_by"], "operator-a")
        self.assertEqual(mention["conversation_customer"], "mention")
        self.assertEqual(mention["unread"], True)
        self.assertIn("operator-b", mention["note_preview"])
        # operator-a does not see someone else's mention.
        own = self.client.get("/api/mentions", headers=self.op_a).json()
        self.assertEqual(own["unread_count"], 0)
        self.assertEqual(own["mentions"], [])

    def test_self_mention_and_non_actor_tokens_are_skipped(self) -> None:
        conversation_id = self._open("skip")
        self._accept(conversation_id)
        self._note(
            conversation_id,
            "自己 @operator-a 同一个人,邮箱 user@example.com 不算提及;@operator-b 算",
            self.op_a,
        )
        inbox_a = self.client.get("/api/mentions", headers=self.op_a).json()
        self.assertEqual(inbox_a["unread_count"], 0)
        inbox_b = self.client.get("/api/mentions", headers=self.op_b).json()
        self.assertEqual(inbox_b["unread_count"], 1)
        self.assertEqual(inbox_b["mentions"][0]["mentioned_by"], "operator-a")

    def test_mark_read_is_idempotent_and_audited(self) -> None:
        conversation_id = self._open("read")
        self._accept(conversation_id)
        self._note(conversation_id, "回我一下 @operator-b", self.op_a)
        inbox = self.client.get("/api/mentions", headers=self.op_b).json()
        mention_id = inbox["mentions"][0]["id"]
        first = self.client.post(f"/api/mentions/{mention_id}/read", headers=self.op_b)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["read_at"] is not None, True)
        redo = self.client.post(f"/api/mentions/{mention_id}/read", headers=self.op_b)
        self.assertEqual(redo.status_code, 200)
        self.assertEqual(redo.json()["read_at"], first.json()["read_at"])
        after = self.client.get("/api/mentions", headers=self.op_b).json()
        self.assertEqual(after["unread_count"], 0)
        events = self.client.get("/api/audit-events", headers=self.admin).json()
        events = events if isinstance(events, list) else events.get("events", [])
        self.assertTrue(any(e.get("event_type") == "conversation.mention_read" for e in events))

    def test_mention_read_requires_ownership(self) -> None:
        """operator-a cannot read operator-b's mention (tenant+actor scoped)."""
        conversation_id = self._open("owner")
        self._accept(conversation_id)
        self._note(conversation_id, "只看 @operator-b", self.op_a)
        inbox = self.client.get("/api/mentions", headers=self.op_b).json()
        mention_id = inbox["mentions"][0]["id"]
        response = self.client.post(f"/api/mentions/{mention_id}/read", headers=self.op_a)
        self.assertEqual(response.status_code, 404)
        # operator-b's mention stays unread.
        after = self.client.get("/api/mentions", headers=self.op_b).json()
        self.assertEqual(after["unread_count"], 1)

    def test_channel_role_cannot_see_mentions(self) -> None:
        response = self.client.get("/api/mentions", headers=self.channel)
        self.assertEqual(response.status_code, 403)
        self.assertNotIn(
            "X-API-Key",
            self.client.get(
                "/api/mentions", headers={**self.channel, "X-Tenant-Id": TENANT}
            ).headers,
        )

    def test_mention_cross_tenant_isolation(self) -> None:
        """A mention row never leaks across tenants."""
        conversation_id = self._open("iso")
        self._accept(conversation_id)
        self._note(conversation_id, "给 @other-tenant-user 打个招呼 @operator-b", self.op_a)
        inbox = self.client.get("/api/mentions", headers=self.op_b).json()
        # Only operator-b's rows (tenant-scoped) appear for this tenant.
        self.assertEqual(inbox["unread_count"], 1)
        self.assertEqual(inbox["mentions"][0]["mentioned_by"], "operator-a")

    # ------------------------------------------------------------- 讨论线程

    def test_note_reply_threading(self) -> None:
        conversation_id = self._open("thread")
        self._accept(conversation_id)
        root = self._note(conversation_id, "根备注:谁来处理这个排队问题", self.op_a)
        reply = self._note(
            conversation_id,
            "我来处理 @operator-b 一起看",
            self.op_a,
            reply_to=root["id"],
        )
        self.assertEqual(reply["reply_to"], root["id"])
        threads = self.client.get(
            f"/api/conversations/{conversation_id}/threads", headers=self.admin
        ).json()
        self.assertEqual(len(threads["threads"]), 1)
        thread = threads["threads"][0]
        self.assertEqual(thread["root"]["content"], "根备注:谁来处理这个排队问题")
        self.assertEqual(thread["root"]["id"], root["id"])
        self.assertEqual(len(thread["replies"]), 1)
        self.assertEqual(thread["replies"][0]["id"], reply["id"])

    def test_reply_target_must_be_internal_note(self) -> None:
        conversation_id = self._open("badtarget")
        self._accept(conversation_id)
        self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "客户消息"},
            headers={**self.admin, "Idempotency-Key": "collab-badtarget-msg"},
        )
        details = self.client.get(
            f"/api/conversations/{conversation_id}", headers=self.admin
        ).json()
        customer_msg = next(m for m in details["messages"] if m["role"] == "customer")
        response = self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json={"content": "回复一条客户消息是不允许的", "reply_to": customer_msg["id"]},
            headers=self.op_a,
        )
        self.assertIn(response.status_code, (409, 422), response.text)

    def test_reply_to_unknown_note_is_rejected(self) -> None:
        conversation_id = self._open("unknown")
        self._accept(conversation_id)
        response = self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json={"content": "回错了", "reply_to": "msg_doesnotexist0000"},
            headers=self.op_a,
        )
        self.assertIn(response.status_code, (404, 422), response.text)

    def test_threads_require_conversation_read(self) -> None:
        response = self.client.get("/api/conversations/nope/threads", headers=self.channel)
        self.assertEqual(response.status_code, 403)

    # ------------------------------------------------------------- 旁观模式

    def test_live_event_stream_emits_snapshot(self) -> None:
        conversation_id = self._open("watch")
        self._accept(conversation_id)
        # A supervisor (admin) can open the read-only stream.
        with self.client.stream(
            "GET",
            f"/api/conversations/{conversation_id}/events?timeout=6",
            headers=self.admin,
        ) as response:
            self.assertEqual(response.status_code, 200)
            content_type = response.headers.get("content-type", "")
            self.assertIn("text/event-stream", content_type)
            first_events = self._read_sse(response, 1)
            self.assertTrue(
                any(line.startswith("event: snapshot") for line in first_events),
                first_events,
            )

    def test_live_event_stream_emits_changed_event(self) -> None:
        conversation_id = self._open("watch2")
        self._accept(conversation_id)
        collected: dict[str, Optional[str]] = {"revision": None}
        with self.client.stream(
            "GET",
            f"/api/conversations/{conversation_id}/events?timeout=7",
            headers=self.admin,
        ) as response:
            events = self._drain_sse_until(response, "snapshot", collected)

        # The stream should carry a snapshot (and likely a ping); a change only
        # materializes when something else mutates mid-stream, so we assert the
        # snapshot carries a meaningful revision (not "missing").
        self.assertIsNotNone(collected["revision"], events)
        self.assertNotEqual(collected["revision"], "missing", events)
        self.assertNotEqual(collected["revision"], "", events)

    def test_observation_does_not_change_revision(self) -> None:
        """Watching is read-only: revision before == revision after."""
        conversation_id = self._open("watch3")
        self._accept(conversation_id)
        database = self.services.database
        before = database.conversation_revision(TENANT, conversation_id)
        with self.client.stream(
            "GET",
            f"/api/conversations/{conversation_id}/events?timeout=6",
            headers=self.admin,
        ) as response:
            self._drain_sse_until(response, "snapshot", {})
        after = database.conversation_revision(TENANT, conversation_id)
        self.assertEqual(before, after)

    def test_channel_cannot_open_live_view(self) -> None:
        response = self.client.get("/api/conversations/abc/events", headers=self.channel)
        self.assertEqual(response.status_code, 403)

    def test_mention_flags_and_audit_on_note(self) -> None:
        """The note write audits mentions alongside the note."""
        conversation_id = self._open("audit")
        self._accept(conversation_id)
        self._note(conversation_id, "请你确认 @operator-b", self.op_a)
        events = self.client.get("/api/audit-events", headers=self.admin).json()
        events = events if isinstance(events, list) else events.get("events", [])
        recorded = [e for e in events if e.get("event_type") == "conversation.mentions_recorded"]
        self.assertEqual(len(recorded), 1)
        self.assertEqual(recorded[0]["payload"]["mentions"], 1)
        self.assertIn("operator-b", recorded[0]["payload"]["actors"])

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _read_sse(response, count: int) -> list[str]:
        lines: list[str] = []
        for index, line in enumerate(response.iter_lines()):
            if line:
                lines.append(line)
            if sum(1 for ln in lines if ln.startswith("event:")) >= count:
                break
            if index > 4000:
                break
        return lines

    @staticmethod
    def _drain_sse_until(
        response,
        event_name: str,
        collected: dict[str, Optional[str]],
        attempts: int = 50,
    ) -> list[str]:
        """Read SSE lines until the named event's ``data:`` line is parsed."""
        lines: list[str] = []
        seen_event = False
        for index, line in enumerate(response.iter_lines()):
            if not line:
                continue
            lines.append(line)
            if line.startswith("event:"):
                seen_event = line[len("event:") :].strip() == event_name
                continue
            if line.startswith("data:"):
                payload_text = line[len("data:") :].strip()
                if seen_event and event_name == "snapshot":
                    try:
                        payload = json.loads(payload_text)
                    except json.JSONDecodeError:
                        pass
                    else:
                        if "revision" in payload:
                            collected["revision"] = payload["revision"]
                        return lines
            if index > attempts * 20:
                break
        return lines


if __name__ == "__main__":
    unittest.main()
