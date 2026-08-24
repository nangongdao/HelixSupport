"""Phase 22: tenant self-service, member lifecycle, and metering.

Covers the ROADMAP_1_X section 8 acceptance criteria:
- 22.1 tenant provisioning is idempotent and seeds baseline knowledge so the
  new tenant is immediately serviceable;
- 22.2 member invite/role-change/deactivate with audit retention;
- 22.3 permission matrix is exhaustive (every role x every admin endpoint)
  and the auditor role is read-only;
- 22.4 usage metering reconciles with the audit trail and quota enforcement
  returns 429 when the conversation quota is exhausted.
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
from app.security import ROLE_PERMISSIONS, Role

ADMIN_KEY = "p22-admin-key-00001"
OPERATOR_KEY = "p22-op-key-0000001"
AUDITOR_KEY = "p22-auditor-key-001"
VIEWER_KEY = "p22-viewer-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"},
        AUDITOR_KEY: {"tenant_id": "demo", "actor_id": "aud.user", "role": "auditor"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "view.user", "role": "viewer"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class TenantProvisioningTests(unittest.TestCase):
    """22.1: provisioning is idempotent and seeds baseline knowledge."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p22.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _provision(self, tenant_id: str = "acme") -> dict:
        response = self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": tenant_id, "name": "Acme Corp", "conversation_quota": 100},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_provision_creates_tenant_with_quota(self) -> None:
        body = self._provision()
        self.assertEqual(body["tenant_id"], "acme")
        self.assertEqual(body["conversation_quota"], 100)
        self.assertIsNone(body["daily_turn_budget"])

    def test_provision_is_idempotent(self) -> None:
        self._provision()
        again = self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "acme", "name": "Acme Corp Again"},
            headers=self.admin,
        )
        self.assertEqual(again.status_code, 201)
        self.assertEqual(again.json()["tenant_id"], "acme")

    def test_provision_seeds_knowledge_for_immediate_service(self) -> None:
        self._provision("newco")
        knowledge = self.services.database.search_knowledge("newco", "配送")
        self.assertGreaterEqual(len(knowledge), 1)
        # Articles are keyed per tenant.
        ids = [article["id"] for article in knowledge]
        self.assertTrue(all("newco" in article_id for article_id in ids))

    def test_operator_cannot_provision(self) -> None:
        op = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}
        response = self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "nope", "name": "Nope"},
            headers=op,
        )
        self.assertEqual(response.status_code, 403)

    def test_invalid_tenant_id_rejected(self) -> None:
        response = self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "BAD ID!", "name": "Nope"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 422)


