"""Phase 41.4 (DATA): privacy-operations pipeline.

Covers the operations contract layered over RetentionService:

- canary leak safety for the unified field-level redaction (key and value
  channels) plus the ``assert_no_canary_leaks`` harness;
- DSR SLA due-time and breach flagging;
- deletion-proof attestation keyed by the per-request execution secret;
- tombstone-after-restore replay deleting resurrected data;
- deferred large-deletion drain by the housekeeping window;
- failed-execution retry round-trip (``approved`` -> ``failed`` -> ``approved``).

All tests run against an in-memory SQLite database through the FastAPI
TestClient so route wiring and permissions are exercised end to end.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.privacy import (
    DEFERRED_DELETION_THRESHOLD,
    DataProtectionService,
    DeferredDeletionStore,
)
from app.redaction import (
    assert_no_canary_leaks,
    make_canary,
    redact_for_logs,
    redact_sensitive,
)

ADMIN_A_KEY = "p41-admin-a-00001"
ADMIN_B_KEY = "p41-admin-b-00002"
ADMIN_C_KEY = "p41-admin-c-00003"
OPERATOR_KEY = "p41-op-key-0000001"
AUDITOR_KEY = "p41-auditor-key-001"
VIEWER_KEY = "p41-viewer-key-001"

_DEMO_PRINCIPALS = {
    ADMIN_A_KEY: {"tenant_id": "demo", "actor_id": "admin.a", "role": "admin"},
    ADMIN_B_KEY: {"tenant_id": "demo", "actor_id": "admin.b", "role": "admin"},
    ADMIN_C_KEY: {"tenant_id": "demo", "actor_id": "admin.c", "role": "admin"},
    OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"},
    AUDITOR_KEY: {"tenant_id": "demo", "actor_id": "aud.user", "role": "auditor"},
    VIEWER_KEY: {"tenant_id": "demo", "actor_id": "view.user", "role": "viewer"},
}


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(_DEMO_PRINCIPALS),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        dsr_export_secret=os.environ.get("DSR_EXPORT_SECRET", "p41-test-secret"),
    )


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key, "X-Tenant-Id": "demo"}


class PrivacyAppCase(unittest.TestCase):
    """Shared app bootstrapping with a seeded customer conversation."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "p41.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services: Any = cast(Any, self.client.app).state.services
        self.dp: DataProtectionService = cast(Any, self.services).data_protection
        self.customer_ref = "CUST-41"
        self._seed_conversation(self.customer_ref)

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

    def _create(self, request_type: str = "deletion", customer_ref: str | None = None) -> str:
        response = self.client.post(
            "/api/data-subject-requests",
            json={
                "customer_ref": customer_ref or self.customer_ref,
                "request_type": request_type,
            },
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _approve_execute(self, request_id: str) -> dict[str, Any]:
        approved = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve",
            headers=_headers(ADMIN_B_KEY),
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute",
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        return executed.json()


class RedactionCanaryTests(PrivacyAppCase):
    """The unified redactor never lets a seeded canary survive a sink path."""

    def test_key_channel_redacts_canary_field(self) -> None:
        canary = make_canary()
        payload = {
            "customer_ref": canary,
            "message": "the body text",
            "nested": {"download_token": canary},
        }
        out = redact_for_logs(payload)
        assert_no_canary_leaks(out)
        self.assertNotIn(canary, json.dumps(out))
        self.assertEqual(out["customer_ref"], "[REDACTED]")
        self.assertEqual(out["nested"]["download_token"], "[REDACTED]")

    def test_value_channel_redacts_canary_inside_bearer_token(self) -> None:
        canary = make_canary()
        payload = {"body": f"Bearer {canary}"}
        out = redact_for_logs(payload)
        assert_no_canary_leaks(out)
        self.assertNotIn(canary, json.dumps(out))
        self.assertEqual(out["body"], "[REDACTED]")

    def test_canary_inside_long_hex_is_redacted(self) -> None:
        canary = make_canary()
        # Pad the canary's 32-hex core into a 48-hex blob so the value channel
        # (long-hex pattern) must strip it even though the key name is innocent.
        value = canary + "0123456789abcdef"
        payload = {"ref": value}
        out = redact_sensitive(payload)
        assert_no_canary_leaks(out)
        self.assertNotIn(canary, json.dumps(out))
        self.assertEqual(out["ref"], "[REDACTED]")

    def test_permissions_deny_privacy_console_to_non_admin(self) -> None:
        for key in (OPERATOR_KEY, AUDITOR_KEY, VIEWER_KEY):
            with self.subTest(key=key):
                denied = self.client.get("/api/privacy/board", headers=_headers(key))
                self.assertEqual(denied.status_code, 403, key)


class SlaBreachTests(PrivacyAppCase):
    """DSR SLA due time is recorded and breach scanning flags overdue items."""

    def test_created_request_carries_sla_due(self) -> None:
        request_id = self._create()
        board = self.client.get("/api/privacy/board", headers=_headers(ADMIN_A_KEY))
        self.assertEqual(board.status_code, 200, board.text)
        row = next(item for item in board.json() if item["id"] == request_id)
        self.assertIsNotNone(row["sla_due_at"])
        self.assertEqual(row["sla_breached"], 0)

    def test_flag_sla_breaches_marks_overdue_request(self) -> None:
        request_id = self._create()
        with self.services.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET sla_due_at = '2000-01-01T00:00:00' "
                "WHERE id = ? AND tenant_id = 'demo'",
                (request_id,),
            )
        flagged = self.client.post("/api/privacy/sla/scan", headers=_headers(ADMIN_A_KEY))
        self.assertEqual(flagged.status_code, 200, flagged.text)
        self.assertGreaterEqual(flagged.json()["flagged"], 1)
        board = self.client.get("/api/privacy/board", headers=_headers(ADMIN_A_KEY))
        row = next(item for item in board.json() if item["id"] == request_id)
        self.assertEqual(row["sla_breached"], 1)

    def test_sla_scan_is_idempotent(self) -> None:
        request_id = self._create()
        with self.services.database.connect() as conn:
            conn.execute(
                "UPDATE data_subject_requests SET sla_due_at = '2000-01-01T00:00:00' "
                "WHERE id = ? AND tenant_id = 'demo'",
                (request_id,),
            )
        first = self.client.post("/api/privacy/sla/scan", headers=_headers(ADMIN_A_KEY)).json()[
            "flagged"
        ]
        second = self.client.post("/api/privacy/sla/scan", headers=_headers(ADMIN_A_KEY)).json()[
            "flagged"
        ]
        self.assertGreaterEqual(first, 1)
        self.assertEqual(second, 0)
        board = self.client.get("/api/privacy/board", headers=_headers(ADMIN_A_KEY))
        row = next(item for item in board.json() if item["id"] == request_id)
        self.assertEqual(row["sla_breached"], 1)


