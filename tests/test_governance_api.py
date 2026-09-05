"""Governance-plane production wiring tests (ROADMAP 2.5.0).

The 43.5 governance machinery (approvals, capability tokens, tool
policies) was test-only until now. These tests pin the production
surface:

1. **Approvals API** — maker-checker for ``tool_enablement``: request →
   decide (self-approval refused), list by status, RBAC.
2. **The first mutating tool** — ``knowledge.draft`` through the
   copilot endpoint: lands ``pending_review``, audited, RBAC-gated,
   schema-denied on bad arguments.
3. **Capability secret wiring** — with ``CAPABILITY_SECRET`` set the
   endpoint mints + presents a token the gateway verifies; validation
   refuses short secrets.
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

ADMIN_KEY = "gov-admin-key-00001"
ADMIN2_KEY = "gov-admin2-key-0002"
VIEWER_KEY = "gov-viewer-key-0001"
SECRET = "capability-secret-0123456789abcdef-0123456789abcdef"


def _principals() -> dict[str, Any]:
    return {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "gov.admin", "role": "admin"},
        ADMIN2_KEY: {"tenant_id": "demo", "actor_id": "gov.admin2", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "gov.viewer", "role": "viewer"},
    }


def _settings(db_path: Path, *, capability_secret: str = "") -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(_principals()),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
        capability_secret=capability_secret,
    )


class GovernanceApprovalsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(_settings(Path(self._tmp.name) / "gov.db")))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.admin2 = {"X-API-Key": ADMIN2_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self._tmp.cleanup()

    def test_request_decide_and_list_round_trip(self) -> None:
        created = self.client.post(
            "/api/admin/governance/approvals/request",
            headers=self.admin,
            json={
                "subject_kind": "tool_enablement",
                "subject_id": "knowledge.publish_bulk",
                "reason": "supervisor bulk publishing",
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        approval_id = created.json()["id"]
        self.assertEqual(created.json()["decision"], "pending")

        # Maker-checker: the requester cannot approve their own request.
        self_approval = self.client.post(
            f"/api/admin/governance/approvals/{approval_id}/decide",
            headers=self.admin,
            json={"approve": True, "reason": "self"},
        )
        self.assertEqual(self_approval.status_code, 409, self_approval.text)

        decided = self.client.post(
            f"/api/admin/governance/approvals/{approval_id}/decide",
            headers=self.admin2,
            json={"approve": True, "reason": "second pair of eyes"},
        )
        self.assertEqual(decided.status_code, 200, decided.text)
        self.assertEqual(decided.json()["decision"], "approved")

        pending = self.client.get(
            "/api/admin/governance/approvals?status=pending", headers=self.admin
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json(), [])
        approved = self.client.get(
            "/api/admin/governance/approvals?status=approved", headers=self.admin
        )
        self.assertEqual(len(approved.json()), 1)

        # RBAC: a viewer cannot open requests.
        forbidden = self.client.post(
            "/api/admin/governance/approvals/request",
            headers=self.viewer,
            json={"subject_kind": "tool_enablement", "subject_id": "x"},
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_duplicate_open_request_conflicts(self) -> None:
        body = {
            "subject_kind": "tool_enablement",
            "subject_id": "knowledge.publish_bulk",
        }
        first = self.client.post(
            "/api/admin/governance/approvals/request", headers=self.admin, json=body
        )
        self.assertEqual(first.status_code, 200)
        second = self.client.post(
            "/api/admin/governance/approvals/request", headers=self.admin2, json=body
        )
        self.assertEqual(second.status_code, 409, second.text)

    def test_gateway_sees_the_wired_governance_service(self) -> None:
        gateway = self.services.orchestrator.tools
        self.assertIs(gateway.governance_service, self.services.ai_governance)


class KnowledgeDraftToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "draft.db"
        self.client = TestClient(create_app(_settings(self.db_path, capability_secret=SECRET)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self._tmp.cleanup()

    def _draft_body(self, **overrides: Any) -> dict[str, Any]:
        body: dict[str, Any] = {
            "title": "配送时效政策",
            "content": "首单配送时效承诺为 48 小时，偏远地区顺延两个工作日。",
            "tags": ["配送", "政策"],
            "category": "shipping",
        }
        body.update(overrides)
        return body

    def test_mutating_call_lands_pending_review_and_audits(self) -> None:
        response = self.client.post(
            "/api/copilot/knowledge-draft", headers=self.admin, json=self._draft_body()
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["tool"], "knowledge.draft")
        self.assertEqual(body["status"], "draft")
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT status FROM knowledge_articles WHERE id = ?", (body["article_id"],)
            ).fetchone()
            audits = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE tenant_id='demo' "
                "AND event_type='tool.knowledge_drafted'"
            ).fetchone()
        self.assertEqual(row["status"], "draft")
        self.assertEqual(int(audits["n"]), 1)

    def test_capability_token_is_minted_and_verified(self) -> None:
        # With CAPABILITY_SECRET set the endpoint mints a token per call; the
        # gateway verifies it against the same secret (a forged/absent token
        # cannot be told apart here — but a poisoned schema digest would
        # fail, so also prove the denial path below).
        gateway = self.services.orchestrator.tools
        token = gateway.mint_capability_token("knowledge.draft", "demo")
        self.assertIsNotNone(token)
        response = self.client.post(
            "/api/copilot/knowledge-draft", headers=self.admin, json=self._draft_body()
        )
        self.assertEqual(response.status_code, 200, response.text)

        # Schema denial: the tool refuses out-of-bounds arguments and the
        # denial is audited.
        bad = self.client.post(
            "/api/copilot/knowledge-draft",
            headers=self.admin,
            json=self._draft_body(content="太短"),
        )
        self.assertEqual(bad.status_code, 403, bad.text)
        # The Problem Details body renders the denial as a string (the
        # middleware normalizes detail); the machine-readable record lives in
        # the tool.denied audit.
        self.assertIn("schema", bad.json()["detail"])
        with self.services.database.connect() as conn:
            audits = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE tenant_id='demo' "
                "AND event_type='tool.denied'"
            ).fetchone()
        self.assertEqual(int(audits["n"]), 1)

    def test_viewer_cannot_call_the_mutating_tool(self) -> None:
        forbidden = self.client.post(
            "/api/copilot/knowledge-draft", headers=self.viewer, json=self._draft_body()
        )
        self.assertEqual(forbidden.status_code, 403)

    def test_gateway_without_secret_rejects_presented_tokens(self) -> None:
        # Without CAPABILITY_SECRET the gateway cannot verify tokens: a call
        # that presents one fails closed with token_unsupported. This app is
        # built WITHOUT the secret (unlike the class default).
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(_settings(Path(tmp) / "nosecret.db")))
            services = cast(Any, client.app).state.services
            gateway = services.orchestrator.tools
            self.assertIsNone(gateway.mint_capability_token("knowledge.draft", "demo"))
            execution = gateway.draft_knowledge(
                "demo",
                {
                    "title": "无密钥调用",
                    "content": "这个调用携带了一个不可能被验证的令牌。",
                    "tags": ["x"],
                },
                actor_id="gov.admin",
                capability={"payload": "forged"},
            )
            self.assertFalse(execution.success)
            self.assertEqual(execution.code, "policy_denied")
            services.database.close()


class CapabilitySecretValidationTests(unittest.TestCase):
    def test_short_secret_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "CAPABILITY_SECRET"):
                Settings(
                    database_path=Path(tmp) / "s.db",
                    auth_mode="api_key",
                    api_keys_json=json.dumps(_principals()),
                    capability_secret="short",
                ).validate()

    def test_empty_secret_is_the_dev_default(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            Settings(
                database_path=Path(tmp) / "s.db",
                auth_mode="demo",
                capability_secret="",
            ).validate()


if __name__ == "__main__":
    unittest.main()
