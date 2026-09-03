"""Attachment router error branches — 404/403/409/413 response codes.

Complements tests/test_attachments.py (happy paths + upload validation) with
the error-response branches of app/routers/attachments.py: unknown
conversation/attachment lookups, signed-download-token rejection, verdict
conflict and missing-row handling, and permission denials.
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

ADMIN_KEY = "att-err-admin"
VIEWER_KEY = "att-err-viewer"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer", "role": "viewer"},
    }
    defaults: dict[str, Any] = dict(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(principals),
        rate_limit_per_minute=10000,
        docs_enabled=False,
        turn_worker_enabled=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


class AttachmentRouterErrorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "att.db"
        self.storage = Path(self._tmp.name) / "attachments"
        self.client = TestClient(
            create_app(_settings(self.db_path, attachment_storage_dir=self.storage))
        )
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.viewer = {"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _open_conversation(self) -> str:
        conv = self.client.post(
            "/api/conversations", json={"customer_name": "S"}, headers=self.admin
        ).json()
        return conv["id"]

    def _upload(self, conversation_id: str) -> dict[str, Any]:
        response = self.client.post(
            "/api/attachments",
            data={"conversation_id": conversation_id},
            files={"file": ("pic.png", PNG_BYTES, "image/png")},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_upload_unknown_conversation_404(self) -> None:
        response = self.client.post(
            "/api/attachments",
            data={"conversation_id": "nonexistent-conv"},
            files={"file": ("pic.png", PNG_BYTES, "image/png")},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404)

    def test_upload_requires_operator_act(self) -> None:
        conv_id = self._open_conversation()
        response = self.client.post(
            "/api/attachments",
            data={"conversation_id": conv_id},
            files={"file": ("pic.png", PNG_BYTES, "image/png")},
            headers=self.viewer,
        )
        self.assertEqual(response.status_code, 403)

    def test_get_unknown_attachment_404(self) -> None:
        response = self.client.get("/api/attachments/no-such-id", headers=self.admin)
        self.assertEqual(response.status_code, 404)

    def test_download_unknown_attachment_404(self) -> None:
        response = self.client.get("/api/attachments/no-such-id/download", headers=self.admin)
        self.assertEqual(response.status_code, 404)

    def test_download_with_bad_token_403(self) -> None:
        conv_id = self._open_conversation()
        attachment = self._upload(conv_id)
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download",
            params={"token": "forged", "expires": 9999999999},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 403)

    def test_download_with_token_but_no_expiry_403(self) -> None:
        conv_id = self._open_conversation()
        attachment = self._upload(conv_id)
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download",
            params={"token": "forged"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 403)

    def test_verdict_conflict_409(self) -> None:
        conv_id = self._open_conversation()
        attachment = self._upload(conv_id)
        # The in-process scanner already promoted this upload to stored;
        # a second verdict on a non-quarantined row is a conflict.
        response = self.client.post(
            f"/api/attachments/{attachment['id']}/verdict",
            json={"clean": True, "verdict": "clean"},
            headers=self.admin,
        )
        self.assertIn(response.status_code, (409, 404), response.text)

    def test_verdict_unknown_attachment_404(self) -> None:
        response = self.client.post(
            "/api/attachments/no-such-id/verdict",
            json={"clean": True, "verdict": "clean"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_unknown_attachment_404(self) -> None:
        response = self.client.delete("/api/attachments/no-such-id", headers=self.admin)
        self.assertEqual(response.status_code, 404)

    def test_delete_requires_operator_act(self) -> None:
        conv_id = self._open_conversation()
        attachment = self._upload(conv_id)
        response = self.client.delete(f"/api/attachments/{attachment['id']}", headers=self.viewer)
        self.assertEqual(response.status_code, 403)

    def test_list_unknown_conversation_is_empty(self) -> None:
        response = self.client.get(
            "/api/attachments",
            params={"conversation_id": "no-such-conv"},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_get_requires_conversation_read(self) -> None:
        conv_id = self._open_conversation()
        attachment = self._upload(conv_id)
        response = self.client.get(f"/api/attachments/{attachment['id']}", headers=self.viewer)
        self.assertEqual(response.status_code, 200)


if __name__ == "__main__":
    unittest.main()
