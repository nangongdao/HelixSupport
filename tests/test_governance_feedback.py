"""Online feedback → eval dataset pipeline tests (ROADMAP 2.6.0).

The 43.5 governance loop closes in production: a negative customer rating
auto-stages the exchange into the governance registry (redacted at ingest,
pending human review, exactly-once per flip), operators review the queue
through the governance API, and accepted rows fold into a new eval dataset
version — with pending/rejected ride-alongs aborting the promotion.
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

ADMIN_KEY = "feedback-admin-key-01"
VIEWER_KEY = "feedback-viewer-key-01"


def _principals() -> dict[str, Any]:
    return {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "fb.admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "fb.viewer", "role": "viewer"},
    }


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(_principals()),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )


class OnlineFeedbackPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.client = TestClient(create_app(_settings(Path(self._tmp.name) / "fb.db")))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}
        # A knowledge article so the turn answers from the knowledge agent
        # (deterministic assistant reply to rate against).
        self.services.database.create_knowledge(
            "demo",
            "配送时效",
            "首单配送时效承诺为 48 小时，偏远地区顺延两个工作日。",
            ["配送"],
            "shipping",
            "",
        )

    def tearDown(self) -> None:
        self.services.database.close()
        self._tmp.cleanup()

    def _rate_assistant_message(self, rating: int, reason: str = "") -> str:
        conversation = self.client.post(
            "/api/conversations",
            headers=self.admin,
            json={"customer_name": f"客户-{self.id()[-4:]}", "channel": "web"},
        ).json()
        conversation_id = conversation["id"]
        send = self.client.post(
            f"/api/conversations/{conversation_id}/messages",
            headers=self.admin,
            json={"content": "配送一般多久能到？"},
        )
        assert send.status_code == 200, send.text
        messages = self.client.get(
            f"/api/conversations/{conversation_id}/messages", headers=self.admin
        ).json()
        assistant = next(m for m in messages if m["role"] == "assistant")
        feedback = self.client.post(
            f"/api/conversations/{conversation_id}/feedback",
            headers=self.admin,
            json={
                "message_id": assistant["id"],
                "rating": rating,
                "reason": reason,
            },
        )
        assert feedback.status_code == 200, feedback.text
        return conversation_id

    def _pending(self) -> list[dict[str, Any]]:
        response = self.client.get(
            "/api/admin/governance/feedback?status=pending_review", headers=self.admin
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_negative_rating_flip_auto_stages_redacted(self) -> None:
        conversation_id = self._rate_assistant_message(-1, "答非所问")
        pending = self._pending()
        self.assertEqual(len(pending), 1)
        row = pending[0]
        self.assertEqual(row["conversation_id"], conversation_id)
        self.assertEqual(row["review_status"], "pending_review")
        # The staged document is the REDACTED one — the raw customer text is
        # only reachable through the redaction-verified stored copy.
        document = json.loads(row["redacted_json"])
        self.assertEqual(document["rating"], -1)

    def test_exactly_once_per_flip(self) -> None:
        conversation_id = self._rate_assistant_message(-1, "第一次")
        # Re-submitting the same -1 must not duplicate the staged row.
        messages = self.client.get(
            f"/api/conversations/{conversation_id}/messages", headers=self.admin
        ).json()
        assistant = next(m for m in messages if m["role"] == "assistant")
        again = self.client.post(
            f"/api/conversations/{conversation_id}/feedback",
            headers=self.admin,
            json={"message_id": assistant["id"], "rating": -1, "reason": "重复"},
        )
        self.assertEqual(again.status_code, 200)
        self.assertEqual(len(self._pending()), 1)

    def test_positive_rating_never_stages(self) -> None:
        self._rate_assistant_message(1, "很好")
        self.assertEqual(self._pending(), [])

    def test_review_then_promote_into_dataset(self) -> None:
        self._rate_assistant_message(-1, "需要复核")
        pending = self._pending()
        feedback_id = pending[0]["id"]
        reviewed = self.client.post(
            f"/api/admin/governance/feedback/{feedback_id}/review",
            headers=self.admin,
            json={"accept": True},
        )
        self.assertEqual(reviewed.status_code, 200, reviewed.text)
        self.assertEqual(reviewed.json()["review_status"], "accepted")

        promoted = self.client.post(
            "/api/admin/governance/datasets/promote-feedback",
            headers=self.admin,
            json={"dataset_name": "browser-feedback", "feedback_ids": [feedback_id]},
        )
        self.assertEqual(promoted.status_code, 200, promoted.text)
        dataset = promoted.json()
        self.assertEqual(dataset["strategy"], "feedback")

        datasets = self.client.get("/api/admin/governance/datasets", headers=self.admin).json()
        self.assertEqual(len(datasets), 1)
        items = self.client.get(
            f"/api/admin/governance/datasets/{dataset['id']}/items", headers=self.admin
        ).json()
        self.assertEqual(len(items), 1)

    def test_promotion_aborts_on_pending_ride_along(self) -> None:
        # Two staged rows: accept one, leave the other pending — the batch
        # must abort so unreviewed material cannot ride along.
        self._rate_assistant_message(-1, "第一条")
        self._rate_assistant_message(-1, "第二条")
        pending = self._pending()
        self.assertGreaterEqual(len(pending), 2)
        accepted_id = pending[0]["id"]
        pending_id = pending[1]["id"]
        self.client.post(
            f"/api/admin/governance/feedback/{accepted_id}/review",
            headers=self.admin,
            json={"accept": True},
        )
        aborted = self.client.post(
            "/api/admin/governance/datasets/promote-feedback",
            headers=self.admin,
            json={
                "dataset_name": "browser-feedback",
                "feedback_ids": [accepted_id, pending_id],
            },
        )
        self.assertEqual(aborted.status_code, 409, aborted.text)
        datasets = self.client.get("/api/admin/governance/datasets", headers=self.admin).json()
        self.assertEqual(datasets, [])

    def test_review_and_promote_rbac(self) -> None:
        self._rate_assistant_message(-1, "权限")
        feedback_id = self._pending()[0]["id"]
        forbidden_review = self.client.post(
            f"/api/admin/governance/feedback/{feedback_id}/review",
            headers=self.viewer,
            json={"accept": True},
        )
        self.assertEqual(forbidden_review.status_code, 403)
        forbidden_promote = self.client.post(
            "/api/admin/governance/datasets/promote-feedback",
            headers=self.viewer,
            json={"dataset_name": "x", "feedback_ids": [feedback_id]},
        )
        self.assertEqual(forbidden_promote.status_code, 403)


if __name__ == "__main__":
    unittest.main()
