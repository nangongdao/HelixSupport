"""Phase 43.3: /api/v2 contract — cursors, idempotency, shadow reads.

Consumer-driven contract pins:
- cursor envelopes paginate stably and never skip or repeat rows;
- Idempotency-Key replays return the original resource, not a duplicate;
- core resource fields are byte-identical between v1 and v2 (shadow read —
  no silent semantic change);
- every v2 response carries X-API-Version and errors stay Problem Details.
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


class ApiV2ContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        settings = Settings(
            database_path=Path(self._tmp.name) / "v2.db",
            turn_worker_enabled=False,
            rate_limit_per_minute=10_000,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    "admin-key-0123456789": {
                        "tenant_id": "demo",
                        "actor_id": "admin",
                        "role": "admin",
                    }
                }
            ),
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services
        self.headers = {"X-API-Key": "admin-key-0123456789", "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    # ------------------------------------------------------------ helpers

    def _create(self, name: str, idempotency_key: str | None = None) -> Any:
        headers = dict(self.headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self.client.post(
            "/api/v2/conversations",
            json={"customer_name": name, "channel": "web"},
            headers=headers,
        )

    # ------------------------------------------------------- version header

    def test_every_v2_response_carries_version_header(self) -> None:
        listed = self.client.get("/api/v2/conversations", headers=self.headers)
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.headers["X-API-Version"], "2.0")
        missing = self.client.get("/api/v2/conversations/conv_missing", headers=self.headers)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(missing.headers["X-API-Version"], "2.0")

    def test_errors_stay_problem_details(self) -> None:
        missing = self.client.get("/api/v2/conversations/conv_x", headers=self.headers)
        body = missing.json()
        self.assertEqual(body["type"], "urn:helix:error:not_found")
        self.assertIn("status", body)

    # ------------------------------------------------------ cursor envelope

    def test_cursor_pagination_is_stable_and_complete(self) -> None:
        names = [f"Cursor Customer {index:02d}" for index in range(7)]
        for name in names:
            response = self._create(name)
            self.assertEqual(response.status_code, 201, response.text)

        seen: list[str] = []
        cursor: str | None = None
        pages = 0
        while True:
            params = {"limit": "3"}
            if cursor:
                params["cursor"] = cursor
            page = self.client.get("/api/v2/conversations", params=params, headers=self.headers)
            self.assertEqual(page.status_code, 200)
            body = page.json()
            seen.extend(item["customer_name"] for item in body["data"])
            pages += 1
            cursor = body["next_cursor"]
            if not cursor:
                break
        self.assertEqual(pages, 3)
        self.assertEqual(sorted(seen), sorted(names), "pagination must lose nothing")

    def test_invalid_cursor_is_a_clean_400(self) -> None:
        response = self.client.get(
            "/api/v2/conversations",
            params={"cursor": "!!!not-a-cursor!!!"},
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 400)

    # -------------------------------------------------- idempotent create

    def test_idempotency_key_replay_returns_original_resource(self) -> None:
        first = self._create("Idempotent Customer", idempotency_key="idem-001")
        self.assertEqual(first.status_code, 201)
        second = self._create("Idempotent Customer", idempotency_key="idem-001")
        self.assertEqual(second.status_code, 201)
        self.assertTrue(second.headers.get("X-Idempotent-Replay") == "true")
        self.assertEqual(first.json()["id"], second.json()["id"])

        # A different key creates a distinct resource.
        third = self._create("Idempotent Customer", idempotency_key="idem-002")
        self.assertNotEqual(first.json()["id"], third.json()["id"])
        self.assertNotIn("X-Idempotent-Replay", third.headers)

    # ------------------------------------------- shadow read (v1 vs v2)

    def test_shadow_read_core_fields_match_v1_exactly(self) -> None:
        created = self._create("Shadow Customer")
        conversation_id = created.json()["id"]
        v2_row = created.json()
        v1_row = self.client.get(
            f"/api/conversations/{conversation_id}", headers=self.headers
        ).json()
        if isinstance(v1_row, dict) and "conversation" in v1_row:
            v1_row = v1_row["conversation"]
        for field in ("id", "status", "priority", "channel", "customer_name", "created_at"):
            self.assertEqual(
                v2_row[field],
                v1_row[field],
                f"shadow-read drift on {field} — a silent semantic change",
            )

    # ------------------------------------- transactional outbox integration

    def test_create_records_domain_event_exactly_once(self) -> None:
        from app.event_outbox import DomainEventOutbox

        created = self._create("Outbox Customer", idempotency_key="idem-outbox")
        self.assertEqual(created.status_code, 201)
        conversation_id = created.json()["id"]

        outbox = DomainEventOutbox(self.services.database)
        delivered: list[dict[str, Any]] = []
        self.assertGreaterEqual(outbox.drain(delivered.append), 1)
        # Replaying the drain must not re-deliver (consumer dedup by event_id).
        self.assertEqual(outbox.drain(delivered.append), 0)
        matches = [
            event for event in delivered if event["payload"]["conversation_id"] == conversation_id
        ]
        self.assertEqual(len(matches), 1)
        event = matches[0]
        self.assertEqual(event["event_type"], "helix.conversation.created")
        self.assertEqual(event["schema_version"], 1)
        self.assertEqual(event["payload"]["source_api"], "v2")


if __name__ == "__main__":
    unittest.main()
