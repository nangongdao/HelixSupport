"""Backlog: SLA policy engine.

Covers the roadmap acceptance:
- policies are configured per tenant/priority/channel;
- resolution falls back exact -> tenant default -> global default -> builtin;
- create_conversation applies the resolved deadline;
- a pre-breach warning (conversation.sla_impending) is emitted before the
  deadline passes, and the breach fires after.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.database import utc_after
from app.main import create_app
from app.webhooks import (
    EVENT_CONVERSATION_SLA_BREACHED,
    EVENT_CONVERSATION_SLA_IMPENDING,
)

ADMIN_KEY = "sla-admin-key-001"


def _public_resolve(_host: str, _port: int) -> list[str]:
    """Offline resolver: treat every hostname as a public unicast address.

    Keeps the webhook URL safety check hermetic — the app-level webhook
    service would otherwise resolve ``example.com`` through the OS resolver,
    which sandboxed CI may map to a reserved/proxy address.
    """
    return ["93.184.216.34"]


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
        "normal_sla_minutes": 120,
        "high_sla_minutes": 15,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class SlaPolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "sla.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.database = self.services.database
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_set_and_list_policy(self) -> None:
        response = self.client.put(
            "/api/admin/sla-policies",
            json={"priority": "high", "resolve_minutes": 5, "first_response_minutes": 2},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["resolve_minutes"], 5)
        listed = self.client.get("/api/admin/sla-policies", headers=self.admin).json()
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["priority"], "high")

    def test_resolution_fallback_chain(self) -> None:
        db = self.database
        # Global default applies.
        self.assertEqual(db.resolve_sla_policy("demo", "normal", "web", 120), 120)
        # Tenant default overrides global.
        db.set_sla_policy(
            tenant_id="demo",
            priority=None,
            channel=None,
            first_response_minutes=10,
            resolve_minutes=30,
        )
        self.assertEqual(db.resolve_sla_policy("demo", "normal", "web", 120), 30)
        # Exact (tenant, priority, channel) wins over tenant default.
        db.set_sla_policy(
            tenant_id="demo",
            priority="high",
            channel="web",
            first_response_minutes=1,
            resolve_minutes=5,
        )
        self.assertEqual(db.resolve_sla_policy("demo", "high", "web", 120), 5)
        self.assertEqual(db.resolve_sla_policy("demo", "high", "email", 120), 30)

    def test_create_conversation_applies_resolve_policy(self) -> None:
        self.database.set_sla_policy(
            tenant_id="demo",
            priority="normal",
            channel="web",
            first_response_minutes=1,
            resolve_minutes=3,
        )
        conv = self.client.post(
            "/api/conversations",
            json={"customer_name": "C", "channel": "web"},
            headers=self.admin,
        ).json()
        # 3-minute deadline: sla_due_at within ~3 min of created_at.
        from datetime import datetime

        created = datetime.fromisoformat(conv["created_at"])
        due = datetime.fromisoformat(conv["sla_due_at"])
        delta_minutes = (due - created).total_seconds() / 60
        self.assertAlmostEqual(delta_minutes, 3, delta=0.5)

    def test_operator_cannot_manage_policies(self) -> None:
        op_key = "sla-op-key-000001"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
            op_key: {"tenant_id": "demo", "actor_id": "op", "role": "operator"},
        }
        db_path = Path(self._tmp.name) / "sla-op.db"
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
            response = client.put(
                "/api/admin/sla-policies",
                json={"priority": "high", "resolve_minutes": 5, "first_response_minutes": 2},
                headers={"X-API-Key": op_key, "X-Tenant-Id": "demo"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            cast(Any, client.app).state.services.database.close()
            client.close()

    def test_cross_tenant_policy_write_is_refused(self) -> None:
        """A tenant admin cannot create a policy for another tenant, even with
        admin:manage (matches _ensure_same_tenant everywhere else)."""
        other_key = "sla-other-000001"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
            other_key: {"tenant_id": "acme", "actor_id": "aadmin", "role": "admin"},
        }
        db_path = Path(self._tmp.name) / "sla-x.db"
        admin_client = TestClient(
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
            other_tenancy = {
                "tenant_id": "acme",
                "priority": "high",
                "resolve_minutes": 1,
                "first_response_minutes": 1,
            }
            response = admin_client.put(
                "/api/admin/sla-policies",
                json=other_tenancy,
                headers={"X-API-Key": other_key, "X-Tenant-Id": "acme"},
            )
            self.assertEqual(response.status_code, 200, response.text)
            # Own-tenant policy is fine, but acme's admin cannot touch demo's.
            forged = dict(other_tenancy, tenant_id="demo")
            response = admin_client.put(
                "/api/admin/sla-policies",
                json=forged,
                headers={"X-API-Key": other_key, "X-Tenant-Id": "acme"},
            )
            self.assertEqual(response.status_code, 403)
        finally:
            cast(Any, admin_client.app).state.services.database.close()
            admin_client.close()

    def test_global_policy_with_priority_resolves(self) -> None:
        """Global (NULL tenant) policies may carry a priority; the lookups must
        probe them before the plain global default."""
        db = self.database
        db.set_sla_policy(
            tenant_id=None,
            priority="high",
            channel=None,
            first_response_minutes=5,
            resolve_minutes=7,
        )
        db.set_sla_policy(
            tenant_id=None,
            priority=None,
            channel=None,
            first_response_minutes=10,
            resolve_minutes=40,
        )
        # Global high-priority tier wins over the global default for any tenant.
        self.assertEqual(db.resolve_sla_policy("demo", "high", "web", 120), 7)
        # Non-high falls through to the global default.
        self.assertEqual(db.resolve_sla_policy("demo", "normal", "web", 120), 40)

    def test_priority_change_applies_policy(self) -> None:
        """Roadmap application point: priority change recomputes the SLA
        deadline via the policy matrix (not hardcoded settings)."""
        self.database.set_sla_policy(
            tenant_id="demo",
            priority="high",
            channel="web",
            first_response_minutes=1,
            resolve_minutes=5,
        )
        settled = self.client.post(
            "/api/conversations",
            json={"customer_name": "C", "channel": "web"},
            headers=self.admin,
        ).json()
        escalated = self.client.patch(
            f"/api/conversations/{settled['id']}",
            json={"priority": "high"},
            headers=self.admin,
        ).json()
        # Escalating to high tightens the deadline toward ~5 minutes.
        from datetime import datetime

        created = datetime.fromisoformat(settled["sla_due_at"])
        due = datetime.fromisoformat(escalated["sla_due_at"])
        self.assertLessEqual(due, created)
        delta_minutes = (due - datetime.fromisoformat(settled["updated_at"])).total_seconds() / 60
        self.assertAlmostEqual(delta_minutes, 5, delta=1.0)

    def test_reopen_applies_policy(self) -> None:
        """Roadmap application point: reopen recomputes the SLA deadline via
        the policy matrix instead of the fixed normal_sla_minutes."""
        self.database.set_sla_policy(
            tenant_id="demo",
            priority="normal",
            channel="web",
            first_response_minutes=1,
            resolve_minutes=3,
        )
        conv = self.client.post(
            "/api/conversations",
            json={"customer_name": "C", "channel": "web"},
            headers=self.admin,
        ).json()
        self.client.post(f"/api/conversations/{conv['id']}/resolve", headers=self.admin)
        reopened = self.client.post(
            f"/api/conversations/{conv['id']}/reopen", headers=self.admin
        ).json()
        from datetime import datetime

        created = datetime.fromisoformat(reopened["updated_at"])
        due = datetime.fromisoformat(reopened["sla_due_at"])
        delta_minutes = (due - created).total_seconds() / 60
        self.assertAlmostEqual(delta_minutes, 3, delta=1.0)


class SlaDialectPortabilityTests(unittest.TestCase):
    """The SLA lookups must stay valid under the PostgreSQL dialect.

    ``IS ?`` is a SQLite-only null-safe-equality extension that the PG dialect
    does not translate, so any nullable matching must use the portable
    ``(col = ? OR (col IS NULL AND ? IS NULL))`` form. Guard the source so the
    regression cannot be reintroduced (the PG integration suite only runs when
    HELIX_PG_INTEGRATION=1, so it would miss this otherwise).
    """

    def test_no_sqlite_only_null_safe_equality_in_db(self) -> None:
        """No ``<column> IS ?`` anywhere in the database layer.

        The PostgreSQL dialect does not translate SQLite's ``IS ?``
        null-safe-equality extension, so PostgreSQL conversation intake would
        fail at runtime (invisible to the SQLite-only default test run).
        """
        import re

        db_dir = Path(__file__).resolve().parents[1] / "app" / "db"
        pattern = re.compile(r"\w+ IS \?")
        for path in sorted(db_dir.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            match = pattern.search(source)
            self.assertIsNone(
                match,
                f"{path.name} uses {match.group() if match else ''}; "
                "must use portable (col = ? OR (col IS NULL AND ? IS NULL)) matching",
            )


class SlaImpendingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "impend.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.database = self.services.database
        self.webhooks = self.services.webhooks
        self.webhooks._resolve_host = _public_resolve
        self.webhooks.register_endpoint(
            "demo",
            "https://example.com/hook",
            [EVENT_CONVERSATION_SLA_IMPENDING, EVENT_CONVERSATION_SLA_BREACHED],
            "secret-123",
        )

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_impending_then_breach(self) -> None:
        conv = self.database.create_conversation("demo", "C", None, "web", "admin", 120)
        # Due in 3 minutes -> within the 5-minute warning window.
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE conversations SET sla_due_at = ? WHERE id = ?",
                (utc_after(3), conv["id"]),
            )
            conn.commit()
        self.assertEqual(self.webhooks.check_sla_impending(window_minutes=5), 1)
        # Idempotent within the same window.
        self.assertEqual(self.webhooks.check_sla_impending(window_minutes=5), 0)
        deliveries = self.webhooks.list_deliveries("demo")
        self.assertEqual(deliveries[0]["event_type"], EVENT_CONVERSATION_SLA_IMPENDING)
        # Past due -> breach fires after.
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE conversations SET sla_due_at = '2020-01-01T00:00:00+00:00' WHERE id = ?",
                (conv["id"],),
            )
            conn.commit()
        self.assertEqual(self.webhooks.check_sla_breaches(), 1)
        events = [d["event_type"] for d in self.webhooks.list_deliveries("demo")]
        self.assertIn(EVENT_CONVERSATION_SLA_BREACHED, events)


if __name__ == "__main__":
    unittest.main()
