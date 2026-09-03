"""Phase 42.1 / REL-001: PROCESS_ROLE web/worker split.

A ``web`` process (stateless API tier) must not start the turn worker or its
housekeeping loop — scaling the web fleet must not silently add workers. A
``worker``/``all`` process starts the worker exactly when the queue is ready
(M0 REL-001 fail-closed). The role is surfaced through /health/ready and
/api/admin/diagnostics so an operator can tell the instances apart.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _settings(role: str, db: Path) -> Settings:
    return Settings(
        database_path=db,
        auth_mode="api_key",
        api_keys_json=json.dumps(
            {"admin-key-0123456789": {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
        ),
        rate_limit_per_minute=20000,
        docs_enabled=False,
        process_role=role,
    )


class ProcessRoleConfigTests(unittest.TestCase):
    def test_default_role_is_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(database_path=Path(tmp) / "x.db", auth_mode="api_key")
            self.assertEqual(s.process_role, "all")
            self.assertTrue(s.runs_turn_worker)

    def test_web_role_does_not_run_worker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            s = Settings(database_path=Path(tmp) / "x.db", auth_mode="api_key", process_role="web")
            self.assertFalse(s.runs_turn_worker)

    def test_invalid_role_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Settings(
                    database_path=Path(tmp) / "x.db", auth_mode="api_key", process_role="edge"
                ).validate()


class ProcessRoleAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _db(self) -> Path:
        return Path(self._tmp.name) / f"{self._testMethodName}.db"

    def _admin_headers(self) -> dict[str, str]:
        return {"X-API-Key": "admin-key-0123456789", "X-Tenant-Id": "demo"}

    def test_web_role_does_not_start_turn_worker(self) -> None:
        app = create_app(_settings("web", self._db()))
        client = TestClient(app)
        services = app.state.services
        try:
            client.__enter__()  # run startup handlers
            worker = services.turn_worker
            self.assertFalse(
                any(t.is_alive() for t in worker._threads),
                "web role must not start the turn worker",
            )
            ready = client.get("/health/ready").json()
            self.assertEqual(ready["process_role"], "web")
            self.assertFalse(ready["runs_turn_worker"])
        finally:
            client.__exit__(None, None, None)
            services.database.close()

    def test_all_role_starts_turn_worker(self) -> None:
        app = create_app(_settings("all", self._db()))
        client = TestClient(app)
        services = app.state.services
        try:
            client.__enter__()
            worker = services.turn_worker
            self.assertTrue(
                any(t.is_alive() for t in worker._threads) or not worker.enabled,
                "all role should start a live worker thread (or worker is disabled)",
            )
            ready = client.get("/health/ready").json()
            self.assertEqual(ready["process_role"], "all")
            self.assertTrue(ready["runs_turn_worker"])
        finally:
            client.__exit__(None, None, None)
            services.database.close()

    def test_diagnostics_exposes_process_role(self) -> None:
        app = create_app(_settings("worker", self._db()))
        client = TestClient(app)
        services = app.state.services
        try:
            client.__enter__()
            diag = client.get("/api/admin/diagnostics", headers=self._admin_headers()).json()
            self.assertEqual(diag["config"]["process_role"], "worker")
        finally:
            client.__exit__(None, None, None)
            services.database.close()


if __name__ == "__main__":
    unittest.main()
