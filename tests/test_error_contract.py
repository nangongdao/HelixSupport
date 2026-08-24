"""Phase 25.1: RFC 9457 Problem Details error contract.

Every API error response must be a Problem Details document carrying
``type/title/status/detail/instance`` plus ``request_id`` and the ``code``
extension, while preserving the legacy top-level ``detail`` field for one
minor release. These tests pin the contract so a route that regresses to a
bare ``{"detail": ...}`` body fails the gate.
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

ADMIN_KEY = "errcontract-admin-key-01"
OPERATOR_KEY = "errcontract-op-key-000001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
        OPERATOR_KEY: {
            "tenant_id": "demo",
            "actor_id": "operator.user",
            "role": "operator",
        },
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class ProblemDetailsContractTests(unittest.TestCase):
    """The full Problem Details envelope on representative error classes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "errors.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        # Close the DB pool explicitly (Windows keeps file handles otherwise)
        # before removing the temp directory.
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _assert_problem(self, response, *, status: int, code: str) -> dict:
        self.assertEqual(response.status_code, status)
        body = response.json()
        # Envelope fields always present.
        self.assertEqual(body["status"], status)
        self.assertEqual(body["code"], code)
        self.assertIn("type", body)
        self.assertEqual(body["type"], f"urn:helix:error:{code}")
        self.assertIn("title", body)
        self.assertIn("detail", body)
        self.assertIn("instance", body)
        self.assertIn("request_id", body)
        self.assertTrue(body["request_id"].startswith("req_"))
        # Legacy compatibility: top-level detail still carries the message.
        self.assertIsInstance(body["detail"], str)
        return body

    def test_not_found_lookup(self) -> None:
        response = self.client.get("/api/conversations/conv-nope", headers=self.admin)
        self._assert_problem(response, status=404, code="not_found")
        self.assertEqual(response.json()["instance"], "/api/conversations/conv-nope")

    def test_forbidden_rbac(self) -> None:
        # Operator lacks metrics:read.
        response = self.client.get("/api/supervisor/quality", headers=self.operator)
        self._assert_problem(response, status=403, code="forbidden")

    def test_unauthorized_bad_key(self) -> None:
        response = self.client.get(
            "/api/conversations",
            headers={"X-API-Key": "wrong-key-12345678", "X-Tenant-Id": "demo"},
        )
        self._assert_problem(response, status=401, code="unauthorized")

    def test_validation_422_has_errors_list(self) -> None:
        response = self.client.post(
            "/api/conversations", json={"customer_name": ""}, headers=self.admin
        )
        body = self._assert_problem(response, status=422, code="validation_error")
        self.assertIn("errors", body)
        self.assertIsInstance(body["errors"], list)
        self.assertGreaterEqual(len(body["errors"]), 1)
        # Per-field structure survives (type/loc/msg).
        first = body["errors"][0]
        self.assertIn("type", first)
        self.assertIn("loc", first)
        self.assertIn("msg", first)

    def test_conflict_invalid_transition(self) -> None:
        # Retiring a non-existent article yields 404; a duplicate shortcut is
        # 409. Use canned-response shortcut conflict for a deterministic 409.
        first = self.client.post(
            "/api/canned-responses",
            json={"title": "T", "body": "body", "shortcut": "dup"},
            headers=self.admin,
        )
        self.assertEqual(first.status_code, 201, first.text)
        second = self.client.post(
            "/api/canned-responses",
            json={"title": "T2", "body": "body2", "shortcut": "dup"},
            headers=self.admin,
        )
        body = self._assert_problem(second, status=409, code="conflict")
        self.assertIn("detail", body)

    def test_rate_limited_429(self) -> None:
        principals = {
            "rl-admin-key-00001": {
                "tenant_id": "demo",
                "actor_id": "admin.user",
                "role": "admin",
            }
        }
        settings = Settings(
            database_path=Path(self._tmp.name) / "rl.db",
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=2,
            docs_enabled=False,
        )
        client = TestClient(create_app(settings))
        try:
            headers = {"X-API-Key": "rl-admin-key-00001", "X-Tenant-Id": "demo"}
            for _ in range(2):
                client.get("/api/conversations", headers=headers)
            limited = client.get("/api/conversations", headers=headers)
            self._assert_problem(limited, status=429, code="rate_limited")
            self.assertIn("Retry-After", limited.headers)
        finally:
            cast(Any, client.app).state.services.database.close()
            client.close()


if __name__ == "__main__":
    unittest.main()
