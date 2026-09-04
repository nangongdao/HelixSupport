"""Phase 42.3 / REL-002: archive object store — compressed partitions.

Contracts under test: gzip JSONL partitions with per-tenant manifests,
time-range iteration with hard limits, streaming reads with bounded memory,
and fail-closed integrity (missing object / tampered bytes are rejected
before any record is yielded).
"""

from __future__ import annotations

import tempfile
import tracemalloc
import unittest
from pathlib import Path

from app.archive_store import ArchiveIntegrityError, ArchiveObjectStore


def _records(count: int, *, day: str = "2026-08-21", size: int = 64) -> list[dict[str, str]]:
    return [
        {
            "id": f"rec-{index:06d}",
            "created_at": f"{day}T00:00:{index % 60:02d}.{index:06d}+00:00",
            "payload": ("x" * size) + str(index),
        }
        for index in range(1, count + 1)
    ]


class ArchiveObjectStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = ArchiveObjectStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roundtrip_preserves_order_and_compresses(self) -> None:
        records = _records(100)
        entry = self.store.put_partition(
            "t1",
            partition_key="2026-08-21",
            records=records,
            created_at="2026-08-21T00:00:00+00:00",
        )
        self.assertEqual(entry.record_count, 100)
        self.assertTrue(entry.content_sha256)
        object_path = Path(self._tmp.name) / "t1" / f"{entry.object_id}.jsonl.gz"
        self.assertTrue(object_path.exists())
        # On-disk bytes really are gzip.
        with object_path.open("rb") as handle:
            self.assertEqual(handle.read(2)[:1], b"\x1f", "gzip magic missing")
        roundtripped = list(self.store.iter_records("t1", entry.object_id))
        self.assertEqual(roundtripped, records)

    def test_manifest_tracks_entries_and_range_iteration(self) -> None:
        day1 = _records(5, day="2026-08-20")
        day2 = _records(5, day="2026-08-21")
        self.store.put_partition(
            "t1", partition_key="2026-08-20", records=day1, created_at="2026-08-20T00:00:00+00:00"
        )
        self.store.put_partition(
            "t1", partition_key="2026-08-21", records=day2, created_at="2026-08-21T00:00:00+00:00"
        )
        everything = list(self.store.iter_range("t1"))
        self.assertEqual(len(everything), 10)
        windowed = list(self.store.iter_range("t1", from_dt="2026-08-21", to_dt="2026-08-21"))
        self.assertEqual(len(windowed), 5)
        limited = list(self.store.iter_range("t1", limit=3))
        self.assertEqual(len(limited), 3, "limit must cap total streamed records")

    def test_missing_object_fails_closed(self) -> None:
        entry = self.store.put_partition(
            "t1", partition_key="2026-08-21", records=_records(3), created_at="now"
        )
        (Path(self._tmp.name) / "t1" / f"{entry.object_id}.jsonl.gz").unlink()
        with self.assertRaises(ArchiveIntegrityError):
            list(self.store.iter_records("t1", entry.object_id))
        with self.assertRaises(ArchiveIntegrityError):
            self.store.verify("t1")

    def test_tampered_object_fails_closed_before_first_record(self) -> None:
        entry = self.store.put_partition(
            "t1", partition_key="2026-08-21", records=_records(20), created_at="now"
        )
        path = Path(self._tmp.name) / "t1" / f"{entry.object_id}.jsonl.gz"
        blob = bytearray(path.read_bytes())
        blob[-5] ^= 0xFF  # flip bits inside the payload region
        path.write_bytes(bytes(blob))
        with self.assertRaises(ArchiveIntegrityError):
            list(self.store.iter_records("t1", entry.object_id))

    def test_verify_reports_clean_state(self) -> None:
        self.store.put_partition(
            "t1",
            partition_key="2026-08-20",
            records=_records(4, day="2026-08-20"),
            created_at="now",
        )
        self.store.put_partition(
            "t1",
            partition_key="2026-08-21",
            records=_records(6, day="2026-08-21"),
            created_at="now",
        )
        stats = self.store.verify("t1")
        self.assertEqual(stats, {"partitions": 2, "records": 10})

    def test_streaming_read_is_bounded_memory(self) -> None:
        # ~5k records x ~1KB ≈ 5MB uncompressed; a streaming reader must not
        # materialise anything close to the full partition.
        records = _records(5_000, size=900)
        entry = self.store.put_partition(
            "t1", partition_key="2026-08-21", records=records, created_at="now"
        )
        tracemalloc.start()
        try:
            count = sum(1 for _line in self.store.iter_lines("t1", entry.object_id))
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        self.assertEqual(count, 5_000)
        # The whole stream is ~5MB; a bounded reader stays far below it.
        self.assertLess(peak, 2 * 1024 * 1024, f"streaming peak {peak} bytes too high")


if __name__ == "__main__":
    unittest.main()