class MemberLifecycleTests(unittest.TestCase):
    """22.2: invite, role change, deactivate with audit retention."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p22m.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        # Phase 32.1 audit: member/quota routes are tenant-scoped. The admin
        # principal belongs to ``demo`` so we exercise those routes under the
        # same tenant rather than the freshly provisioned ``acme`` tenant.
        self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "demo", "name": "Demo"},
            headers=self.admin,
        )

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_invite_list_role_change_deactivate(self) -> None:
        invite = self.client.post(
            "/api/admin/tenants/demo/members",
            json={"actor_id": "bob.user", "role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(invite.status_code, 201)
        self.assertEqual(invite.json()["status"], "invited")
        self.assertEqual(invite.json()["role"], "operator")

        members = self.client.get("/api/admin/tenants/demo/members", headers=self.admin)
        self.assertEqual(len(members.json()), 1)

        role = self.client.patch(
            "/api/admin/tenants/demo/members/bob.user",
            json={"role": "supervisor"},
            headers=self.admin,
        )
        self.assertEqual(role.status_code, 200)
        self.assertEqual(role.json()["role"], "supervisor")
        self.assertEqual(role.json()["status"], "active")

        deactivated = self.client.post(
            "/api/admin/tenants/demo/members/bob.user/deactivate",
            headers=self.admin,
        )
        self.assertEqual(deactivated.status_code, 200)
        self.assertEqual(deactivated.json()["status"], "deactivated")

    def test_invite_is_idempotent_per_member(self) -> None:
        for _ in range(2):
            response = self.client.post(
                "/api/admin/tenants/demo/members",
                json={"actor_id": "carol.user", "role": "viewer"},
                headers=self.admin,
            )
            self.assertEqual(response.status_code, 201)
        members = self.client.get("/api/admin/tenants/demo/members", headers=self.admin)
        self.assertEqual(len(members.json()), 1)

    def test_member_changes_are_audited(self) -> None:
        self.client.post(
            "/api/admin/tenants/demo/members",
            json={"actor_id": "dave.user", "role": "operator"},
            headers=self.admin,
        )
        self.client.patch(
            "/api/admin/tenants/demo/members/dave.user",
            json={"role": "admin"},
            headers=self.admin,
        )
        with self.services.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type FROM audit_events WHERE tenant_id='demo' ORDER BY created_at"
            ).fetchall()
        types = [row["event_type"] for row in rows]
        self.assertIn("tenant.provisioned", types)
        self.assertIn("member.invited", types)
        self.assertIn("member.role_changed", types)

    def test_unknown_member_role_change_404(self) -> None:
        response = self.client.patch(
            "/api/admin/tenants/demo/members/nobody",
            json={"role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404)

    def test_operator_cannot_manage_members(self) -> None:
        op = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}
        response = self.client.get("/api/admin/tenants/demo/members", headers=op)
        self.assertEqual(response.status_code, 403)

    # Phase 32.2: cross-tenant isolation and self-protection guards.

    def test_admin_cannot_manage_other_tenant_members(self) -> None:
        """A tenant admin must not invite/list/mutate members of a foreign tenant."""
        # Provision a second tenant owned by the same admin, then attempt to
        # manipulate its members from the demo admin context.  Every path is
        # rejected with 403 before touching the database.
        self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "other", "name": "Other"},
            headers=self.admin,
        )
        invite = self.client.post(
            "/api/admin/tenants/other/members",
            json={"actor_id": "eve.user", "role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(invite.status_code, 403)
        listing = self.client.get("/api/admin/tenants/other/members", headers=self.admin)
        self.assertEqual(listing.status_code, 403)
        patch = self.client.patch(
            "/api/admin/tenants/other/members/nobody",
            json={"role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(patch.status_code, 403)
        deactivate = self.client.post(
            "/api/admin/tenants/other/members/nobody/deactivate",
            headers=self.admin,
        )
        self.assertEqual(deactivate.status_code, 403)

    def test_admin_cannot_manage_other_tenant_quota(self) -> None:
        """Quota read/write on a foreign tenant is rejected with 403."""
        self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "other", "name": "Other"},
            headers=self.admin,
        )
        read = self.client.get("/api/admin/tenants/other/quota", headers=self.admin)
        self.assertEqual(read.status_code, 403)
        write = self.client.put(
            "/api/admin/tenants/other/quota",
            json={"conversation_quota": 50},
            headers=self.admin,
        )
        self.assertEqual(write.status_code, 403)

    def test_admin_cannot_demote_self(self) -> None:
        """Self-demotion is rejected with 409 to keep at least one admin."""
        demote = self.client.patch(
            "/api/admin/tenants/demo/members/admin.user",
            json={"role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(demote.status_code, 409)
        self.assertIn("admin", demote.json()["detail"].lower())

    def test_admin_cannot_deactivate_self(self) -> None:
        """Self-deactivation is rejected with 409 to avoid tenant lock-out."""
        deactivate = self.client.post(
            "/api/admin/tenants/demo/members/admin.user/deactivate",
            headers=self.admin,
        )
        self.assertEqual(deactivate.status_code, 409)

    def test_cannot_demote_last_admin_cross_member(self) -> None:
        """W1: a credential admin outside the roster cannot demote the tenant's
        final active admin member — the tenant still needs one admin."""
        self.client.post(
            "/api/admin/tenants/demo/members",
            json={"actor_id": "sole.admin", "role": "admin"},
            headers=self.admin,
        )
        # The sole rostered admin starts ``invited``; activate it via a role
        # change (``update_member_role`` flips status to ``active``).
        activate = self.client.patch(
            "/api/admin/tenants/demo/members/sole.admin",
            json={"role": "admin"},
            headers=self.admin,
        )
        self.assertEqual(activate.status_code, 200)
        self.assertEqual(activate.json()["status"], "active")

        demote = self.client.patch(
            "/api/admin/tenants/demo/members/sole.admin",
            json={"role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(demote.status_code, 409)
        self.assertIn("admin", demote.json()["detail"].lower())
        # The roster still has an active admin.
        members = self.client.get("/api/admin/tenants/demo/members", headers=self.admin)
        after = {m["actor_id"]: m for m in members.json()}
        self.assertEqual(after["sole.admin"]["role"], "admin")
        self.assertEqual(after["sole.admin"]["status"], "active")

    def test_cannot_deactivate_last_admin_cross_member(self) -> None:
        """W1: deactivating the tenant's final active admin is refused even when
        the operator is a credential admin outside the roster."""
        self.client.post(
            "/api/admin/tenants/demo/members",
            json={"actor_id": "sole.admin", "role": "admin"},
            headers=self.admin,
        )
        activate = self.client.patch(
            "/api/admin/tenants/demo/members/sole.admin",
            json={"role": "admin"},
            headers=self.admin,
        )
        self.assertEqual(activate.status_code, 200)

        deactivate = self.client.post(
            "/api/admin/tenants/demo/members/sole.admin/deactivate",
            headers=self.admin,
        )
        self.assertEqual(deactivate.status_code, 409)
        members = self.client.get("/api/admin/tenants/demo/members", headers=self.admin)
        after = {m["actor_id"]: m for m in members.json()}
        self.assertEqual(after["sole.admin"]["status"], "active")

    def test_can_demote_last_admin_when_another_admin_remains(self) -> None:
        """W1: with a second active admin remaining, demoting one member admin
        is still allowed — the tenant keeps its last admin."""
        for member in ("keeper.admin", "leaver.admin"):
            self.client.post(
                "/api/admin/tenants/demo/members",
                json={"actor_id": member, "role": "admin"},
                headers=self.admin,
            )
            activate = self.client.patch(
                f"/api/admin/tenants/demo/members/{member}",
                json={"role": "admin"},
                headers=self.admin,
            )
            self.assertEqual(activate.status_code, 200)
            self.assertEqual(activate.json()["status"], "active")

        demote = self.client.patch(
            "/api/admin/tenants/demo/members/leaver.admin",
            json={"role": "operator"},
            headers=self.admin,
        )
        self.assertEqual(demote.status_code, 200)
        self.assertEqual(demote.json()["role"], "operator")

    def test_quota_update_rejects_invalid_values(self) -> None:
        """Phase 32.2: the strict quota schema rejects out-of-range values."""
        too_low = self.client.put(
            "/api/admin/tenants/demo/quota",
            json={"conversation_quota": 0},
            headers=self.admin,
        )
        self.assertEqual(too_low.status_code, 422)
        too_high = self.client.put(
            "/api/admin/tenants/demo/quota",
            json={"storage_quota_bytes": 10**16},
            headers=self.admin,
        )
        self.assertEqual(too_high.status_code, 422)
        # Unknown fields are rejected (StrictModel extra=forbid).
        unknown = self.client.put(
            "/api/admin/tenants/demo/quota",
            json={"daily_turn_budget": 100},
            headers=self.admin,
        )
        self.assertEqual(unknown.status_code, 422)


class UsageMeteringTests(unittest.TestCase):
    """22.4: usage export reconciles with activity; quota returns 429."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p22u.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_usage_tracks_conversations_turns_and_messages(self) -> None:
        for index in range(2):
            conv = self.client.post(
                "/api/conversations", json={"customer_name": f"C{index}"}, headers=self.admin
            ).json()
            self.client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "ORD-10482 到哪了"},
                headers={**self.admin, "Idempotency-Key": f"p22u-{index}-abcdefg"},
            )
        usage = self.client.get("/api/admin/usage", headers=self.admin).json()
        self.assertEqual(len(usage), 1)
        row = usage[0]
        self.assertEqual(row["conversation_count"], 2)
        self.assertEqual(row["turn_count"], 2)
        self.assertEqual(row["message_count"], 4)

    def test_usage_export_defaults_to_caller_tenant(self) -> None:
        # Operator lacks tenant:manage so the export is forbidden for them.
        op = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}
        response = self.client.get("/api/admin/usage", headers=op)
        self.assertEqual(response.status_code, 403)

    def test_conversation_quota_returns_429(self) -> None:
        self.client.put(
            "/api/admin/tenants/demo/quota",
            json={"conversation_quota": 1},
            headers=self.admin,
        )
        first = self.client.post(
            "/api/conversations", json={"customer_name": "C1"}, headers=self.admin
        )
        self.assertEqual(first.status_code, 201)
        blocked = self.client.post(
            "/api/conversations", json={"customer_name": "C2"}, headers=self.admin
        )
        self.assertEqual(blocked.status_code, 429)
        body = blocked.json()
        self.assertEqual(body["code"], "rate_limited")
        # Resolving frees a slot.
        conversation_id = first.json()["id"]
        self.client.post(f"/api/conversations/{conversation_id}/resolve", headers=self.admin)
        allowed = self.client.post(
            "/api/conversations", json={"customer_name": "C3"}, headers=self.admin
        )
        self.assertEqual(allowed.status_code, 201)


