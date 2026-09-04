"""Audit-gap tracking and same-transaction anchoring (Phase 41.3, SEC-005).

Covers the observable ``audit_gap`` surface (ordinary telemetry append
failures must leave a durable gap row plus an in-process counter) and the
fail-closed high-risk path (a security/permission/credential/DSR mutation
must never appear to succeed while its audit evidence is lost). The two
guarantees are unit-tested from the tracker up, then one integration case
proves the anchor row and the gap handshake land in the same transaction.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.audit_gap import (
    AuditGapTracker,
    AuditUnavailableError,
    audit_high_risk,
    clear_gaps,
    list_gaps,
    merge_gap_rows,
    record_audit_gap,
)
from app.database import Database
from app.db.audit import HIGH_RISK_EVENT_TYPES


def _fresh_database(root: Path, name: str = "gap.db") -> Database:
    database = Database(root / name)
    database.initialize()
    database.ensure_tenant("demo")
    return database


class AuditGapTrackerTests(unittest.TestCase):
    def test_record_gap_persists_row_and_counters(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        database = _fresh_database(Path(root.name))
        self.addCleanup(database.close)

        record_audit_gap(database, tracker := AuditGapTracker(), event_type="turn.persist_failed")
        record_audit_gap(database, tracker, event_type="turn.persist_failed")

        self.assertEqual(tracker.local_counts["turn.persist_failed"], 2)
        rows = list_gaps(database)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event_type"], "turn.persist_failed")
        self.assertEqual(rows[0]["gap_count"], 2)

    def test_high_risk_event_gap_is_swallowed(self) -> None:
        # High-risk mutations anchor atomically; a separate gap row would be
        # a lie because the audit could still land in the same transaction.
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        database = _fresh_database(Path(root.name))
        self.addCleanup(database.close)
        tracker = AuditGapTracker()

        record_audit_gap(database, tracker, event_type=next(iter(HIGH_RISK_EVENT_TYPES)))

        self.assertEqual(tracker.local_counts, {})
        self.assertEqual(list_gaps(database), [])

    def test_merge_gap_rows_with_local_counts(self) -> None:
        rows = [{"event_type": "turn.persist_failed", "gap_count": 2, "last_seen_at": "t1"}]
        local = {"turn.persist_failed": 3, "other_gap": 1}
        merged = merge_gap_rows(rows, local)
        by_type = {row["event_type"]: row for row in merged}
        self.assertEqual(by_type["turn.persist_failed"]["gap_count"], 5)
        self.assertEqual(by_type["other_gap"]["gap_count"], 1)
        self.assertEqual(by_type["other_gap"]["last_seen_at"], "")

    def test_clear_gaps_empties_table(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        database = _fresh_database(Path(root.name))
        self.addCleanup(database.close)
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO audit_gaps (event_type, gap_count, last_seen_at) VALUES (?, 1, ?)",
                ("telemetry.drop", "2026-09-01T00:00:00Z"),
            )

        clear_gaps(database)
        self.assertEqual(list_gaps(database), [])


class AuditHighRiskTests(unittest.TestCase):
    def test_high_risk_mutation_anchors_tip(self) -> None:
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        database = _fresh_database(Path(root.name))
        self.addCleanup(database.close)

        event_id = audit_high_risk(
            database,
            tenant_id="demo",
            conversation_id=None,
            actor="admin",
            event_type="credential.rotated",
            payload={"key_id": "k1"},
            reason="quarterly rotation",
        )
        self.assertTrue(event_id.startswith("evt_"))

        with database.connect() as connection:
            anchors = connection.execute(
                "SELECT anchor_id, event_type, reason FROM audit_anchors"
            ).fetchall()
            self.assertEqual(len(anchors), 1)
            self.assertEqual(anchors[0]["event_type"], "credential.rotated")
            self.assertEqual(anchors[0]["reason"], "quarterly rotation")

    def test_anchor_rolls_back_with_transaction(self) -> None:
        # A failing audit append must abort the whole mutation — the anchor
        # row must not survive when the audit row does not. A payload that
        # cannot be JSON-serialized makes audit_in_transaction raise.
        root = tempfile.TemporaryDirectory()
        self.addCleanup(root.cleanup)
        database = _fresh_database(Path(root.name))
        self.addCleanup(database.close)

        with self.assertRaises(AuditUnavailableError):
            audit_high_risk(
                database,
                tenant_id="demo",
                conversation_id=None,
                actor="admin",
                event_type="credential.rotated",
                payload={"bad": object()},
                reason="rotation",
            )
        with database.connect() as connection:
            anchors = connection.execute("SELECT COUNT(*) FROM audit_anchors").fetchone()
            self.assertEqual(anchors[0], 0)


if __name__ == "__main__":
    unittest.main()
