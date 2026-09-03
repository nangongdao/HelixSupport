"""Phase 42.3 / REL-002: audit-retention archives on the object store.

With an ``ArchiveObjectStore`` configured, ``RetentionService`` writes each
archived audit batch as a compressed JSONL partition and keeps only a slim
manifest in the database (migration v33 columns). Reads stream the payload
through the incremental chain validator; a missing or tampered object fails
closed. The legacy inline-JSON path stays the default when no store is set.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.archive_store import ArchiveIntegrityError, ArchiveObjectStore
from app.database import Database
from app.retention import RetentionService


class AuditArchiveObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.db = Database(root / "archive.db")
        self.db.initialize()
        self.db.ensure_tenant("test-tenant")
        self.store = ArchiveObjectStore(root / "objects")
        # Production wiring (create_app) attaches the store to the database
        # so the read path can stream payloads.
        self.db.archive_object_store = self.store
        self.service = RetentionService(self.db, archive_object_store=self.store)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _archive_two_events(self) -> None:
        self.db.audit("test-tenant", None, "admin", "retention.one", {"safe": True})
        self.db.audit("test-tenant", None, "admin", "retention.two", {"safe": True})
        deleted = self.service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        self.assertEqual(deleted, 2)

    def _row(self) -> dict:
        with self.db.connect() as connection:
            row = connection.execute(
                "SELECT * FROM audit_archives WHERE tenant_id = ?",
                ("test-tenant",),
            ).fetchone()
        assert row is not None
        return dict(row)

    def test_archive_writes_partition_and_slim_manifest(self) -> None:
        self._archive_two_events()
        row = self._row()
        self.assertTrue(row["object_key"], "object rows must reference a partition")
        self.assertEqual(row["archive_json"], "", "inline payload must stay empty")
        self.assertEqual(row["content_sha256"], row["object_sha256"])
        self.assertGreater(int(row["object_bytes"]), 0)
        # Hot rows are gone; the manifest lists exactly one partition.
        with self.db.connect() as connection:
            hot = connection.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE tenant_id = 'test-tenant'"
            ).fetchone()[0]
        self.assertEqual(hot, 0)
        self.assertEqual(len(list(self.store.iter_range("test-tenant"))), 3)  # header + 2 events

    def test_read_streams_back_identical_events(self) -> None:
        self._archive_two_events()
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        archive = self.db.get_audit_archive("test-tenant", archive_id)
        assert archive is not None
        events = archive["events"]
        self.assertEqual(
            [event["event_type"] for event in events], ["retention.one", "retention.two"]
        )
        self.assertEqual([int(event["seq"]) for event in events], [1, 2])

    def test_tampered_object_fails_closed_on_read(self) -> None:
        self._archive_two_events()
        row = self._row()
        object_path = (
            Path(self._tmp.name) / "objects" / "test-tenant" / f"{row['object_key']}.jsonl.gz"
        )
        blob = bytearray(object_path.read_bytes())
        blob[-3] ^= 0x5A
        object_path.write_bytes(bytes(blob))
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        with self.assertRaises(ArchiveIntegrityError):
            self.db.get_audit_archive("test-tenant", archive_id)

    def test_missing_object_fails_closed_on_read(self) -> None:
        self._archive_two_events()
        row = self._row()
        (
            Path(self._tmp.name) / "objects" / "test-tenant" / f"{row['object_key']}.jsonl.gz"
        ).unlink()
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        with self.assertRaises(ArchiveIntegrityError):
            self.db.get_audit_archive("test-tenant", archive_id)

    def test_legacy_inline_path_unchanged_without_store(self) -> None:
        legacy_service = RetentionService(self.db)
        self.db.audit("test-tenant", None, "admin", "retention.legacy", {"safe": True})
        deleted = legacy_service.enforce_retention("test-tenant", "audit_events", retention_days=-1)
        self.assertEqual(deleted, 1)
        row = self._row()
        self.assertIsNone(row["object_key"])
        self.assertTrue(row["archive_json"], "legacy rows keep the inline payload")
        archive_id = self.db.list_audit_archives("test-tenant")[0]["id"]
        archive = self.db.get_audit_archive("test-tenant", archive_id)
        assert archive is not None
        self.assertEqual(archive["events"][0]["event_type"], "retention.legacy")


if __name__ == "__main__":
    unittest.main()
