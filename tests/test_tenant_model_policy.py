"""Phase 19.4: per-tenant model policy and daily turn budget.

Covers the contract the budget gate depends on:
- The database layer stores and reads the policy and increments usage.
- The orchestrator degrades to the deterministic path and emits a
  ``turn.budget_exceeded`` audit event once the daily cap is reached, and
  increments usage for every processed turn.
- The admin API exposes the policy and refuses cross-tenant writes.
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
from app.main import create_app
from app.migrations import all_migrations, run_migrations
from app.orchestrator import ConversationOrchestrator

ADMIN_KEY = "admin-test-key-0001"
OTHER_ADMIN_KEY = "other-admin-key-001"


class TenantModelPolicyDbTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        with self.database.connect() as conn:
            run_migrations(conn, all_migrations())
        self.database.ensure_tenant("tenant-1", "Test One")
        self.database.ensure_tenant("tenant-2", "Test Two")

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def test_policy_defaults_unlimited(self) -> None:
        policy = self.database.get_tenant_model_policy("tenant-1")
        self.assertIsNone(policy["allowed_models"])
        self.assertIsNone(policy["daily_turn_budget"])

    def test_set_and_get_policy(self) -> None:
        self.database.set_tenant_model_policy("tenant-1", ["gpt-4o", "claude"], 100)
        policy = self.database.get_tenant_model_policy("tenant-1")
        self.assertEqual(policy["allowed_models"], ["gpt-4o", "claude"])
        self.assertEqual(policy["daily_turn_budget"], 100)

    def test_set_null_allowed_models_clears_restriction(self) -> None:
        self.database.set_tenant_model_policy("tenant-1", ["gpt-4o"], 100)
        self.database.set_tenant_model_policy("tenant-1", None, None)
        policy = self.database.get_tenant_model_policy("tenant-1")
        self.assertIsNone(policy["allowed_models"])
        self.assertIsNone(policy["daily_turn_budget"])

    def test_increment_usage_counts_up(self) -> None:
        today = "2026-08-12"
        self.assertEqual(self.database.increment_tenant_usage("tenant-1", today), 1)
        self.assertEqual(self.database.increment_tenant_usage("tenant-1", today), 2)
        self.assertEqual(self.database.increment_tenant_usage("tenant-1", today), 3)
        self.assertEqual(self.database.get_tenant_daily_usage("tenant-1", today), 3)

    def test_usage_isolated_per_tenant_and_date(self) -> None:
        self.database.increment_tenant_usage("tenant-1", "2026-08-12")
        self.database.increment_tenant_usage("tenant-1", "2026-08-12")
        self.database.increment_tenant_usage("tenant-2", "2026-08-12")
        self.database.increment_tenant_usage("tenant-1", "2026-08-13")
        self.assertEqual(self.database.get_tenant_daily_usage("tenant-1", "2026-08-12"), 2)
        self.assertEqual(self.database.get_tenant_daily_usage("tenant-2", "2026-08-12"), 1)
        self.assertEqual(self.database.get_tenant_daily_usage("tenant-1", "2026-08-13"), 1)

    def test_set_policy_unknown_tenant_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.database.set_tenant_model_policy("nope", ["gpt-4o"], 10)

    def test_get_policy_unknown_tenant_raises(self) -> None:
        with self.assertRaises(LookupError):
            self.database.get_tenant_model_policy("nope")


class OrchestratorBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        self.database.initialize()
        # Seed the demo tenant so knowledge retrieval hits and turns stay
        # open; a tenant with no knowledge would escalate every query and
        # the second turn would be suppressed (not a budget signal).
        self.database.seed_demo()
        self.tenant_id = "demo"
        self.settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.orchestrator = ConversationOrchestrator(self.database, self.settings)
        conv = self.database.create_conversation(
            self.tenant_id, "Customer", None, "web", "admin", 120
        )
        self.conv_id = conv["id"]

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def _send(self, content: str, key: str) -> dict[str, Any]:
        return self.orchestrator.handle_customer_message(
            self.tenant_id, self.conv_id, content, "admin", key
        )

    def _audit_events(self) -> list[dict[str, Any]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type, payload_json FROM audit_events "
                "WHERE tenant_id = ? ORDER BY created_at, seq",
                (self.tenant_id,),
            ).fetchall()
        return [
            {"event_type": r["event_type"], "payload": json.loads(r["payload_json"])} for r in rows
        ]

    def test_no_budget_means_no_exceeded_flag(self) -> None:
        response = self._send("配送一般多久能到？", "idem-budget-1")
        metadata = response["assistant_message"]["metadata"]  # type: ignore[index]
        self.assertFalse(metadata["budget_exceeded"])
        types = [e["event_type"] for e in self._audit_events()]
        self.assertNotIn("turn.budget_exceeded", types)

    def test_budget_exceeded_degrades_and_audits(self) -> None:
        # Cap at one turn; the second turn must flag the budget and audit.
        self.database.set_tenant_model_policy(self.tenant_id, None, 1)
        self._send("配送一般多久能到？", "idem-budget-first")
        second = self._send("退货政策是什么？", "idem-budget-second")
        metadata = second["assistant_message"]["metadata"]  # type: ignore[index]
        self.assertTrue(metadata["budget_exceeded"])
        exceeded = [e for e in self._audit_events() if e["event_type"] == "turn.budget_exceeded"]
        self.assertEqual(len(exceeded), 1)
        self.assertEqual(exceeded[0]["payload"]["limit"], 1)

    def test_usage_incremented_for_each_processed_turn(self) -> None:
        from app.database import utc_now

        today = utc_now()[:10]
        self._send("配送一般多久能到？", "idem-usage-1")
        self._send("退货政策是什么？", "idem-usage-2")
        self.assertEqual(self.database.get_tenant_daily_usage(self.tenant_id, today), 2)


class TenantModelPolicyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test.db"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "agent.admin", "role": "admin"},
            OTHER_ADMIN_KEY: {
                "tenant_id": "other-tenant",
                "actor_id": "other.admin",
                "role": "admin",
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
        self.headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.other_headers = {"X-API-Key": OTHER_ADMIN_KEY, "X-Tenant-Id": "other-tenant"}

    def tearDown(self) -> None:
        self.client.close()
        cast(Any, self.client.app).state.services.database.close()
        self._tmp.cleanup()

    def test_get_default_policy(self) -> None:
        response = self.client.get("/api/admin/tenants/demo/model-policy", headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tenant_id"], "demo")
        self.assertIsNone(body["allowed_models"])
        self.assertIsNone(body["daily_turn_budget"])
        self.assertEqual(body["daily_turn_count"], 0)

    def test_set_and_get_policy(self) -> None:
        response = self.client.put(
            "/api/admin/tenants/demo/model-policy",
            json={"allowed_models": ["gpt-4o"], "daily_turn_budget": 50},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["allowed_models"], ["gpt-4o"])
        self.assertEqual(body["daily_turn_budget"], 50)

        got = self.client.get("/api/admin/tenants/demo/model-policy", headers=self.headers).json()
        self.assertEqual(got["allowed_models"], ["gpt-4o"])
        self.assertEqual(got["daily_turn_budget"], 50)

    def test_cross_tenant_write_forbidden(self) -> None:
        # demo's admin must not write other-tenant's policy.
        response = self.client.put(
            "/api/admin/tenants/other-tenant/model-policy",
            json={"daily_turn_budget": 5},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_cross_tenant_read_forbidden(self) -> None:
        # demo's admin must not read other-tenant's policy (S5: GET direction
        # on a real foreign tenant is refused before any DB access).
        response = self.client.get(
            "/api/admin/tenants/other-tenant/model-policy",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 403, response.text)

    def test_foreign_tenant_forbidden(self) -> None:
        # demo's admin cannot read another tenant's policy; the response is
        # 403 regardless of whether the foreign tenant exists, so existence
        # is not leaked.
        response = self.client.get("/api/admin/tenants/ghost/model-policy", headers=self.headers)
        self.assertEqual(response.status_code, 403, response.text)

    def test_budget_blocks_turn_after_cap_via_api(self) -> None:
        # End-to-end: cap at 1, send two messages, second flags budget.
        self.client.put(
            "/api/admin/tenants/demo/model-policy",
            json={"daily_turn_budget": 1},
            headers=self.headers,
        )
        created = self.client.post(
            "/api/conversations",
            json={"customer_name": "Budget Customer", "channel": "web"},
            headers=self.headers,
        ).json()
        cid = created["id"]
        first = self.client.post(
            f"/api/conversations/{cid}/messages",
            json={"content": "配送一般多久能到？"},
            headers=dict(self.headers, **{"Idempotency-Key": "idem-api-1"}),
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertFalse(first.json()["assistant_message"]["metadata"]["budget_exceeded"])
        second = self.client.post(
            f"/api/conversations/{cid}/messages",
            json={"content": "退货政策是什么？"},
            headers=dict(self.headers, **{"Idempotency-Key": "idem-api-2"}),
        )
        self.assertEqual(second.status_code, 200, second.text)
        self.assertTrue(second.json()["assistant_message"]["metadata"]["budget_exceeded"])


if __name__ == "__main__":
    unittest.main()