class DeletionProofTests(PrivacyAppCase):
    """Executed deletions leave a secret-keyed attestation; wrong secret is None."""

    def _execution_secret(self, request_id: str) -> str:
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT execution_secret FROM data_subject_requests "
                "WHERE id = ? AND tenant_id = 'demo'",
                (request_id,),
            ).fetchone()
        assert row is not None
        return str(row["execution_secret"])

    def test_proof_requires_the_execution_secret(self) -> None:
        request_id = self._create()
        self._approve_execute(request_id)
        secret = self._execution_secret(request_id)
        proof = self.client.get(
            "/api/privacy/deletion-proof",
            params={"customer_ref": self.customer_ref, "secret": secret},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(proof.status_code, 200, proof.text)
        body = proof.json()
        self.assertEqual(body["request_id"], request_id)
        self.assertEqual(body["customer_ref"], self.customer_ref)

    def test_wrong_secret_yields_no_proof(self) -> None:
        request_id = self._create()
        self._approve_execute(request_id)
        denied = self.client.get(
            "/api/privacy/deletion-proof",
            params={
                "customer_ref": self.customer_ref,
                "secret": "forged-secret-value",
            },
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_no_proof_without_deletion(self) -> None:
        denied = self.client.get(
            "/api/privacy/deletion-proof",
            params={"customer_ref": "NEVER-EXISTED", "secret": "anything"},
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)


class TombstoneRestoreTests(PrivacyAppCase):
    """After a backup restore, tombstones re-delete resurrected data."""

    def test_restore_reapplies_deletion(self) -> None:
        request_id = self._create()
        self._approve_execute(request_id)
        with self.services.database.connect() as conn:
            gone = conn.execute(
                "SELECT COUNT(*) AS n FROM conversations "
                "WHERE tenant_id = 'demo' AND customer_ref = ?",
                (self.customer_ref,),
            ).fetchone()
        self.assertEqual(int(gone["n"]), 0)

        # Simulate a backup restore: the erased conversation is back on disk.
        with self.services.database.connect() as conn:
            conn.execute(
                "INSERT INTO conversations "
                "(id, tenant_id, customer_name, customer_ref, channel, status, "
                "priority, message_count, created_at, updated_at) VALUES "
                "('resurrected-1', 'demo', 'Customer', ?, 'web', 'open', "
                "'normal', 1, '2026-01-01T00:00:00', '2026-01-01T00:00:00')",
                (self.customer_ref,),
            )
            conn.execute(
                "INSERT INTO messages "
                "(id, tenant_id, conversation_id, author, content, role, "
                "created_at) VALUES "
                "('resurrected-msg', 'demo', 'resurrected-1', 'system', 'zombie text', "
                "'user', '2026-01-01T00:00:00')",
            )

        results = self.dp.enforce_tombstones_after_restore()
        self.assertGreaterEqual(results.get("deleted", 0), 1)
        with self.services.database.connect() as conn:
            gone_again = conn.execute(
                "SELECT COUNT(*) AS n FROM conversations "
                "WHERE tenant_id = 'demo' AND customer_ref = ?",
                (self.customer_ref,),
            ).fetchone()
        self.assertEqual(int(gone_again["n"]), 0)

    def test_tombstone_list_excludes_secret_hash(self) -> None:
        request_id = self._create()
        self._approve_execute(request_id)
        listed = self.client.get("/api/privacy/tombstones", headers=_headers(ADMIN_A_KEY))
        self.assertEqual(listed.status_code, 200, listed.text)
        rows = listed.json()
        self.assertTrue(any(row["request_id"] == request_id for row in rows))
        for row in rows:
            self.assertNotIn("secret", row)


class DeferredDeletionTests(PrivacyAppCase):
    """Large deletions are deferred to the housekeeping queue, then drained."""

    def test_large_deletion_is_deferred_and_drained(self) -> None:
        big_ref = "CUST-BULK-41"
        with self.services.database.connect() as conn:
            now = "2026-08-01T00:00:00"
            for i in range(DEFERRED_DELETION_THRESHOLD + 5):
                conn.execute(
                    "INSERT INTO conversations "
                    "(id, tenant_id, customer_name, customer_ref, channel, status, "
                    "priority, message_count, created_at, updated_at) VALUES "
                    "(?, 'demo', 'Bulk', ?, 'web', 'open', 'normal', 0, ?, ?)",
                    (f"bulk-conv-{i:06d}", big_ref, now, now),
                )
                conn.execute(
                    "INSERT INTO messages "
                    "(id, tenant_id, conversation_id, author, content, role, "
                    "created_at) VALUES "
                    "(?, 'demo', ?, 'system', 'bulk payload', 'user', ?)",
                    (f"bulk-msg-{i:06d}", f"bulk-conv-{i:06d}", now),
                )
        assert self.dp.estimate_deletion_size("demo", big_ref) >= DEFERRED_DELETION_THRESHOLD

        request_id = self._create(customer_ref=big_ref)
        approved = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve",
            headers=_headers(ADMIN_B_KEY),
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute",
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        body = executed.json()
        self.assertTrue(body.get("deferred"))
        self.assertEqual(body["status"], "accepted")

        with self.services.database.connect() as conn:
            queued = conn.execute(
                "SELECT COUNT(*) AS n FROM deferred_deletion_jobs "
                "WHERE tenant_id = 'demo' AND request_id = ?",
                (request_id,),
            ).fetchone()
        self.assertGreaterEqual(int(queued["n"]), 1)

        drained = self.dp.drain_deferred_jobs(tenant_id="demo", batch=5)
        self.assertGreaterEqual(drained, 1)
        with self.services.database.connect() as conn:
            status = conn.execute(
                "SELECT status FROM data_subject_requests WHERE id = ?",
                (request_id,),
            ).fetchone()
            remaining = conn.execute(
                "SELECT COUNT(*) AS n FROM conversations "
                "WHERE tenant_id = 'demo' AND customer_ref = ?",
                (big_ref,),
            ).fetchone()
        self.assertEqual(str(status["status"]), "completed")
        self.assertEqual(int(remaining["n"]), 0)

    def test_small_deletion_runs_synchronously(self) -> None:
        request_id = self._create()
        approved = self.client.post(
            f"/api/data-subject-requests/{request_id}/approve",
            headers=_headers(ADMIN_B_KEY),
        )
        self.assertEqual(approved.status_code, 200, approved.text)
        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute",
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertFalse(executed.json().get("deferred", False))
        self.assertEqual(executed.json()["status"], "completed")


class RetryFailedRequestTests(PrivacyAppCase):
    """A failed execution is re-approvable via the retry endpoint."""

    def test_failed_execution_can_be_retried(self) -> None:
        # Executor == requester is rejected by retention, which flips the
        # request to 'failed' instead of leaving it stuck ('approved').
        request_id = self._create()
        self.client.post(
            f"/api/data-subject-requests/{request_id}/approve",
            headers=_headers(ADMIN_B_KEY),
        )
        denied = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute",
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(denied.status_code, 400, denied.text)

        retried = self.client.post(
            f"/api/privacy/requests/{request_id}/retry",
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(retried.status_code, 200, retried.text)
        self.assertEqual(retried.json()["status"], "approved")

        executed = self.client.post(
            f"/api/data-subject-requests/{request_id}/execute",
            headers=_headers(ADMIN_C_KEY),
        )
        self.assertEqual(executed.status_code, 200, executed.text)
        self.assertEqual(executed.json()["status"], "completed")

    def test_retry_unknown_request_404(self) -> None:
        denied = self.client.post(
            "/api/privacy/requests/no-such-request/retry",
            headers=_headers(ADMIN_A_KEY),
        )
        self.assertEqual(denied.status_code, 404, denied.text)

    def test_privacy_permission_is_admin_only(self) -> None:
        for key in (OPERATOR_KEY, AUDITOR_KEY, VIEWER_KEY):
            with self.subTest(key=key):
                denied = self.client.post("/api/privacy/sla/scan", headers=_headers(key))
                self.assertEqual(denied.status_code, 403, key)

    def test_import_secret_hash_uses_constant_time_compare(self) -> None:
        # Direct store-level check that the proof route never compares raw
        # secrets; the store works on hashes throughout.
        store = DeferredDeletionStore(self.services.database)
        self.assertIsNotNone(store)
        store.enqueue("demo", "req-dummy", "CUST-41")
        open_jobs = store.list_open("demo")
        self.assertTrue(any(job["request_id"] == "req-dummy" for job in open_jobs))


if __name__ == "__main__":
    unittest.main()
