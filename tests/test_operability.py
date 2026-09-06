"""Phase 30: operability & release tests.

- 30.2 GET /api/admin/diagnostics returns a support bundle (version, redacted
  config, queue state, audit chain head) under admin RBAC;
- 30.2 health tiers: liveness, startup, readiness;
- 30.6 migration drill: a legacy snapshot migrates forward to the latest
  schema version with a contiguous chain.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "ops-admin-key-0001"
OPERATOR_KEY = "ops-op-key-000001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op", "role": "operator"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class DiagnosticsTests(unittest.TestCase):
    """30.2: support diagnostics bundle."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "diag.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def test_diagnostics_requires_admin(self) -> None:
        response = self.client.get("/api/admin/diagnostics", headers=self.operator)
        self.assertEqual(response.status_code, 403)

    def test_diagnostics_bundle_shape(self) -> None:
        response = self.client.get("/api/admin/diagnostics", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        for key in ("version", "config", "queue", "turn_worker", "audit_chain_head"):
            self.assertIn(key, body, f"missing diagnostics key {key}")
        self.assertEqual(body["version"], "2.7.0")
        # Config is redacted: no secrets.
        self.assertNotIn("api_keys", body["config"])
        self.assertNotIn("secret", json.dumps(body["config"]))

    def test_health_tiers(self) -> None:
        for path in ("/health/live", "/health/startup", "/health/ready"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path} -> {response.status_code}")


class MigrationDrillTests(unittest.TestCase):
    """30.6: legacy snapshot migrates forward with contiguous chain."""

    def test_migration_drill_script(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "drill.db"
            result = subprocess.run(
                [sys.executable, "scripts/migration_drill.py", "--db", str(db_path)],
                capture_output=True,
                text=True,
                # `text=True` alone decodes with the OS default codepage (GBK
                # on Windows). The drill's output is ASCII today, so this is
                # prophylactic: the first non-ASCII character in a diagnostic
                # would kill the reader thread and hand the assertions below a
                # None stderr, failing with a TypeError that says nothing
                # about the migration. Same fix as tests/test_audit_anchors.py.
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("migration drill passed", result.stdout)

    def test_migration_chain_contiguous(self) -> None:
        from app.migrations import all_migrations, verify_migration_chain

        self.assertEqual(verify_migration_chain(all_migrations()), [])

    def test_migration_drill_refuses_to_overwrite_existing_db(self) -> None:
        """Regression: the drill must not destroy an existing database by
        default (data-loss footgun)."""
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "existing.db"
            db_path.write_bytes(b"real database data")
            result = subprocess.run(
                [sys.executable, "scripts/migration_drill.py", "--db", str(db_path)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                cwd=str(Path(__file__).resolve().parents[1]),
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("refusing to overwrite", result.stderr)
            # The existing database is untouched.
            self.assertEqual(db_path.read_bytes(), b"real database data")


if __name__ == "__main__":
    unittest.main()
