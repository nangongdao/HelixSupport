"""Phase 42.1 part B: lease fencing token (fresh-lease completion).

A worker whose lease expired must not be able to ``complete`` or ``fail`` its
job, *even when no peer has re-claimed it*. A late completion past an expired
lease could clobber a job that recovery already re-dispatched (or was about
to), so the DB layer rejects the mutation: ``AND locked_at > now - lease``
gates the ownership ``WHERE locked_by = ?`` guard. The tests fake an expired
lease by rewinding ``locked_at`` directly (deterministic, no real ``sleep``)
and assert each path:

- fresh lease completes / fails legitimately,
- expired-lease complete is refused (rowcount 0),
- expired-lease fail is refused (returns None),
- the refusal leaves the job ``processing`` (not silently completed/failed),
- a wrong owner is still refused (defense-in-depth with the lease guard), and
- an invalid ``lease_seconds`` is rejected upfront.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.queue import SQLiteTaskQueue

ADMIN_KEY = "fencing-admin-key-0001"
LEASE = 300


def _settings(db_path: Path) -> Settings:
    return Settings(
        database_path=db_path,
        auth_mode="api_key",
        api_keys_json=json.dumps(
            {ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin", "role": "admin"}}
        ),
        rate_limit_per_minute=10000,
        docs_enabled=False,
    )


class LeaseFencingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "fencing.db"
        self.database = Database(self.db_path)
        self.database.initialize()
        self.database.ensure_tenant("demo")
        self.conv = self.database.create_conversation(
            "demo", "Fencing C", None, "web", "admin", 120
        )["id"]
        self.queue = SQLiteTaskQueue(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _claim(self, worker_id: str, lease_seconds: int = LEASE) -> dict:
        job, _ = self.database.enqueue_turn_job(
            "demo", self.conv, f"key-{worker_id}-{LEASE}", "admin", "hi", 3
        )
        claimed = self.database.claim_next_turn_job(worker_id, lease_seconds)
        assert claimed is not None
        self.assertEqual(claimed["id"], job["id"])
        return dict(claimed)

    def _rewind_lease(self, job_id: str, hours_ago: int = 1) -> None:
        """Force ``locked_at`` into the past so the lease reads as expired."""
        stale = (datetime.now(UTC) - timedelta(hours=hours_ago)).isoformat(timespec="microseconds")
        with self.database.connect() as conn:
            conn.execute("UPDATE turn_jobs SET locked_at = ? WHERE id = ?", (stale, job_id))

    def test_fresh_lease_complete_succeeds(self) -> None:
        claimed = self._claim("w-fresh")
        ok = self.queue.complete(claimed["id"], "w-fresh", {"result": "ok"}, LEASE)
        self.assertTrue(ok)
        after = self.database.get_turn_job("demo", claimed["id"])
        assert after is not None
        self.assertEqual(after["status"], "completed")

    def test_expired_lease_complete_is_refused(self) -> None:
        claimed = self._claim("w-stale")
        self._rewind_lease(claimed["id"])
        ok = self.queue.complete(claimed["id"], "w-stale", {"result": "late"}, LEASE)
        self.assertFalse(ok)
        after = self.database.get_turn_job("demo", claimed["id"])
        assert after is not None
        # No silent completion: the job stays processing with the stale owner.
        self.assertEqual(after["status"], "processing")
        self.assertEqual(after["locked_by"], "w-stale")

    def test_fresh_lease_fail_succeeds(self) -> None:
        claimed = self._claim("w-fresh-fail")
        result = self.queue.fail(
            claimed["id"],
            "w-fresh-fail",
            "ConnectorUnavailable",
            0,
            LEASE,
            retryable=False,
        )
        assert result is not None
        self.assertEqual(result["status"], "failed")

    def test_expired_lease_fail_is_refused(self) -> None:
        claimed = self._claim("w-stale-fail")
        self._rewind_lease(claimed["id"])
        result = self.queue.fail(
            claimed["id"],
            "w-stale-fail",
            "ConnectorUnavailable",
            0,
            LEASE,
            retryable=False,
        )
        self.assertIsNone(result)
        after = self.database.get_turn_job("demo", claimed["id"])
        assert after is not None
        self.assertEqual(after["status"], "processing")
        self.assertEqual(after["locked_by"], "w-stale-fail")

    def test_wrong_owner_complete_is_refused(self) -> None:
        claimed = self._claim("w-owner")
        # A different worker cannot complete a lease it never owned, fresh or
        # not — defense-in-depth with the lease guard.
        ok = self.queue.complete(claimed["id"], "w-intruder", {"ok": True}, LEASE)
        self.assertFalse(ok)

    def test_invalid_lease_seconds_rejected(self) -> None:
        claimed = self._claim("w-validate")
        with self.assertRaises(ValueError):
            self.database.complete_turn_job(claimed["id"], "w-validate", {"ok": True}, 0)
        with self.assertRaises(ValueError):
            self.database.fail_turn_job(
                claimed["id"], "w-validate", "boom", 1, retryable=True, lease_seconds=0
            )


if __name__ == "__main__":
    unittest.main()
