"""Backlog: rich-media attachments (语音/富媒体消息, feasible subset).

Covers the roadmap acceptance:
- upload stores the file on disk and records the row (type allowlist);
- per-file size cap and per-tenant quota return 413; unsupported types 415;
- the deterministic scanner rejects executables and mismatched signatures;
- list/metadata/download respect tenant isolation; download forces attachment;
- delete removes the file and frees quota;
- operator replies can reference stored attachments (metadata + backfill);
- RBAC: upload/delete require operator:act; reads require conversation:read.
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

ADMIN_KEY = "att-admin-key-001"
VIEWER_KEY = "att-viewer-key-001"

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"},
        VIEWER_KEY: {"tenant_id": "demo", "actor_id": "viewer", "role": "viewer"},
    }
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
        "turn_worker_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class AttachmentAppTests(unittest.TestCase):
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

    def _upload(
        self,
        conversation_id: str,
        filename: str = "pic.png",
        content: bytes = PNG_BYTES,
        content_type: str = "image/png",
        headers: dict[str, str] | None = None,
    ):
        return self.client.post(
            "/api/attachments",
            data={"conversation_id": conversation_id},
            files={"file": (filename, content, content_type)},
            headers=headers or self.admin,
        )

    def _storage_key(self, attachment_id: str) -> str:
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT storage_key FROM attachments WHERE id = ?", (attachment_id,)
            ).fetchone()
        return row["storage_key"]

    # ---------------------------------------------------------------- upload

    def test_upload_stores_and_lists(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(conversation_id)
        self.assertEqual(response.status_code, 201, response.text)
        attachment = response.json()
        self.assertEqual(attachment["status"], "stored")
        self.assertEqual(attachment["verdict"], "clean")
        self.assertEqual(attachment["filename"], "pic.png")
        self.assertTrue(attachment["scanned"])
        self.assertTrue((self.storage / self._storage_key(attachment["id"])).is_file())
        listed = self.client.get(
            f"/api/attachments?conversation_id={conversation_id}", headers=self.admin
        ).json()
        self.assertEqual(len(listed), 1)

    def test_upload_rejects_unsupported_type(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(
            conversation_id, "evil.exe", b"MZ\x90\x00", "application/octet-stream"
        )
        self.assertEqual(response.status_code, 415, response.text)

    def test_upload_rejects_executable_magic_with_image_type(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(
            conversation_id, "fake.png", b"MZ\x90\x00" + b"\x00" * 16, "image/png"
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "rejected")
        self.assertEqual(response.json()["verdict"], "executable magic bytes detected")

    def test_upload_rejects_mismatched_signature(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(conversation_id, "fake.png", b"not a png at all", "image/png")
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["status"], "rejected")

    def test_upload_over_per_file_limit(self) -> None:
        import types

        conversation_id = self._open_conversation()
        original = self.services.attachments.settings
        small = types.SimpleNamespace(
            attachment_max_mb=1,
            attachment_quota_mb=512,
            attachment_scan_enabled=True,
            attachment_storage_dir=self.storage,
        )
        self.services.attachments.settings = small
        try:
            big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (2 * 1024 * 1024)
            response = self._upload(conversation_id, "big.png", big)
            self.assertEqual(response.status_code, 413, response.text)
        finally:
            self.services.attachments.settings = original

    def test_upload_over_tenant_quota(self) -> None:
        conversation_id = self._open_conversation()
        original = self.services.attachments.settings
        # Use a tiny quota by replacing the service settings object's values.
        import types

        small = types.SimpleNamespace(
            attachment_max_mb=10,
            attachment_quota_mb=1,
            attachment_scan_enabled=True,
            attachment_storage_dir=self.storage,
        )
        self.services.attachments.settings = small
        try:
            big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (900 * 1024)
            response = self._upload(conversation_id, "big.png", big)
            self.assertEqual(response.status_code, 201, response.text)
            second = self._upload(conversation_id, "big2.png", big)
            self.assertEqual(second.status_code, 413, second.text)
        finally:
            self.services.attachments.settings = original

    # ------------------------------------------------------------- download

    def test_download_forces_attachment(self) -> None:
        conversation_id = self._open_conversation()
        attachment = self._upload(conversation_id).json()
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download", headers=self.admin
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.content, PNG_BYTES)

    def test_download_non_ascii_filename_uses_rfc6266_encoding(self) -> None:
        # A non-ASCII name must be served via ``filename*=utf-8''`` (RFC 6266),
        # never raw bytes or a hand-built quoted header that could break the
        # Content-Disposition line.
        conversation_id = self._open_conversation()
        attachment = self._upload(
            conversation_id, "报价单.pdf", b"%PDF-1.4 test", "application/pdf"
        ).json()
        self.assertEqual(attachment["status"], "stored")
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download", headers=self.admin
        )
        self.assertEqual(response.status_code, 200, response.text)
        header = response.headers["content-disposition"]
        self.assertIn("attachment", header)
        self.assertIn("filename*=utf-8''", header)
        self.assertNotIn("\r", header)
        self.assertNotIn("\n", header)

    def test_sanitize_filename_strips_header_unsafe_characters(self) -> None:
        from app.attachments import sanitize_filename

        sanitized = sanitize_filename('evil"\r\nX.png')
        self.assertEqual(sanitized, "evilX.png")
        backslash = sanitize_filename('a\\b"c.png')
        self.assertNotIn("\\", backslash)
        self.assertNotIn('"', backslash)
        self.assertEqual(sanitize_filename(""), "attachment")
        self.assertEqual(sanitize_filename("报表 报价单.pdf"), "报表 报价单.pdf")

    def test_upload_removes_orphaned_file_when_row_insert_fails(self) -> None:
        conversation_id = self._open_conversation()
        original_connect = self.services.database.connect
        calls = {"n": 0}

        def flaky_connect():
            calls["n"] += 1
            if calls["n"] >= 2:  # first call (quota) works, insert fails
                raise RuntimeError("simulated insert failure")
            return original_connect()

        self.services.database.connect = flaky_connect
        try:
            with self.assertRaises(RuntimeError):
                self._upload(conversation_id)
        finally:
            self.services.database.connect = original_connect
        self.assertEqual(list(self.storage.glob("*")), [])

    def test_rejected_attachment_not_downloadable(self) -> None:
        conversation_id = self._open_conversation()
        attachment = self._upload(conversation_id, "fake.png", b"not a png", "image/png").json()
        self.assertEqual(attachment["status"], "rejected")
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download", headers=self.admin
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_download_tenant_isolation(self) -> None:
        conversation_id = self._open_conversation()
        attachment = self._upload(conversation_id).json()
        response = self.client.get(
            f"/api/attachments/{attachment['id']}/download",
            headers={"X-API-Key": VIEWER_KEY, "X-Tenant-Id": "other"},
        )
        self.assertEqual(response.status_code, 403, response.text)

    # ------------------------------------------------------------------ delete

    def test_delete_removes_file_and_frees_quota(self) -> None:
        conversation_id = self._open_conversation()
        attachment = self._upload(conversation_id).json()
        storage_key = self._storage_key(attachment["id"])
        before = self.services.attachments.quota_used("demo")
        self.assertGreater(before, 0)
        response = self.client.delete(f"/api/attachments/{attachment['id']}", headers=self.admin)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse((self.storage / storage_key).exists())
        self.assertEqual(self.services.attachments.quota_used("demo"), 0)

    # ----------------------------------------------------- operator messages

    def test_operator_message_with_attachments(self) -> None:
        conversation_id = self._open_conversation()
        self.client.post(f"/api/conversations/{conversation_id}/accept", headers=self.admin)
        attachment = self._upload(conversation_id).json()
        response = self.client.post(
            f"/api/conversations/{conversation_id}/operator-messages",
            json={"content": "请看附件", "attachment_ids": [attachment["id"]]},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 200, response.text)
        message = response.json()
        self.assertEqual(message["metadata"]["attachment_ids"], [attachment["id"]])
        with self.services.database.connect() as conn:
            row = conn.execute(
                "SELECT message_id FROM attachments WHERE id = ?", (attachment["id"],)
            ).fetchone()
        self.assertEqual(row["message_id"], message["id"])

    def test_operator_message_rejects_foreign_attachment(self) -> None:
        conversation_id = self._open_conversation()
        other = self._open_conversation()
        attachment = self._upload(other).json()
        self.client.post(f"/api/conversations/{conversation_id}/accept", headers=self.admin)
        response = self.client.post(
            f"/api/conversations/{conversation_id}/operator-messages",
            json={"content": "错附件", "attachment_ids": [attachment["id"]]},
            headers=self.admin,
        )
        self.assertEqual(response.status_code, 404, response.text)

    # --------------------------------------------------------------------- RBAC

    def test_viewer_denied_upload_but_can_read(self) -> None:
        conversation_id = self._open_conversation()
        response = self._upload(conversation_id, headers=self.viewer)
        self.assertEqual(response.status_code, 403, response.text)
        attachment = self._upload(conversation_id).json()
        listed = self.client.get(
            f"/api/attachments?conversation_id={conversation_id}", headers=self.viewer
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        meta = self.client.get(f"/api/attachments/{attachment['id']}", headers=self.viewer)
        self.assertEqual(meta.status_code, 200, meta.text)
        deleted = self.client.delete(f"/api/attachments/{attachment['id']}", headers=self.viewer)
        self.assertEqual(deleted.status_code, 403, deleted.text)


if __name__ == "__main__":
    unittest.main()
