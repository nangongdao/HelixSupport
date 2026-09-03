"""Phase 43.3: API deprecation lifecycle (docs/API_POLICY.md §2).

Pins the contract of ``app.deprecation``:

* registry entries render IMF-fixdate Deprecation/Sunset headers plus a
  successor Link;
* non-deprecated operations get no headers;
* an expired sunset or inverted window is a gate violation (and
  ``create_app`` refuses to boot on one);
* the OpenAPI document carries ``deprecated: true`` and a migration note;
* the live middleware stamps headers via the route's templated path.

The shipped registry is empty (nothing is deprecated today); tests drive
the machinery through the pure functions and by patching the registry —
the mechanism must be proven now so marking any future endpoint is a
one-line change.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.deprecation import (
    DEPRECATION_HEADER,
    SUNSET_HEADER,
    Deprecation,
    active_deprecations,
    apply_deprecation_headers,
    deprecation_for,
    enrich_openapi_with_deprecations,
    validate_registry,
)
from app.main import create_app


_SAMPLE = Deprecation(
    operation="GET /api/conversations",
    deprecated_on="2026-08-22",
    sunset_on="2027-09-01",
    successor="/api/v2/conversations",
    reason="Replaced by the v2 cursor envelope.",
)


class RegistryTests(unittest.TestCase):
    def test_empty_registry_is_valid_and_active(self) -> None:
        self.assertEqual(active_deprecations(), [])
        self.assertEqual(validate_registry(), [])
        self.assertIsNone(deprecation_for("GET /api/conversations"))

    def test_headers_use_imf_fixdate_and_successor_link(self) -> None:
        with patch("app.deprecation._REGISTRY", {"GET /api/conversations": _SAMPLE}):
            headers: dict[str, str] = {}
            applied = apply_deprecation_headers("GET /api/conversations", headers)
        self.assertTrue(applied)
        self.assertEqual(headers[DEPRECATION_HEADER], "Sat, 22 Aug 2026 00:00:00 GMT")
        self.assertEqual(headers[SUNSET_HEADER], "Wed, 01 Sep 2027 00:00:00 GMT")
        self.assertIn('rel="successor-version"', headers["Link"])
        self.assertIn("/api/v2/conversations", headers["Link"])

    def test_unregistered_operation_gets_no_headers(self) -> None:
        headers: dict[str, str] = {}
        self.assertFalse(apply_deprecation_headers("POST /api/conversations", headers))
        self.assertEqual(headers, {})

    def test_expired_sunset_is_a_violation(self) -> None:
        expired = Deprecation(
            operation="GET /api/old",
            deprecated_on="2026-01-01",
            sunset_on="2026-02-01",
            successor="/api/new",
        )
        with (
            patch("app.deprecation._DEPRECATIONS", (expired,)),
            patch("app.deprecation._REGISTRY", {"GET /api/old": expired}),
        ):
            problems = validate_registry(today="2026-03-01")
        self.assertEqual(len(problems), 1)
        self.assertIn("has passed", problems[0])

    def test_inverted_window_is_a_violation(self) -> None:
        inverted = Deprecation(
            operation="GET /api/bad",
            deprecated_on="2027-01-01",
            sunset_on="2026-01-01",
            successor="/api/new",
        )
        with (
            patch("app.deprecation._DEPRECATIONS", (inverted,)),
            patch("app.deprecation._REGISTRY", {"GET /api/bad": inverted}),
        ):
            problems = validate_registry(today="2026-06-01")
        self.assertTrue(any("precedes" in problem for problem in problems))

    def test_active_deprecations_filters_expired(self) -> None:
        old = Deprecation(
            operation="GET /api/old",
            deprecated_on="2026-01-01",
            sunset_on="2026-02-01",
            successor="/api/new",
        )
        current = _SAMPLE
        with patch("app.deprecation._DEPRECATIONS", (old, current)):
            active = active_deprecations(today="2026-08-22")
        self.assertEqual([entry.operation for entry in active], [current.operation])


class CreateAppGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path=Path(self._tmp.name) / "dep.db",
            turn_worker_enabled=False,
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_healthy_registry_boots(self) -> None:
        app = create_app(self.settings)
        client = TestClient(app)
        response = client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        app.state.services.database.close()

    def test_expired_sunset_blocks_startup(self) -> None:
        expired = Deprecation(
            operation="GET /api/conversations",
            deprecated_on="2020-01-01",
            sunset_on="2021-01-01",
            successor="/api/v2/conversations",
        )
        with patch("app.deprecation._DEPRECATIONS", (expired,)):
            with self.assertRaises(RuntimeError) as ctx:
                create_app(self.settings)
        self.assertIn("sunset policy", str(ctx.exception))


class OpenApiDeprecationTests(unittest.TestCase):
    def test_enrich_marks_operation_with_migration_note(self) -> None:
        paths: dict[str, Any] = {
            "/api/conversations": {
                "get": {"description": "List conversations."},
            }
        }
        with patch("app.deprecation._DEPRECATIONS", (_SAMPLE,)):
            marked = enrich_openapi_with_deprecations(paths)
        self.assertEqual(marked, 1)
        operation = paths["/api/conversations"]["get"]
        self.assertTrue(operation["deprecated"])
        self.assertIn("Deprecated since 2026-08-22", operation["description"])
        self.assertIn("/api/v2/conversations", operation["description"])

    def test_enrich_skips_unknown_paths(self) -> None:
        paths: dict[str, Any] = {"/api/other": {"get": {}}}
        with patch("app.deprecation._DEPRECATIONS", (_SAMPLE,)):
            marked = enrich_openapi_with_deprecations(paths)
        self.assertEqual(marked, 0)
        self.assertNotIn("deprecated", paths["/api/other"]["get"])


class MiddlewareHeaderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path=Path(self._tmp.name) / "mw.db",
            turn_worker_enabled=False,
            auth_mode="api_key",
            api_keys_json=_keys_json(),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_live_response_carries_headers_for_registered_operation(self) -> None:
        # Patch before create_app so the startup gate sees a healthy registry.
        with (
            patch("app.deprecation._DEPRECATIONS", (_SAMPLE,)),
            patch("app.deprecation._REGISTRY", {_SAMPLE.operation: _SAMPLE}),
        ):
            app = create_app(self.settings)
            client = TestClient(app)
            response = client.get("/api/conversations", headers=_auth(app))
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.headers[DEPRECATION_HEADER], "Sat, 22 Aug 2026 00:00:00 GMT")
            self.assertEqual(response.headers[SUNSET_HEADER], "Wed, 01 Sep 2027 00:00:00 GMT")
            # A non-deprecated endpoint stays clean.
            health = client.get("/health/ready")
            self.assertNotIn(DEPRECATION_HEADER, health.headers)
        app.state.services.database.close()

    def test_templated_path_resolves_to_operation_key(self) -> None:
        """A path parameter request still matches 'GET /api/conversations/{id}' shape."""
        entry = Deprecation(
            operation="GET /api/conversations/{conversation_id}",
            deprecated_on="2026-08-22",
            sunset_on="2027-09-01",
            successor="/api/v2/conversations/{conversation_id}",
        )
        with (
            patch("app.deprecation._DEPRECATIONS", (entry,)),
            patch("app.deprecation._REGISTRY", {entry.operation: entry}),
        ):
            app = create_app(self.settings)
            client = TestClient(app)
            created = client.post(
                "/api/conversations",
                json={"customer_name": "Deprecation Probe"},
                headers=_auth(app),
            )
            conversation_id = created.json()["id"]
            detail = client.get(f"/api/conversations/{conversation_id}", headers=_auth(app))
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertIn(DEPRECATION_HEADER, detail.headers)
        app.state.services.database.close()


def _auth(app: Any) -> dict[str, str]:
    """First configured admin principal for the test settings."""
    keys = json.loads(_keys_json())
    key = next(iter(keys))
    return {"X-API-Key": key, "X-Tenant-Id": keys[key]["tenant_id"]}


def _keys_json() -> str:
    return json.dumps(
        {
            "dep-admin-key-0000000001": {
                "tenant_id": "demo",
                "actor_id": "admin",
                "role": "admin",
            }
        }
    )


if __name__ == "__main__":
    unittest.main()
