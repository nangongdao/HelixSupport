"""Backlog: auto-routing rules.

Covers the roadmap acceptance for auto-routing:
- rules match by intent/label/channel and pick the highest priority;
- a conversation with a matching rule is assigned to a group agent with
  spare capacity (round-robin across least-loaded members);
- a full group leaves the conversation unassigned with a routing audit;
- agent groups carry skill tags and a per-agent capacity cap.
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

ADMIN_KEY = "route-admin-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class AutoRoutingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "routing.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _group(self, capacity: int = 1) -> str:
        response = self.client.post(
            "/api/admin/agent-groups",
            json={"name": "Order Support", "skills": ["orders"], "capacity": capacity},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        group_id = response.json()["id"]
        for actor in ("agent.a", "agent.b"):
            added = self.client.post(
                f"/api/admin/agent-groups/{group_id}/agents",
                json={"actor_id": actor},
                headers=self.admin,
            )
            self.assertEqual(added.status_code, 201)
        return group_id

    def _rule(self, group_id: str, intent: str | None = None, priority: int = 10) -> str:
        payload: dict[str, Any] = {"group_id": group_id, "priority": priority}
        if intent:
            payload["intent"] = intent
        response = self.client.post("/api/admin/routing-rules", json=payload, headers=self.admin)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _turn(self, content: str, key: str, customer_ref: str | None = "CUST-1001") -> dict:
        conv = self.client.post(
            "/api/conversations",
            json={"customer_name": "C", "customer_ref": customer_ref},
            headers=self.admin,
        ).json()
        return self.client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": content},
            headers={**self.admin, "Idempotency-Key": key},
        ).json()

    def _routing_audits(self) -> list[str]:
        with self.services.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE event_type LIKE 'routing.%'"
            ).fetchall()
        return [row["event_type"] for row in rows]

    def test_group_and_rule_crud(self) -> None:
        group_id = self._group()
        listed = self.client.get("/api/admin/agent-groups", headers=self.admin)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()), 1)
        self.assertEqual(listed.json()[0]["skills"], ["orders"])
        rules = self.client.get("/api/admin/routing-rules", headers=self.admin)
        self.assertEqual(rules.json(), [])
        rule_id = self._rule(group_id, intent="order_status")
        rules = self.client.get("/api/admin/routing-rules", headers=self.admin).json()
        self.assertEqual(len(rules), 1)
        self.assertEqual(rules[0]["intent"], "order_status")
        deleted = self.client.delete(f"/api/admin/routing-rules/{rule_id}", headers=self.admin)
        self.assertEqual(deleted.status_code, 204)

    def test_intent_rule_assigns_group_agent(self) -> None:
        group_id = self._group()
        self._rule(group_id, intent="order_status")
        turn = self._turn("ORD-10482 到哪了", "route-a-1")
        self.assertEqual(turn["conversation"]["status"], "open")
        assigned = turn["conversation"].get("assigned_agent")
        self.assertIn(assigned, ("agent.a", "agent.b"))
        self.assertIn("routing.assigned", self._routing_audits())

    def test_capacity_full_leaves_unassigned_with_audit(self) -> None:
        group_id = self._group(capacity=1)
        self._rule(group_id, intent="order_status")
        # Two agents each at capacity 1 -> two assignments, third stays pooled.
        for index in range(2):
            turn = self._turn("ORD-10482 到哪了", f"route-cap-{index}")
            self.assertIn(turn["conversation"].get("assigned_agent"), ("agent.a", "agent.b"))
        third = self._turn("ORD-10482 到哪了", "route-cap-2")
        self.assertEqual(third["conversation"].get("assigned_agent"), "order")
        self.assertIn("routing.group_full", self._routing_audits())

    def test_no_rule_leaves_pooled(self) -> None:
        self._group()
        turn = self._turn("ORD-10482 到哪了", "route-norule-1")
        self.assertEqual(turn["conversation"].get("assigned_agent"), "order")
        self.assertEqual(self._routing_audits(), [])

    def test_priority_picks_highest(self) -> None:
        group_a = self._group()
        group_b = self.client.post(
            "/api/admin/agent-groups",
            json={"name": "General", "skills": [], "capacity": 2},
            headers=self.admin,
        ).json()["id"]
        # Higher-priority channel rule wins over lower-priority intent rule.
        self._rule(group_a, intent="order_status", priority=5)
        self.client.post(
            "/api/admin/routing-rules",
            json={"group_id": group_b, "channel": "web", "priority": 20},
            headers=self.admin,
        )
        turn = self._turn("ORD-10482 到哪了", "route-prio-1")
        # The channel=web rule (priority 20) matches first (priority DESC), so
        # the turn routes to group_b — which has no agents, hence unassigned
        # (pooled) with a group_full audit.
        self.assertEqual(turn["conversation"].get("assigned_agent"), "order")
        self.assertIn("routing.group_full", self._routing_audits())

    def test_operator_cannot_manage_routing(self) -> None:
        # Operator role lacks admin:manage -> 403.
        op_key = "route-op-key-000001"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
            op_key: {"tenant_id": "demo", "actor_id": "op", "role": "operator"},
        }
        db_path = Path(self._tmp.name) / "routing-op.db"
        client = TestClient(
            create_app(
                Settings(
                    database_path=db_path,
                    auth_mode="api_key",
                    api_keys_json=json.dumps(principals),
                    rate_limit_per_minute=10000,
                    docs_enabled=False,
                )
            )
        )
        try:
            response = client.get(
                "/api/admin/agent-groups",
                headers={"X-API-Key": op_key, "X-Tenant-Id": "demo"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            cast(Any, client.app).state.services.database.close()
            client.close()


if __name__ == "__main__":
    unittest.main()
