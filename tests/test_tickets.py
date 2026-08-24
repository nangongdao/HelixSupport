"""Backlog: ticketing (工单化).

Covers the roadmap acceptance:
- converting a conversation creates a ticket snapshot (customer identity +
  latest customer message as description) and sets conversations.ticket_id;
- converting again is idempotent (returns the existing ticket);
- the ticket state machine enforces open -> in_progress/closed,
  in_progress -> closed, closed -> open, with audits and 409 on invalid moves;
- another conversation can be linked to the ticket for cross-conversation
  tracking; the detail lists linked conversations (no internal notes);
- listing filters by status / customer_ref and stays tenant-scoped;
- RBAC: writes require operator:act; reads require conversation:read;
- ConversationOut carries ticket_id (append-only).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "tickets-admin-key-001"
VIEWER_KEY = "tickets-viewer-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer", "role": "viewer"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class TicketAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "tickets.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _open_conversation(self, name: str = "S", customer_ref: str | None = None) -> str:
        conv = self.client.post(
            "/api/conversations",
            json={"customer_name": name, "customer_ref": customer_ref},
            headers=self.admin,
        ).json()
        return conv["id"]

    def _send(self, conversation_id: str, content: str, key: str) -> None:
        response = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
            headers={**self.admin, "Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _create_ticket(self, conversation_id: str, subject: str = "发货异常") -> dict[str, Any]:
        response = self.client.post(
            "/api/tickets",
            json={"conversation_id": conversation_id, "subject": subject},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # ------------------------------------------------------------- conversion

    def test_convert_snapshot_and_marks_conversation(self) -> None:
        conversation_id = self._open_conversation("张三", "CUST-1001")
        self._send(conversation_id, "我的订单 ORD-888 一直没发货", "ticket-key-1")
        ticket = self._create_ticket(conversation_id, "发货异常")
        self.assertEqual(ticket["status"], "open")
        self.assertEqual(ticket["customer_name"], "张三")
        self.assertEqual(ticket["customer_ref"], "CUST-1001")
        self.assertEqual(ticket["source_conversation_id"], conversation_id)
        self.assertIn("ORD-888", ticket["description"])
        detail = self.client.get(f"/api/conversations/{conversation_id}", headers=self.admin).json()
        self.assertEqual(detail["conversation"]["ticket_id"], ticket["id"])

    def test_convert_is_idempotent(self) -> None:
        conversation_id = self._open_conversation()
        first = self._create_ticket(conversation_id)
        second = self._create_ticket(conversation_id, "另一个主题")
        self.assertEqual(second["id"], first["id"])
        self.assertEqual(second["subject"], first["subject"])

    def test_convert_unknown_conversation_404(self) -> None:
        response = self.client.post(
            "/api/tickets",
            json={"conversation_id": "conv_nonexistent", "subject": "问题"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_explicit_description_used(self) -> None:
        conversation_id = self._open_conversation()
        response = self.client.post(
            "/api/tickets",
            json={
                "conversation_id": conversation_id,
                "subject": "备用问题",
                "description": "自定义描述内容",
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["description"], "自定义描述内容")

    # -------------------------------------------------------------- lifecycle

    def test_transition_state_machine(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "in_progress", "reason": "开始处理"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "in_progress")
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "closed"},
            headers=self.admin,
        )
        self.assertEqual(response.json()["status"], "closed")
        self.assertIsNotNone(response.json()["closed_at"])
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "open"},
            headers=self.admin,
        )
        self.assertEqual(response.json()["status"], "open")

    def test_invalid_transition_409(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "closed"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        # closed -> in_progress is not a legal move.
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "in_progress"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_transition_audited(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        self.client.post(
            f"/api/tickets/{ticket['id']}/transition",
            json={"status": "closed"},
            headers=self.admin,
        )
        with self.services.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE event_type LIKE 'ticket.%'"
            ).fetchall()
        events = [row["event_type"] for row in rows]
        self.assertIn("ticket.created", events)
        self.assertIn("ticket.transitioned", events)

    # ----------------------------------------------------- cross-conversation

    def test_link_second_conversation(self) -> None:
        first = self._open_conversation("客户A", "CUST-2001")
        second = self._open_conversation("客户A", "CUST-2001")
        ticket = self._create_ticket(first)
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/link",
            json={"conversation_id": second},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        detail = self.client.get(f"/api/tickets/{ticket['id']}", headers=self.admin).json()
        conversation_ids = [item["id"] for item in detail["conversations"]]
        self.assertIn(first, conversation_ids)
        self.assertIn(second, conversation_ids)
        # The linked conversation is marked too.
        detail2 = self.client.get(f"/api/conversations/{second}", headers=self.admin).json()
        self.assertEqual(detail2["conversation"]["ticket_id"], ticket["id"])

    def test_link_unknown_404(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        response = self.client.post(
            f"/api/tickets/{ticket['id']}/link",
            json={"conversation_id": "conv_nonexistent"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_ticket_detail_never_leaks_internal_notes(self) -> None:
        conversation_id = self._open_conversation()
        self.client.post(
            f"/api/conversations/{conversation_id}/notes",
            json={"content": "内部机密：客户准备投诉到消协"},
            headers=self.admin,
        )
        ticket = self._create_ticket(conversation_id)
        response = self.client.get(f"/api/tickets/{ticket['id']}", headers=self.admin)
        body = response.text
        self.assertNotIn("内部机密", body)
        self.assertNotIn("消协", body)

    # ---------------------------------------------------------------- listing

    def test_list_filters_by_status_and_customer_ref(self) -> None:
        c1 = self._open_conversation("A", "CUST-3001")
        c2 = self._open_conversation("B", "CUST-3002")
        t1 = self._create_ticket(c1, "问题甲")
        self._create_ticket(c2, "问题乙")
        self.client.post(
            f"/api/tickets/{t1['id']}/transition",
            json={"status": "closed"},
            headers=self.admin,
        )
        response = self.client.get("/api/tickets?status=closed", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        closed = [t for t in response.json() if t["id"] == t1["id"]]
        self.assertEqual(len(closed), 1)
        response = self.client.get("/api/tickets?customer_ref=CUST-3001", headers=self.admin)
        ids = [t["id"] for t in response.json()]
        self.assertIn(t1["id"], ids)

    def test_update_ticket_fields(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        response = self.client.patch(
            f"/api/tickets/{ticket['id']}",
            json={"assigned_agent": "operator-1", "priority": "high"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["assigned_agent"], "operator-1")
        self.assertEqual(response.json()["priority"], "high")

    # --------------------------------------------------------------------- RBAC

    def test_viewer_denied_writes(self) -> None:
        conversation_id = self._open_conversation()
        calls = [
            ("POST", "/api/tickets", {"conversation_id": conversation_id, "subject": "x"}),
            ("PATCH", "/api/tickets/tkt_none", {"subject": "x"}),
            ("POST", "/api/tickets/tkt_none/transition", {"status": "closed"}),
            ("POST", "/api/tickets/tkt_none/link", {"conversation_id": conversation_id}),
        ]
        for method, path, body in calls:
            response = self.client.request(method, path, json=body, headers=self.viewer)
            self.assertEqual(response.status_code, 403, f"{method} {path}: {response.text}")

    def test_viewer_can_read_tickets(self) -> None:
        conversation_id = self._open_conversation()
        ticket = self._create_ticket(conversation_id)
        response = self.client.get(f"/api/tickets/{ticket['id']}", headers=self.viewer)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["id"], ticket["id"])


if __name__ == "__main__":
    unittest.main()
