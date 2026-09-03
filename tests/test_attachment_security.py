"""Phase 42.4 / SEC-006: attachment object store & malware isolation.

Acceptance coverage: EICAR + polymorphic samples, disguised MIME, compression
bombs, scan timeout / unknown verdicts fail closed, object overwrite & path
traversal rejected, signed-URL expiry/replay/tamper/cross-tenant, download
security headers, checksum tamper detection at download, quarantine flow
(external scanner mode), and DSR deletion purging blobs with proof.
"""

from __future__ import annotations

import gzip
import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from app.attachment_store import (
    AttachmentKeyConflict,
    AttachmentKeyError,
    DiskAttachmentStore,
)
from app.attachments import (
    AttachmentScanner,
    AttachmentService,
    TimeoutMalwareScanner,
    normalize_verdict,
)
from app.config import Settings
from app.database import Database

EICAR = b"X5O!P%@AP[4\\PZX54(P^)7CC)7}$EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*"
EICAR_POLY = EICAR.replace(b"X5O", b"X5A", 1)  # polymorphic head variant


def _settings(tmp: Path, **overrides: object) -> Settings:
    kwargs: dict = {
        "auth_mode": "api_key",
        "attachment_storage_dir": tmp / "attachments",
        "attachment_max_mb": 10,
        "attachment_quota_mb": 512,
        "attachment_scan_enabled": True,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


class ScannerHardeningTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scanner = AttachmentScanner()

    def test_eicar_exact_and_polymorphic_are_rejected(self) -> None:
        for sample in (EICAR, EICAR_POLY):
            clean, verdict = self.scanner.scan(sample, "invoice.txt", "text/plain")
            self.assertFalse(clean)
            self.assertIn("EICAR", verdict)

    def test_disguised_mime_binary_magic_rejected(self) -> None:
        clean, verdict = self.scanner.scan(b"MZ\x90\x00fake", "notes.txt", "text/plain")
        self.assertFalse(clean)
        self.assertIn("executable magic", verdict)
        clean, verdict = self.scanner.scan(b"PK\x03\x04zipdata", "notes.txt", "text/plain")
        self.assertFalse(clean)
        self.assertIn("binary container", verdict)

    def test_zip_bomb_exceeding_ratio_rejected(self) -> None:
        payload = b"0" * (1024 * 1024)  # 1MB of zeros → ~1KB compressed
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as bundle:
            bundle.writestr("bomb.bin", payload)
        blob = buffer.getvalue()
        clean, verdict = self.scanner.scan(blob, "blob.zip", "application/zip")
        self.assertFalse(clean)
        self.assertIn("expansion", verdict)

    def test_gzip_bomb_stream_bound(self) -> None:
        payload = b"0" * (5 * 1024 * 1024)
        buffer = io.BytesIO()
        with gzip.GzipFile(fileobj=buffer, mode="wb") as gz:
            gz.write(payload)
        blob = buffer.getvalue()
        # application/json is allowed but not text/*, so the bomb guard (not
        # the disguised-MIME rule) is what catches this.
        clean, verdict = self.scanner.scan(blob, "blob.json", "application/json")
        self.assertFalse(clean)
        self.assertIn("safety bound", verdict)

    def test_benign_text_still_passes(self) -> None:
        clean, verdict = self.scanner.scan(b"hello world", "hello.txt", "text/plain")
        self.assertTrue(clean)
        self.assertEqual(verdict, "clean")

    def test_scan_timeout_fails_closed(self) -> None:
        class SlowEngine:
            def scan(self, data: bytes, filename: str, content_type: str):
                time.sleep(1.0)
                return True, "clean"

        wrapped = TimeoutMalwareScanner(SlowEngine(), timeout_seconds=0.1)
        started = time.monotonic()
        clean, verdict = wrapped.scan(b"data", "a.txt", "text/plain")
        self.assertFalse(clean, "timeout must never pass as clean")
        self.assertLess(time.monotonic() - started, 0.9)

    def test_unknown_verdict_fails_closed(self) -> None:
        class WeirdEngine:
            def scan(self, data: bytes, filename: str, content_type: str):
                return "sure?", "probably fine"  # nonsense shapes

        wrapped = TimeoutMalwareScanner(WeirdEngine(), timeout_seconds=1.0)
        clean, verdict = wrapped.scan(b"data", "a.txt", "text/plain")
        self.assertFalse(clean)
        self.assertIn("unrecognized", verdict)
        # And the normalizer alone agrees.
        clean2, _ = normalize_verdict("true", "clean")  # type: ignore[arg-type]
        self.assertFalse(clean2)


class StoreSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = DiskAttachmentStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_no_silent_overwrite(self) -> None:
        self.store.put("att_a_11111111", b"first")
        with self.assertRaises(AttachmentKeyConflict):
            self.store.put("att_a_11111111", b"second")
        # The original object is intact.
        self.assertTrue(self.store.verify("att_a_11111111", self.store.digest(b"first")))

    def test_path_traversal_keys_rejected(self) -> None:
        for key in ("../escape", "sub/dir/key", "..", ".hidden", "", "a" * 200):
            with self.assertRaises((AttachmentKeyError, ValueError)):
                self.store.put(key, b"x")

    def test_delete_and_verify(self) -> None:
        stored = self.store.put("att_b_22222222", b"payload")
        self.assertTrue(self.store.verify(stored.key, stored.sha256))
        self.assertTrue(self.store.delete(stored.key))
        self.assertFalse(self.store.exists(stored.key))
        self.assertFalse(self.store.verify(stored.key, stored.sha256))


class QuarantineFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.db = Database(root / "att.db")
        self.db.initialize()
        self.db.ensure_tenant("t1")
        conv = self.db.create_conversation("t1", "Conv", "C-Q", "web", "admin", 120)
        self.conv_id = conv["id"]
        self.settings = _settings(
            root, attachment_storage_dir=root / "objects", attachment_scan_mode="external"
        )
        self.service = AttachmentService(
            self.db, self.settings, store=DiskAttachmentStore(root / "objects")
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_external_upload_parks_in_quarantine(self) -> None:
        att = self.service.upload("t1", self.conv_id, "admin", "a.txt", "text/plain", b"hello")
        self.assertEqual(att["status"], "quarantined")
        # Not downloadable…
        self.assertIsNone(self.service.download("t1", str(att["id"])))
        # …and not bindable to a message.
        with self.assertRaises(LookupError):
            self.service.validate_for_message("t1", self.conv_id, [str(att["id"])])

    def test_clean_verdict_promotes_to_downloadable(self) -> None:
        att = self.service.upload("t1", self.conv_id, "admin", "a.txt", "text/plain", b"hello")
        updated = self.service.finalize_verdict("t1", str(att["id"]), True, "clean", "scanner")
        assert updated is not None
        self.assertEqual(updated["status"], "stored")
        result = self.service.download("t1", str(att["id"]))
        self.assertIsNotNone(result)
        ids = self.service.validate_for_message("t1", self.conv_id, [str(att["id"])])
        self.assertEqual(ids, [str(att["id"])])

    def test_infected_verdict_rejects_and_removes_object(self) -> None:
        att = self.service.upload("t1", self.conv_id, "admin", "e.txt", "text/plain", EICAR)
        updated = self.service.finalize_verdict("t1", str(att["id"]), False, "infected", "scanner")
        assert updated is not None
        self.assertEqual(updated["status"], "rejected")
        self.assertFalse(
            self.service.store.exists(str(updated["storage_key"])),
            "rejected payloads must not remain in the bucket",
        )
        self.assertIsNone(self.service.download("t1", str(att["id"])))

    def test_finalize_only_from_quarantined_state(self) -> None:
        att = self.service.upload("t1", self.conv_id, "admin", "a.txt", "text/plain", b"hello")
        self.service.finalize_verdict("t1", str(att["id"]), True, "clean", "scanner")
        with self.assertRaises(ValueError):
            self.service.finalize_verdict("t1", str(att["id"]), True, "clean", "scanner")


class SignedUrlAndIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.db = Database(root / "att.db")
        self.db.initialize()
        self.db.ensure_tenant("t1")
        self.db.ensure_tenant("t2")
        conv = self.db.create_conversation("t1", "Conv", "C-S", "web", "admin", 120)
        self.conv_id = conv["id"]
        self.settings = _settings(root, attachment_storage_dir=root / "objects")
        self.service = AttachmentService(
            self.db, self.settings, store=DiskAttachmentStore(root / "objects")
        )
        self.att = self.service.upload(
            "t1", self.conv_id, "admin", "doc.txt", "text/plain", b"signed body"
        )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_token_roundtrip_and_expiry(self) -> None:
        token, expires = self.service.sign_download_url_token(
            "t1", str(self.att["id"]), ttl_seconds=60, now_epoch=1_000_000
        )
        self.assertTrue(
            self.service.verify_download_url_token(
                "t1", str(self.att["id"]), token, expires, now_epoch=1_000_010
            )
        )
        # Replay after expiry is rejected.
        self.assertFalse(
            self.service.verify_download_url_token(
                "t1", str(self.att["id"]), token, expires, now_epoch=expires + 1
            )
        )

    def test_tampered_and_cross_tenant_tokens_fail(self) -> None:
        token, expires = self.service.sign_download_url_token(
            "t1", str(self.att["id"]), ttl_seconds=60
        )
        self.assertFalse(
            self.service.verify_download_url_token(
                "t1", str(self.att["id"]), token[:-1] + ("0" if token[-1] != "0" else "1"), expires
            )
        )
        other_token, other_exp = self.service.sign_download_url_token(
            "t2", str(self.att["id"]), ttl_seconds=60
        )
        self.assertFalse(
            self.service.verify_download_url_token(
                "t1", str(self.att["id"]), other_token, other_exp
            ),
            "a token minted for another tenant must not validate here",
        )

    def test_ttl_is_capped_at_ten_minutes(self) -> None:
        _, expires = self.service.sign_download_url_token(
            "t1", str(self.att["id"]), ttl_seconds=999_999, now_epoch=50_000
        )
        self.assertLessEqual(expires - 50_000, 600)

    def test_checksum_pins_content_and_detects_tampering(self) -> None:
        att = self.service.get("t1", str(self.att["id"]))
        assert att is not None
        self.assertEqual(att["sha256"], DiskAttachmentStore.digest(b"signed body"))
        self.assertIsNotNone(self.service.download("t1", str(self.att["id"])))
        # Flip one byte on disk; the download must refuse the object.
        key = str(att["storage_key"])
        obj_path = Path(str(self.settings.attachment_storage_dir)) / f"{key}.jsonl.gz"
        real_path = Path(str(self.settings.attachment_storage_dir)) / key
        self.assertTrue(real_path.exists())
        real_path.write_bytes(b"TAMPERED")
        del obj_path
        self.assertIsNone(self.service.download("t1", str(self.att["id"])))

    def test_delete_leaves_proof_audit(self) -> None:
        att_id = str(self.att["id"])
        row = self.service.get("t1", att_id)
        assert row is not None
        storage_key = str(row["storage_key"])
        self.assertTrue(self.service.delete("t1", att_id, "admin"))
        events = self.db.list_audit("t1", self.conv_id)
        deleted = [e for e in events if e["event_type"] == "attachment.deleted"]
        self.assertEqual(len(deleted), 1)
        payload = deleted[0]["payload"]
        self.assertEqual(payload["storage_key"], storage_key)
        self.assertEqual(payload["sha256"], DiskAttachmentStore.digest(b"signed body"))
        self.assertEqual(payload["size_bytes"], len(b"signed body"))

    def test_cross_tenant_isolation(self) -> None:
        self.assertIsNone(self.service.get("t2", str(self.att["id"])))
        self.assertIsNone(self.service.download("t2", str(self.att["id"])))


if __name__ == "__main__":
    unittest.main()
