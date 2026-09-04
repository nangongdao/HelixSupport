"""Backlog: CSAT satisfaction survey.

Covers: resolution issues a one-time survey token exposed on the resolve
response and the ``conversation.resolved`` webhook; the public endpoint
accepts a rating exactly once (atomically); the browser landing page
validates the token and renders a form; ratings reflow into feedback;
invalid/expired tokens are rejected.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

import app.webhooks as webhooks_module
from app.config import Settings
from app.main import create_app
from app.webhooks import EVENT_CONVERSATION_RESOLVED

ADMIN_KEY = "csat-admin-key-001"


def _settings(db_path: Path) -> Settings:
    principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class CsatSurveyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "csat.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _resolve_conversation(self, name: str = "C") -> tuple[str, str]:
        """Resolve a fresh conversation; returns (id, survey token)."""
        conv = self.client.post(
            "/api/conversations", json={"customer_name": name}, headers=self.admin
        ).json()
        self.client.post(
            f"/api/conversations/{conv['id']}/messages",
            json={"content": "ORD-10482 到哪了"},
            headers={**self.admin, "Idempotency-Key": f"csat-t-{name}"},
        )
        response = self.client.post(f"/api/conversations/{conv['id']}/resolve", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertIsNotNone(body.get("survey_url"), "resolve response must carry survey_url")
        token = body["survey_url"].rsplit("/", 1)[-1]
        return conv["id"], token

    def test_resolve_creates_survey_and_one_time_rating(self) -> None:
        conversation_id, token = self._resolve_conversation("one")
        response = self.client.post(f"/api/csat/{token}", json={"rating": 5})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["conversation_id"], conversation_id)
        # One-time: second submission is rejected.
        second = self.client.post(f"/api/csat/{token}", json={"rating": 1})
        self.assertEqual(second.status_code, 404)

    def test_invalid_token_rejected(self) -> None:
        response = self.client.post("/api/csat/no-such-token", json={"rating": 3})
        self.assertEqual(response.status_code, 404)

    def test_rating_reflows_to_feedback(self) -> None:
        _, token = self._resolve_conversation("reflow")
        self.client.post(f"/api/csat/{token}", json={"rating": 5})
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT rating, reason FROM feedback WHERE actor='csat' LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        # Rating >= 4 maps to positive feedback (1).
        self.assertEqual(row["rating"], 1)
        self.assertEqual(row["reason"], "CSAT 5/5")

    def test_resolved_webhook_carries_survey_url(self) -> None:
        # The registration path validates the host against real DNS; our test
        # host cannot resolve, so relax the check for this single call and let
        # the worker stay disabled so nothing actually POSTs to the network.
        original = webhooks_module.assert_public_webhook_url
        webhooks_module.assert_public_webhook_url = lambda *args, **kwargs: None
        try:
            endpoint = self.client.post(
                "/api/webhooks",
                json={
                    "url": "https://example.invalid/hook",
                    "events": [EVENT_CONVERSATION_RESOLVED],
                    "secret": "secret-123456",
                },
                headers=self.admin,
            )
        finally:
            webhooks_module.assert_public_webhook_url = original
        self.assertIn(endpoint.status_code, (201, 200), endpoint.text)
        conversation_id, token = self._resolve_conversation("wh")
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT payload_json FROM webhook_deliveries "
                "WHERE event_type = ? ORDER BY created_at DESC LIMIT 1",
                (EVENT_CONVERSATION_RESOLVED,),
            ).fetchone()
        self.assertIsNotNone(row, "resolved webhook delivery should be enqueued")
        payload = json.loads(row["payload_json"])
        self.assertEqual(payload["survey_url"], f"/api/csat/{token}")
        self.assertEqual(payload["conversation_id"], conversation_id)

    def test_browser_landing_page_validates_token(self) -> None:
        _, token = self._resolve_conversation("page")
        page = self.client.get(f"/api/csat/{token}")
        self.assertEqual(page.status_code, 200)
        self.assertIn("请为本次服务评分", page.text)
        # After use, the same link shows the invalid message, not the form.
        self.client.post(f"/api/csat/{token}", json={"rating": 4})
        stale = self.client.get(f"/api/csat/{token}")
        self.assertIn("链接已失效", stale.text)
        # Unknown tokens render the invalid message too.
        unknown = self.client.get("/api/csat/no-such-token")
        self.assertIn("链接已失效", unknown.text)

    def test_browser_form_submission_returns_thank_you(self) -> None:
        _, token = self._resolve_conversation("form")
        response = self.client.post(
            f"/api/csat/{token}",
            data={"rating": "5"},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("感谢您的评价", response.text)
        # The rating still landed in feedback.
        with self.services.database.connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM feedback WHERE actor='csat'"
            ).fetchone()["n"]
        self.assertEqual(count, 1)

    def test_atomic_submission_single_row(self) -> None:
        """The guarded UPDATE means an already-answered token can never
        re-answer: concurrent-style double submission reflows once."""
        _, token = self._resolve_conversation("atomic")
        first = self.services.database.submit_csat_rating(token, 5)
        self.assertIsNotNone(first)
        second = self.services.database.submit_csat_rating(token, 1)
        self.assertIsNone(second, "answered token must not be re-answered")
        with self.services.database.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM feedback").fetchone()["n"]
        self.assertEqual(count, 0)

    def test_absolute_survey_url_when_base_configured(self) -> None:
        principals = {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            csat_base_url="https://support.example.com",
            rate_limit_per_minute=10000,
            docs_enabled=False,
            turn_worker_enabled=False,
        )
        with TestClient(create_app(settings)) as client:
            services = cast(Any, client.app).state.services
            admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
            conv = client.post(
                "/api/conversations", json={"customer_name": "abs"}, headers=admin
            ).json()
            client.post(
                f"/api/conversations/{conv['id']}/messages",
                json={"content": "ORD-10482 到哪了"},
                headers={**admin, "Idempotency-Key": "csat-t-abs"},
            )
            response = client.post(f"/api/conversations/{conv['id']}/resolve", headers=admin)
            url = response.json()["survey_url"]
            self.assertTrue(url.startswith("https://support.example.com/api/csat/"))
            services.database.close()


if __name__ == "__main__":
    unittest.main()
