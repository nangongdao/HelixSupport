"""End-to-end SDK tests against a real in-process Helix app (Phase 25.4).

Bridges httpx to FastAPI's TestClient so the SDK exercises the real HTTP
stack (auth headers, routing, Problem Details errors, tenant scoping)
without a live server.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

import httpx

# Make the repo root importable so `app.main` resolves.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))

from helix_client import HelixClient, HelixNotFoundError

ADMIN_KEY = "sdk-e2e-key-000001"
TENANT_ADMIN_KEY = "sdk-tenant-admin-0001"


class _TestClientTransport(httpx.BaseTransport):
    """Forward sync httpx requests to FastAPI's TestClient."""

    def __init__(self, test_client: Any) -> None:
        self._tc = test_client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self._tc.request(
            request.method,
            str(request.url),
            headers=dict(request.headers),
            content=request.content if request.content else None,
        )
        return httpx.Response(
            response.status_code,
            headers=dict(response.headers),
            content=response.content,
        )


class SdkEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "sdk.db"
        principals = {
            ADMIN_KEY: {"tenant_id": "demo", "actor_id": "sdk.user", "role": "admin"},
            TENANT_ADMIN_KEY: {
                "tenant_id": "sdkco",
                "actor_id": "sdkco.admin",
                "role": "admin",
            },
        }
        from app.config import Settings
        from app.main import create_app

        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        self.app = create_app(settings)
        self.settings = settings
        self._tc = __import__("fastapi.testclient", fromlist=["TestClient"]).TestClient(self.app)
        self.client = HelixClient(
            base_url="http://testserver",
            api_key=ADMIN_KEY,
            tenant_id="demo",
            transport=_TestClientTransport(self._tc),
        )

    def tearDown(self) -> None:
        self.client.close()
        self.app.state.services.database.close()
        self._tmp.cleanup()

    def test_full_conversation_flow(self) -> None:
        me = self.client.me()
        self.assertEqual(me["role"], "admin")
        conv = self.client.create_conversation("SDK User", customer_ref="CUST-1001")
        self.assertEqual(conv["status"], "open")
        turn = self.client.send_message(conv["id"], "ORD-10482 到哪了", idempotency_key="sdk-e2e-1")
        self.assertEqual(turn["conversation"]["status"], "open")
        metadata = turn["assistant_message"]["metadata"]
        self.assertEqual(metadata["agent"], "order")
        # Feedback round-trips through the API.
        feedback = self.client.submit_feedback(conv["id"], turn["assistant_message"]["id"], -1)
        self.assertEqual(feedback["rating"], -1)

    def test_not_found_maps_to_problem_details(self) -> None:
        with self.assertRaises(HelixNotFoundError) as ctx:
            self.client.get_conversation("conv-nope")
        self.assertEqual(ctx.exception.code, "not_found")
        self.assertIsNotNone(ctx.exception.request_id)
        self.assertEqual(ctx.exception.instance, "/api/conversations/conv-nope")

    def test_golden_order_case_through_sdk(self) -> None:
        """The order-no-number golden scenario escalates via the SDK."""
        conv = self.client.create_conversation("Golden", customer_ref="CUST-1001")
        turn = self.client.send_message(
            conv["id"], "帮我查一下订单", idempotency_key="sdk-golden-1"
        )
        self.assertEqual(turn["conversation"]["status"], "waiting_human")

    def test_tenant_provision_and_member_lifecycle_through_sdk(self) -> None:
        """Phase 22: provision, invite, role change via the SDK."""
        quota = self.client.provision_tenant("sdkco", "SDK Co", conversation_quota=50)
        self.assertEqual(quota["tenant_id"], "sdkco")
        self.assertEqual(quota["conversation_quota"], 50)
        # Tenant admins are intentionally scoped to their own tenant. Use the
        # newly provisioned tenant's credential for its member lifecycle.
        with HelixClient(
            base_url="http://testserver",
            api_key=TENANT_ADMIN_KEY,
            tenant_id="sdkco",
            transport=_TestClientTransport(self._tc),
        ) as tenant_client:
            member = tenant_client.invite_member("sdkco", "alice.user", "operator")
            self.assertEqual(member["status"], "invited")
            updated = tenant_client.update_member_role("sdkco", "alice.user", "supervisor")
            self.assertEqual(updated["role"], "supervisor")
            members = tenant_client.list_members("sdkco")
            self.assertEqual(len(members), 1)
            deactivated = tenant_client.deactivate_member("sdkco", "alice.user")
            self.assertEqual(deactivated["status"], "deactivated")

    def test_usage_export_through_sdk(self) -> None:
        conv = self.client.create_conversation("Usage")
        self.client.send_message(conv["id"], "ORD-10482 到哪了", idempotency_key="sdk-usage-1")
        rows = self.client.export_tenant_usage()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["conversation_count"], 1)
        self.assertGreaterEqual(rows[0]["message_count"], 2)

    def test_widget_chat_and_channel_idempotency_through_sdk(self) -> None:
        """Phase 23: widget session + channel_message_id replay dedup."""
        from app.widget_token import sign_token

        token = sign_token(
            secret=self.settings.widget_secret, tenant_id="demo", customer_ref="CUST-1"
        )
        session = self.client.widget_create_session(widget_token=token, customer_name="Widget User")
        conversation_id = session["conversation"]["id"]
        session_token = session["widget_token"]
        self.assertEqual(session["conversation"]["channel"], "web_chat")
        first = self.client.widget_send_message(
            widget_token=session_token,
            conversation_id=conversation_id,
            content="ORD-10482 到哪了",
            channel_message_id="sdk-ch-1",
        )
        self.assertFalse(first["idempotent_replay"])
        replay = self.client.widget_send_message(
            widget_token=session_token,
            conversation_id=conversation_id,
            content="ORD-10482 到哪了",
            channel_message_id="sdk-ch-1",
        )
        self.assertTrue(replay["idempotent_replay"])

    # ------------------------------------------------- v1/v2 matrix (43.3)

    SHADOW_READ_FIELDS = (
        "id",
        "status",
        "priority",
        "channel",
        "customer_name",
        "customer_ref",
        "created_at",
        "updated_at",
    )

    def test_cross_version_shadow_read_fields_are_identical(self) -> None:
        """Same resource through v1 and v2: core fields byte-identical."""
        created = self.client.create_conversation("Matrix User", customer_ref="CUST-MATRIX")
        conversation_id = created["id"]
        v1_view = self.client.get_conversation(conversation_id)
        if "conversation" in v1_view:  # detail endpoint nests the resource
            v1_view = v1_view["conversation"]
        v2_view = self.client.get_conversation_v2(conversation_id)
        for field in self.SHADOW_READ_FIELDS:
            self.assertEqual(v1_view[field], v2_view[field], field)
        # The listing agrees with the single-resource reads, both versions.
        v1_row = next(
            row for row in self.client.list_conversations() if row["id"] == conversation_id
        )
        page = self.client.list_conversations_v2(limit=200)
        v2_rows = [row for row in page.data if row["id"] == conversation_id]
        self.assertEqual(len(v2_rows), 1)
        for field in self.SHADOW_READ_FIELDS:
            self.assertEqual(v1_row[field], v2_rows[0][field], field)
        self.assertEqual(page.api_version, "2.0")

    def test_v2_cursor_pagination_is_complete_and_stable(self) -> None:
        """Seven conversations at limit=3 walk every item exactly once."""
        for i in range(7):
            self.client.create_conversation(f"Pager {i}")
        seen = [row["id"] for row in self.client.iter_conversations_v2(limit=3)]
        self.assertEqual(len(seen), len(set(seen)), "cursor pages must not repeat")
        # Every v1-visible conversation appears in the v2 iteration too.
        v1_ids = {row["id"] for row in self.client.list_conversations()}
        self.assertEqual(set(seen), v1_ids)

    def test_v2_create_replay_returns_original_resource(self) -> None:
        """Idempotency-Key replay: same id, X-Idempotent-Replay flagged."""
        first = self.client.create_conversation_v2("Idem User", idempotency_key="sdk-v2-idem-1")
        self.assertFalse(first["_idempotent_replay"])
        second = self.client.create_conversation_v2("Idem User", idempotency_key="sdk-v2-idem-1")
        self.assertTrue(second["_idempotent_replay"])
        self.assertEqual(first["id"], second["id"])
        # Exactly one conversation was created for the key.
        matches = [
            row for row in self.client.iter_conversations_v2(limit=200) if row["id"] == first["id"]
        ]
        self.assertEqual(len(matches), 1)

    def test_v2_messages_keyset_covers_full_history(self) -> None:
        """Message cursor pagination (created_at + seq) never skips a turn."""
        conv = self.client.create_conversation("History User")
        turns = [
            self.client.send_message(conv["id"], f"问 {i}", idempotency_key=f"sdk-hist-{i}")
            for i in range(3)
        ]
        # Once the first turn escalates (waiting_human), later turns are
        # suppressed by design (41.6) — only the first gets an assistant
        # reply, so the transcript is 3 customer + 1 assistant messages.
        collected: list[dict] = []
        cursor = None
        while True:
            page = self.client.list_messages_v2(conv["id"], cursor=cursor, limit=2)
            collected.extend(page.data)
            cursor = page.next_cursor
            if not cursor:
                break
        roles = [m["role"] for m in collected]
        self.assertEqual(roles.count("customer"), 3)
        self.assertGreaterEqual(roles.count("assistant"), 1)
        first_reply = turns[0]["assistant_message"]
        self.assertIn(
            first_reply["id"], [m["id"] for m in collected], "first turn reply must appear"
        )


if __name__ == "__main__":
    unittest.main()
