"""Tests for the cost dashboard API (ROADMAP 2.3.4)."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ADMIN_KEY = "analytics-admin-key-001"
VIEWER_KEY = "analytics-viewer-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer", "role": "viewer"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=__import__("json").dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class CostAnalyticsAPITests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "analytics.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _seed_cost(self, cost_usd: float, date_str: str | None = None) -> None:
        self.services.cost_attribution.record_inference_cost(
            "demo",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=cost_usd,
            date_str=date_str,
        )

    def test_daily_cost_summary(self) -> None:
        self._seed_cost(0.0048, "2026-09-01")
        self._seed_cost(0.0048, "2026-09-02")
        response = self.client.get(
            "/api/analytics/costs/daily",
            params={"start_date": "2026-09-01", "end_date": "2026-09-02"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tenant_id"], "demo")
        self.assertEqual(payload["turn_count"], 2)
        self.assertAlmostEqual(payload["cost_usd"], 0.0096, places=6)

    def test_breakdown_by_agent(self) -> None:
        self.services.cost_attribution.record_inference_cost(
            "demo",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=0.0048,
            context={"agent": "triage"},
            date_str="2026-09-01",
        )
        response = self.client.get(
            "/api/analytics/costs/by_agent", params={"date": "2026-09-01"}, headers=self.admin
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "triage")
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.0048, places=6)

    def test_breakdown_by_prompt(self) -> None:
        self.services.cost_attribution.record_inference_cost(
            "demo",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=0.0048,
            context={"prompt_version": "v1"},
            date_str="2026-09-01",
        )
        response = self.client.get(
            "/api/analytics/costs/by_prompt", params={"date": "2026-09-01"}, headers=self.admin
        )
        self.assertEqual(response.status_code, 200)
        rows = response.json()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["prompt_version"], "v1")

    def test_anomaly_endpoint(self) -> None:
        response = self.client.get("/api/analytics/costs/anomaly", headers=self.admin)
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("anomaly", payload)
        self.assertIn("factor", payload)

    def test_viewer_forbidden(self) -> None:
        response = self.client.get("/api/analytics/costs/daily", headers=self.viewer)
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
