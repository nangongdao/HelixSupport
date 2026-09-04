"""Tests for the cross-cell replication ingress (ROADMAP 2.2.2)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

CELL_SECRET = "smoke-cell-secret-32bytes-long-0001"


def _build_app() -> TestClient:
    tmp = tempfile.mkdtemp()
    settings = Settings(
        database_path=Path(tmp) / "cell.db",
        widget_secret="smoke-widget-secret",
        widget_frame_ancestors=("'self'",),
        cell_registry_config={
            "cell-default": {
                "db_url": "sqlite:///data/default.db",
                "redis_url": "redis://localhost:6379/0",
                "health_url": "http://127.0.0.1:8000/health",
                "region": "us-east-1",
                "capacity_tier": "default",
            },
            "cell-premium": {
                "db_url": "sqlite:///data/premium.db",
                "redis_url": "redis://localhost:6380/0",
                "health_url": "http://127.0.0.1:8001/health",
                "region": "us-west-2",
                "capacity_tier": "premium",
            },
        },
        current_cell_id="cell-default",
        control_plane_secret=CELL_SECRET,
    )
    return TestClient(create_app(settings))


class TestReplicationIngress(unittest.TestCase):
    def setUp(self) -> None:
        self.client = _build_app()

    def tearDown(self) -> None:
        self.client.close()

    def test_apply_requires_internal_token(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            json={
                "table_name": "conversations",
                "row_id": "conv-1",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            },
        )
        self.assertEqual(response.status_code, 401)

    def test_apply_insert_with_synthetic_defaults(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": CELL_SECRET},
            json={
                "table_name": "conversations",
                "row_id": "conv-2",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "Replicated", "preview": "p"},
            },
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["status"], "applied")

    def test_apply_rejects_unsupported_table(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": CELL_SECRET},
            json={
                "table_name": "evil_table",
                "row_id": "x",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_rejects_invalid_operation(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": CELL_SECRET},
            json={
                "table_name": "conversations",
                "row_id": "x",
                "operation": "drop",
                "tenant_id": "demo",
                "payload": {},
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_apply_delete(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": CELL_SECRET},
            json={
                "table_name": "conversations",
                "row_id": "conv-3",
                "operation": "delete",
                "tenant_id": "demo",
                "payload": {},
            },
        )
        self.assertEqual(response.status_code, 200)

    def test_apply_wrong_token_rejected(self) -> None:
        response = self.client.post(
            "/api/internal/replication/apply",
            headers={"X-Internal-Token": "wrong"},
            json={
                "table_name": "conversations",
                "row_id": "x",
                "operation": "insert",
                "tenant_id": "demo",
                "payload": {"customer_name": "A"},
            },
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
