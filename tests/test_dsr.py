"""M0 SEC-002: data-subject request maker-checker workflow and exports.

Covers the ROADMAP_2_X Phase 40 acceptance contract:
- create -> approve -> execute state machine over request ids only;
- maker-checker separation for deletions (requester/approver/executor are
  three distinct people);
- idempotent replay of completed requests and of ``idempotency_key``;
- encrypted-at-rest exports with one-time 15-minute download tokens and
  a 24-hour object expiry, all failing closed without ``DSR_EXPORT_SECRET``;
- audit events carry request ids and counts, never the customer reference;
- privacy permissions are ADMIN-only; operator/auditor/viewer are denied.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.dsr import DsrExportStore
from app.main import create_app
from app.security import ROLE_PERMISSIONS, Role

ADMIN_A_KEY = "p40-admin-a-00001"
ADMIN_B_KEY = "p40-admin-b-00002"
ADMIN_C_KEY = "p40-admin-c-00003"
OPERATOR_KEY = "p40-op-key-0000001"
AUDITOR_KEY = "p40-auditor-key-001"
VIEWER_KEY = "p40-viewer-key-001"
TENANT2_ADMIN_KEY = "p40-admin-t2-00001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_A_KEY: {"tenant_id": "demo", "actor_id": "admin.a", "role": "admin"},
        ADMIN_B_KEY: {"tenant_id": "demo", "actor_id": "admin.b", "role": "admin"},
        ADMIN_C_KEY: {"tenant_id": "demo", "actor_id": "admin.c", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"},
        AUDITOR_KEY: {"tenant_id": "demo", "actor_id": "aud.user", "role": "auditor"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "view.user", "role": "viewer"},
        TENANT2_ADMIN_KEY: {"tenant_id": "acme", "actor_id": "admin.t2", "role": "admin"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        dsr_export_secret=os.environ.get("DSR_EXPORT_SECRET", "p40-test-secret"),
    )


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key, "X-Tenant-Id": "demo"}


class DsrWorkflowTests(unittest.TestCase):
    """SEC-002 acceptance: state machine, permissions, maker-checker."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p40.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self._seed_conversation("CUST-1")

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _seed_conversation(self, customer_ref: str) -> str:
        response = self.client.post(
            "/api/conversations",
            json={"customer_name": "Customer", "customer_ref": customer_ref},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(response.status_code, 201, response.text)
        conversation_id = response.json()["id"]
        sent = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Hello from the customer"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(sent.status_code, 200, sent.text)
        return conversation_id

    def _create(
        self,
        customer_ref: str = "CUST-1",
        request_type: str = "deletion",
        idempotency_key: str | None = None,
        key: str = ADMIN_A_KEY,
    ) -> Any:
        payload: dict[str, Any] = {
            "customer_ref": customer_ref,
            "request_type": request_type,
        }
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return self.client.post("/api/data-subject-requests", json=payload, headers=_headers(key))

    def _request_id(self, response: Any) -> str:
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def test_permissions_are_admin_only(self) -> None:
        for role, key in (
            ("operator", OPERATOR_KEY),
            ("auditor", AUDITOR_KEY),
            ("viewer", VIEWER_KEY),
        ):
            expected = ROLE_PERMISSIONS[Role(role)]
            with self.subTest(role=role):
                self.assertNotIn("privacy:request", expected)
                self.assertNotIn("privacy:approve", expected)
                self.assertNotIn("privacy:execute", expected)
                denied = self._create(key=key)
                self.assertEqual(denied.status_code, 403, role)

    def test_create_approve_execute_deletion_full_flow(self) -> None:
        request_id = self._request_id(self._create())
        approved = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(approved.json()["status"], "approved")
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        body = executed.json()
        self.assertEqual(body["status"], "completed")
        self.assertFalse(body["idempotent_replay"])
        self.assertGreaterEqual(body["summary"]["deleted"]["conversations"], 1)
        self.assertGreaterEqual(body["summary"]["deleted"]["messages"], 1)

    def test_execute_replays_completed_request_idempotently(self) -> None:
        request_id = self._request_id(self._create())
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        first = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        ).json()
        replay = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["idempotent_replay"])
        self.assertEqual(replay.json()["summary"], first["summary"])

    def test_requester_cannot_approve_own_deletion(self) -> None:
        request_id = self._request_id(self._create())
        denied = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_A_KEY)
        )
        self.assertEqual(denied.status_code, 400, denied.text)

    def test_requester_cannot_execute_own_deletion(self) -> None:
        request_id = self._request_id(self._create())
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        denied = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_A_KEY)
        )
        self.assertEqual(denied.status_code, 400, denied.text)

    def test_approver_cannot_execute_their_own_approval(self) -> None:
        request_id = self._request_id(self._create())
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        denied = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_B_KEY)
        )
        self.assertEqual(denied.status_code, 400, denied.text)

    def test_execute_rejects_unapproved_request(self) -> None:
        request_id = self._request_id(self._create())
        denied = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(denied.status_code, 400, denied.text)

    def test_execute_rejects_unknown_request(self) -> None:
        denied = self.client.post(
            "/api/data-subject-requests/no-such-request/execute",
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_approve_rejects_unknown_request(self) -> None:
        denied = self.client.post(
            "/api/data-subject-requests/no-such-request/approve",
            headers=_headers(ADMIN_B_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_idempotency_key_returns_original_request(self) -> None:
        first = self._create(idempotency_key="p40-idem-12345")
        self.assertEqual(first.status_code, 201, first.text)
        replay = self._create(idempotency_key="p40-idem-12345")
        self.assertEqual(replay.status_code, 201, replay.text)
        self.assertEqual(replay.json()["id"], first.json()["id"])

    def test_approve_twice_rejected(self) -> None:
        request_id = self._request_id(self._create())
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        again = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(again.status_code, 400, again.text)

    def test_list_returns_created_requests(self) -> None:
        first = self._request_id(self._create())
        second = self._request_id(self._create(request_type="export"))
        listed = self.client.get("/api/data-subject-requests", headers=_headers(ADMIN_A_KEY))
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()
        ids = [row["id"] for row in rows]
        self.assertIn(first, ids)
        self.assertIn(second, ids)
        # Board view (Phase 41.4) redacts the customer reference to a stable
        # one-way fingerprint; the raw reference never leaves the API.
        for row in rows:
            self.assertNotIn("CUST-1", row["customer_ref"])
            self.assertEqual(len(row["customer_ref"]), 16)
            self.assertIn("sla_breached", row)
            self.assertIn("sla_due_at", row)

    def test_invalid_request_type_rejected(self) -> None:
        denied = self.client.post(
            "/api/data-subject-requests",
            json={"customer_ref": "CUST-1", "request_type": "purge"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(denied.status_code, 422, denied.text)

    def test_audit_events_never_contain_customer_reference(self) -> None:
        self._request_id(self._create())
        events = self.client.get(
            "/api/audit-events",
            params={"event_type": "data_subject_request.created"},
            headers=_headers(AUDITOR_KEY),
        )
        self.assertEqual(events.status_code, 200, events.text)
        payload = json.dumps(events.json())
        self.assertNotIn("CUST-1", payload)


class DsrExportTests(unittest.TestCase):
    """SEC-002: encrypted exports, one-time tokens, expiry, fail-closed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p40x.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        response = self.client.post(
            "/api/conversations",
            json={"customer_name": "Export Me", "customer_ref": "CUST-EXP"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(response.status_code, 201, response.text)
        conversation_id = response.json()["id"]
        sent = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": "Secret message content"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(sent.status_code, 200, sent.text)

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _completed_export(self, key: str = ADMIN_C_KEY) -> Any:
        response = self.client.post(
            "/api/data-subject-requests",
            json={"customer_ref": "CUST-EXP", "request_type": "export"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(response.status_code, 201, response.text)
        request_id = response.json()["id"]
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(key)
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        return executed.json()

    def test_export_returns_one_time_token_and_full_history(self) -> None:
        body = self._completed_export()
        self.assertEqual(body["summary"]["exported"]["messages"], 2)  # customer + reply
        self.assertIn("download_url", body)
        token = body["download_token"]
        downloaded = self.client.get(
            body["download_url"], params={"token": token}, headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(downloaded.status_code, 200, downloaded.text)
        payload = downloaded.json()
        self.assertEqual(payload["customer_ref"], "CUST-EXP")
        contents = [message["content"] for message in payload["messages"]]
        self.assertIn("Secret message content", contents)

    def test_download_token_is_single_use(self) -> None:
        body = self._completed_export()
        token = body["download_token"]
        url = body["download_url"]
        first = self.client.get(url, params={"token": token}, headers=_headers(ADMIN_C_KEY))
        self.assertEqual(first.status_code, 200)
        second = self.client.get(url, params={"token": token}, headers=_headers(ADMIN_C_KEY))
        self.assertEqual(second.status_code, 404, second.text)

    def test_wrong_token_denied(self) -> None:
        body = self._completed_export()
        denied = self.client.get(
            body["download_url"], params={"token": "forged-token"}, headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_cross_tenant_download_denied(self) -> None:
        body = self._completed_export()
        headers = {"X-API-Key": TENANT2_ADMIN_KEY, "X-Tenant-Id": "acme"}
        denied = self.client.get(
            body["download_url"], params={"token": body["download_token"]}, headers=headers
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_export_is_encrypted_at_rest(self) -> None:
        body = self._completed_export()
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT encrypted_blob, download_token_hash FROM dsr_export_objects",
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotIn("Secret message content", str(row["encrypted_blob"]))
        self.assertNotEqual(row["download_token_hash"], body["download_token"])
        self.assertTrue(str(row["encrypted_blob"]).startswith("gAAAAA"))

    def test_download_reports_404_when_object_expired(self) -> None:
        body = self._completed_export()
        store = DsrExportStore(self.services.database)
        now = int(time.time())
        expired = now + 25 * 3600
        pruned = store.prune_expired(now_epoch=expired)
        self.assertGreaterEqual(pruned, 1)
        denied = self.client.get(
            body["download_url"],
            params={"token": body["download_token"]},
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_store_token_expiry_via_epoch(self) -> None:
        store = DsrExportStore(self.services.database, secret="p40-test-secret")
        now = int(time.time())
        stored = store.store("demo", "req-1", b"payload-bytes", now_epoch=now)
        consumed = store.consume(
            "demo", stored["object_id"], stored["raw_token"], now_epoch=now + 100
        )
        self.assertIsNotNone(consumed)
        assert consumed is not None
        self.assertEqual(consumed["plaintext"], b"payload-bytes")
        # The same token is single-use even within the TTL window.
        again = store.consume("demo", stored["object_id"], stored["raw_token"], now_epoch=now + 101)
        self.assertIsNone(again)
        # A token past its 15-minute TTL is rejected.
        stored2 = store.store("demo", "req-2", b"x", now_epoch=now)
        late = store.consume(
            "demo", stored2["object_id"], stored2["raw_token"], now_epoch=now + 16 * 60
        )
        self.assertIsNone(late)


class DsrFailClosedTests(unittest.TestCase):
    """SEC-002: missing DSR_EXPORT_SECRET never weakens encryption."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p40f.db"
        self._old = os.environ.get("DSR_EXPORT_SECRET")
        if self._old is not None:
            del os.environ["DSR_EXPORT_SECRET"]
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {
                    ADMIN_A_KEY: {"tenant_id": "demo", "actor_id": "admin.a", "role": "admin"},
                    ADMIN_B_KEY: {"tenant_id": "demo", "actor_id": "admin.b", "role": "admin"},
                    ADMIN_C_KEY: {"tenant_id": "demo", "actor_id": "admin.c", "role": "admin"},
                }
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        self.client = TestClient(create_app(settings))
        self.services = cast(Any, self.client.app).state.services

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()
        if self._old is not None:
            os.environ["DSR_EXPORT_SECRET"] = self._old

    def test_export_fails_closed_without_secret(self) -> None:
        created = self.client.post(
            "/api/data-subject-requests",
            json={"customer_ref": "CUST-F", "request_type": "export"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(created.status_code, 201, created.text)
        request_id = created.json()["id"]
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(executed.status_code, 501, executed.text)
        self.assertIn("DSR_EXPORT_SECRET", executed.text)

    def test_deletion_still_works_without_secret(self) -> None:
        created = self.client.post(
            "/api/data-subject-requests",
            json={"customer_ref": "CUST-F", "request_type": "deletion"},
            headers=_headers(ADMIN_A_KEY),
        )
        request_id = created.json()["id"]
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve", headers=_headers(ADMIN_B_KEY)
        )
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute", headers=_headers(ADMIN_C_KEY)
        )
        self.assertEqual(executed.status_code, 200, executed.text)


if __name__ == "__main__":
    unittest.main()
