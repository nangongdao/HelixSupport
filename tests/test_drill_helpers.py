"""Gate B drill pure-component tests (Phase 41 / 1.4 code-review follow-up).

The end-to-end drills (run_rotation/restore/patch_drill.py) exercise the real
HTTP/subprocess path; these tests pin the *pure* helpers a drill depends on
so a future edit cannot silently weaken their guarantees:

- ``run_patch_drill._simulate_sign`` is deterministic for one manifest and
  changes when the manifest OR the nonce changes (the property an image
  tag-swap rollback relies on to detect a tampered artifact).
- ``run_restore_drill._seed_db_anchors`` records one frontier tip per
  high-risk event type and returns the recomputed chain hash for each, so the
  signed WORM claims match the chain (a placeholder hash would fail every check).
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_patch_drill import _simulate_sign
from scripts.run_restore_drill import _seed_db_anchors


class SimulatedSignTests(unittest.TestCase):
    def test_deterministic_for_same_manifest_and_nonce(self) -> None:
        payload = {"a": 1, "b": 2}
        self.assertEqual(_simulate_sign(payload, "n"), _simulate_sign(payload, "n"))

    def test_manifest_change_changes_signature(self) -> None:
        payload = {"a": 1, "b": 2}
        self.assertNotEqual(
            _simulate_sign({**payload, "a": 99}, "n"), _simulate_sign(payload, "n")
        )

    def test_nonce_change_changes_signature(self) -> None:
        payload = {"a": 1, "b": 2}
        self.assertNotEqual(_simulate_sign(payload, "n"), _simulate_sign(payload, "n2"))

    def test_signature_is_hex_digest(self) -> None:
        sig = _simulate_sign({"a": 1}, "n")
        self.assertEqual(len(sig), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in sig))


class SeedDbAnchorsTests(unittest.TestCase):
    HIGH_RISK = {"api_key.issued", "api_key.revoked", "member.invited"}

    def _seeded_connection(self, tmp: Path) -> sqlite3.Connection:
        conn = sqlite3.connect(tmp / "drill.db")
        conn.row_factory = sqlite3.Row
        conn.execute(
            "CREATE TABLE audit_anchors ("
            "anchor_id TEXT PRIMARY KEY, seq INTEGER, chain_hash TEXT, "
            "event_type TEXT, reason TEXT, created_at TEXT)"
        )
        conn.execute(
            "CREATE TABLE audit_events ("
            "id TEXT PRIMARY KEY, seq INTEGER, event_type TEXT, event_hash TEXT)"
        )
        events = [
            ("e1", 1, "conversation.created", "hash1"),
            ("e2", 2, "api_key.issued", "hash2"),
            ("e3", 3, "message.created", "hash3"),
            ("e4", 4, "member.invited", "hash4"),
        ]
        conn.executemany(
            "INSERT INTO audit_events (id, seq, event_type, event_hash) VALUES (?,?,?,?)",
            events,
        )
        conn.commit()
        return conn

    def test_records_one_tip_per_high_risk_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._seeded_connection(Path(tmp))
            try:
                _seed_db_anchors(conn)
                rows = conn.execute("SELECT seq, event_type FROM audit_anchors ORDER BY seq").fetchall()
                self.assertEqual(
                    [r["seq"] for r in rows], [2, 4], "one anchor per high-risk event"
                )
                self.assertTrue(
                    all(r["event_type"] in self.HIGH_RISK for r in rows)
                )
            finally:
                conn.close()

    def test_returns_recomputed_chain_hash_per_seq(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = self._seeded_connection(Path(tmp))
            try:
                hashes = _seed_db_anchors(conn)
                # Returns a mapping seq -> event_hash for EVERY event, so the
                # caller can sign WORM claims that match the actual chain.
                self.assertEqual(set(hashes.keys()), {1, 2, 3, 4})
                self.assertEqual(hashes[2], "hash2")
                self.assertEqual(hashes[4], "hash4")
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()