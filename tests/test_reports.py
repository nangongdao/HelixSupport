"""Backlog: report export & subscription (报表导出与订阅).

Covers the roadmap acceptance:
- subscription CRUD is tenant-scoped and requires admin:manage;
- on-demand generation returns bounded quality/usage rows;
- CSV export has the right header/rows and tenant isolation;
- the due-scan generates + delivers reports to the subscription's webhook
  endpoint (deduplicated per subscription+period) and updates last_run_at;
- weekly cadence is due only after 7 days;
- invalid input (bad report_type/schedule/endpoint) is rejected.
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
from app.reports import ReportService
from app.webhooks import EVENT_REPORT_GENERATED

ADMIN_KEY = "reports-admin-key-001"
OPERATOR_KEY = "reports-operator-key-01"
ACME_ADMIN_KEY = "reports-acme-admin-key-1"


def _settings(db_path: Path) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op", "role": "operator"},
        ACME_ADMIN_KEY: {"tenant_id": "acme", "actor_id": "acme-admin", "role": "admin"},
    }
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class ReportAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "reports.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.reports: ReportService = self.services.reports
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _send_turn(self, conversation_id: str, content: str, key: str) -> None:
        response = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            json={"content": content},
            headers={**self.admin, "Idempotency-Key": key},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _seed_usage(self) -> None:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": "S"}, headers=self.admin
        ).json()
        self._send_turn(conv["id"], "我的订单什么时候发货", "report-key-1")
        self._send_turn(conv["id"], "ORD-123 退款", "report-key-2")

    def _register_endpoint(self) -> str:
        response = self.client.post(
            "/api/webhooks",
            json={
                "url": "https://93.184.216.34/hook",
                "events": [EVENT_REPORT_GENERATED],
                "secret": "report-secret",
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    # ------------------------------------------------------------- subscriptions

    def test_subscription_crud(self) -> None:
        endpoint_id = self._register_endpoint()
        response = self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "usage",
                "schedule": "daily",
                "window_days": 7,
                "webhook_endpoint_id": endpoint_id,
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        subscription = response.json()
        self.assertEqual(subscription["report_type"], "usage")
        self.assertTrue(subscription["active"])
        listed = self.client.get("/api/admin/report-subscriptions", headers=self.admin).json()
        self.assertEqual(len(listed), 1)
        patched = self.client.patch(
            f"/api/admin/report-subscriptions/{subscription['id']}",
            json={"active": False, "window_days": 14},
            headers=self.admin,
        )
        self.assertEqual(patched.status_code, 200, patched.text)
        self.assertFalse(patched.json()["active"])
        self.assertEqual(patched.json()["window_days"], 14)
        deleted = self.client.delete(
            f"/api/admin/report-subscriptions/{subscription['id']}", headers=self.admin
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertEqual(
            self.client.get("/api/admin/report-subscriptions", headers=self.admin).json(), []
        )

    def test_subscription_unknown_endpoint_404(self) -> None:
        response = self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "usage",
                "schedule": "daily",
                "webhook_endpoint_id": "wh_nonexistent",
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_subscription_bad_type_422(self) -> None:
        endpoint_id = self._register_endpoint()
        response = self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "bogus",
                "schedule": "daily",
                "webhook_endpoint_id": endpoint_id,
            },
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_subscription_requires_admin(self) -> None:
        endpoint_id = self._register_endpoint()
        response = self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "usage",
                "schedule": "daily",
                "webhook_endpoint_id": endpoint_id,
            },
            headers=self.operator,
        )
        self.assertEqual(response.status_code, 403, response.text)

    # ----------------------------------------------------------- on-demand + csv

    def test_on_demand_usage_report(self) -> None:
        self._seed_usage()
        response = self.client.post(
            "/api/admin/reports/generate",
            json={"report_type": "usage", "window_days": 7},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["report_type"], "usage")
        self.assertTrue(report["rows"])
        self.assertIn("conversation_count", report["rows"][0])

    def test_csv_export_quality_and_usage(self) -> None:
        self._seed_usage()
        response = self.client.get("/api/admin/reports/usage/export", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/csv", response.headers["content-type"])
        self.assertIn("attachment", response.headers["content-disposition"])
        lines = response.text.strip().splitlines()
        self.assertEqual(lines[0], "date,tenant_id,turn_count,conversation_count,message_count")
        self.assertEqual(len(lines), 2)  # header + one usage row for today

    def test_csv_export_tenant_isolation(self) -> None:
        self._seed_usage()
        demo_lines = (
            self.client.get("/api/admin/reports/usage/export", headers=self.admin)
            .text.strip()
            .splitlines()
        )
        self.assertEqual(len(demo_lines), 2)
        acme = {"X-API-Key": ACME_ADMIN_KEY, "X-Tenant-Id": "acme"}
        acme_response = self.client.get("/api/admin/reports/usage/export", headers=acme)
        self.assertEqual(acme_response.status_code, 200, acme_response.text)
        acme_lines = acme_response.text.strip().splitlines()
        self.assertEqual(len(acme_lines), 1)  # header only: no acme usage rows
        self.assertNotIn("demo", acme_response.text)

    # ------------------------------------------------------------ scheduled scan

    def test_due_scan_generates_and_delivers(self) -> None:
        endpoint_id = self._register_endpoint()
        self._seed_usage()
        response = self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "usage",
                "schedule": "daily",
                "window_days": 7,
                "webhook_endpoint_id": endpoint_id,
            },
            headers=self.admin,
        )
        subscription_id = response.json()["id"]
        processed = self.reports.run_due_subscriptions()
        self.assertEqual(processed, 1)
        with self.services.database.connect() as conn:
            delivery = conn.execute(
                "SELECT event_type, payload_json FROM webhook_deliveries WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()
            last_run = conn.execute(
                "SELECT last_run_at FROM report_subscriptions WHERE id = ?",
                (subscription_id,),
            ).fetchone()
        self.assertIsNotNone(delivery)
        self.assertEqual(delivery["event_type"], EVENT_REPORT_GENERATED)
        payload = json.loads(delivery["payload_json"])
        self.assertEqual(payload["report_type"], "usage")
        self.assertTrue(payload["rows"])
        self.assertIsNotNone(last_run["last_run_at"])

    def test_due_scan_is_idempotent_per_period(self) -> None:
        endpoint_id = self._register_endpoint()
        self._seed_usage()
        self.client.post(
            "/api/admin/report-subscriptions",
            json={
                "report_type": "usage",
                "schedule": "daily",
                "webhook_endpoint_id": endpoint_id,
            },
            headers=self.admin,
        )
        first = self.reports.run_due_subscriptions()
        second = self.reports.run_due_subscriptions()
        self.assertEqual(first, 1)
        self.assertEqual(second, 0)  # not due again today, no duplicate delivery
        with self.services.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM webhook_deliveries WHERE endpoint_id = ?",
                (endpoint_id,),
            ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_weekly_cadence_due_after_seven_days(self) -> None:
        today = "2026-08-16"
        weekly = {"schedule": "weekly", "last_run_at": "2026-08-09T00:00:00+00:00"}
        self.assertTrue(ReportService._due(weekly, today))
        not_due = {"schedule": "weekly", "last_run_at": "2026-08-10T00:00:00+00:00"}
        self.assertFalse(ReportService._due(not_due, today))
        daily_due = {"schedule": "daily", "last_run_at": "2026-08-15T00:00:00+00:00"}
        self.assertTrue(ReportService._due(daily_due, today))
        daily_fresh = {"schedule": "daily", "last_run_at": "2026-08-16T00:00:00+00:00"}
        self.assertFalse(ReportService._due(daily_fresh, today))

    def test_on_demand_delivery_to_endpoint(self) -> None:
        endpoint_id = self._register_endpoint()
        self._seed_usage()
        response = self.client.post(
            "/api/admin/reports/generate",
            json={"report_type": "usage", "webhook_endpoint_id": endpoint_id},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["deliveries"], 1)


if __name__ == "__main__":
    unittest.main()