class PermissionMatrixTests(unittest.TestCase):
    """22.3: exhaustive role x endpoint matrix for the admin surface.

    Every role is tried against every Phase 22 admin endpoint; the outcome
    must match ROLE_PERMISSIONS. This pins the permission model so a role
    that silently gains (or loses) access to an admin endpoint fails the
    gate.
    """

    ROLE_KEYS: dict[str, str] = {
        "admin": ADMIN_KEY,
        "operator": OPERATOR_KEY,
        "auditor": AUDITOR_KEY,
        "viewer": VIEWER_KEY,
    }

    # endpoint -> (method, needs_tenant) where needs_tenant triggers the
    # cross-tenant path (only admin:manage/tenant:manage roles may pass).
    ADMIN_ENDPOINTS: dict[tuple[str, str], bool] = {
        ("POST", "/api/admin/tenants"): False,
        ("GET", "/api/admin/tenants/demo/quota"): False,
        ("PUT", "/api/admin/tenants/demo/quota"): False,
        ("POST", "/api/admin/tenants/demo/members"): False,
        ("GET", "/api/admin/tenants/demo/members"): False,
        ("PATCH", "/api/admin/tenants/demo/members/bob.user"): False,
        ("POST", "/api/admin/tenants/demo/members/bob.user/deactivate"): False,
        ("GET", "/api/admin/usage"): False,
    }

    # M0 SEC-002: DSR endpoints must be ADMIN-only. Adding them here means
    # operator/auditor/viewer gain of privacy:* fails the gate alongside the
    # admin surface (see test_privacy_endpoints_are_admin_only).
    DSR_ENDPOINTS: tuple[str, ...] = (
        "/api/data-subject-requests",
        "/api/data-subject-requests/x/approve",
        "/api/data-subject-requests/x/execute",
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p22p.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        # Provision the target tenant and one member so PATCH/deactivate work.
        admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.client.post(
            "/api/admin/tenants",
            json={"tenant_id": "demo", "name": "Demo"},
            headers=admin,
        )
        self.client.post(
            "/api/admin/tenants/demo/members",
            json={"actor_id": "bob.user", "role": "operator"},
            headers=admin,
        )

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_admin_permission_matrix(self) -> None:
        for role, key in self.ROLE_KEYS.items():
            headers = {"X-API-Key": key, "X-Tenant-Id": "demo"}
            expected = ROLE_PERMISSIONS[Role(role)]
            for (method, path), _cross in self.ADMIN_ENDPOINTS.items():
                with self.subTest(role=role, method=method, path=path):
                    response = self.client.request(method, path, headers=headers, json={})
                    allowed = response.status_code != 403
                    self.assertEqual(
                        allowed,
                        "tenant:manage" in expected,
                        f"{role} {method} {path} -> {response.status_code}",
                    )

    def test_auditor_is_read_only(self) -> None:
        """Auditor sees metrics/audit but cannot act on anything."""
        auditor = {"X-API-Key": AUDITOR_KEY, "X-Tenant-Id": "demo"}
        # Can read the dashboard and metrics.
        self.assertEqual(self.client.get("/api/dashboard", headers=auditor).status_code, 200)
        # Cannot write a conversation or call admin management.
        self.assertEqual(
            self.client.post(
                "/api/conversations",
                json={"customer_name": "X"},
                headers=auditor,
            ).status_code,
            403,
        )
        self.assertEqual(
            self.client.get("/api/admin/tenants/demo/quota", headers=auditor).status_code,
            403,
        )

    def test_audit_read_permission_grants_audit_export(self) -> None:
        auditor = {"X-API-Key": AUDITOR_KEY, "X-Tenant-Id": "demo"}
        response = self.client.get("/api/audit-events", headers=auditor)
        self.assertEqual(response.status_code, 200)

    def test_privacy_endpoints_are_admin_only(self) -> None:
        """M0 SEC-002: DSR endpoints stay ADMIN-only for every role."""
        for role, key in self.ROLE_KEYS.items():
            headers = {"X-API-Key": key, "X-Tenant-Id": "demo"}
            for path in self.DSR_ENDPOINTS:
                with self.subTest(role=role, path=path):
                    response = self.client.post(path, headers=headers, json={})
                    # Admin may get 400/404 (payload/tenant validation); the
                    # three non-admin roles must always be refused with 403.
                    if role == "admin":
                        self.assertNotEqual(response.status_code, 403, path)
                    else:
                        self.assertEqual(response.status_code, 403, f"{role} {path}")


if __name__ == "__main__":
    unittest.main()
