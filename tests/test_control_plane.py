"""Phase 43.1: tenant control plane — signed snapshots & last-known-good.

Contracts: the control plane is the only policy writer and speaks in
HMAC-signed versioned snapshots; the data plane verifies before accepting,
serves last-known-good while the control plane is down (within TTL), refuses
expired snapshots, and rejects high-risk policy *changes* that arrive while
the control plane is unavailable.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.control_plane import (
    ConfigSnapshot,
    ControlPlaneError,
    DataPlaneConfig,
    PolicyUnavailableError,
    SnapshotVerificationError,
    TenantControlPlane,
    TenantPolicy,
)
from app.database import Database

SECRET = "control-plane-signing-secret-0123456789abcdef"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {
        "plan": "enterprise",
        "region": "eu-central",
        "deployment_cell": "cell-7",
        "feature_policy": frozenset({"copilot", "reports"}),
        "model_policy": {"allowed_models": ["gpt-4.1-mini"], "daily_turn_budget": 5000},
    }
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class ControlPlanePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "cp.db")
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.cp = TenantControlPlane(self.db, SECRET)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_unknown_plan_rejected(self) -> None:
        with self.assertRaises(ValueError):
            _policy(plan="platinum")

    def test_versions_increment_and_snapshots_verify(self) -> None:
        first = self.cp.set_policy("acme", _policy())
        second = self.cp.set_policy("acme", _policy(feature_policy=frozenset({"copilot"})))
        self.assertEqual((first.version, second.version), (1, 2))
        for snapshot in (first, second):
            document = snapshot.to_document()
            from app.control_plane import verify_snapshot_signature

            self.assertTrue(
                verify_snapshot_signature(SECRET.encode(), document, snapshot.signature)
            )
        self.assertEqual(self.cp.current_version("acme"), 2)

    def test_issue_snapshot_reverifies_stored_state(self) -> None:
        self.cp.set_policy("acme", _policy())
        snapshot = self.cp.issue_snapshot("acme")
        self.assertEqual(snapshot.version, 1)
        # A tampered stored row must fail closed on re-issue.
        with self.db.connect() as connection:
            connection.execute(
                "UPDATE tenant_control_policies SET plan = 'free' WHERE tenant_id = 'acme'"
            )
        with self.assertRaises(SnapshotVerificationError):
            self.cp.issue_snapshot("acme")

    def test_unknown_tenant_has_no_policy(self) -> None:
        with self.assertRaises(LookupError):
            self.cp.issue_snapshot("ghost")


class DataPlaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "dp.db")
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.cp = TenantControlPlane(self.db, SECRET)
        self.dp = DataPlaneConfig(self.db, SECRET)
        self.snapshot = self.cp.set_policy("acme", _policy())

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_apply_and_serve(self) -> None:
        self.dp.apply_snapshot(self.snapshot)
        policy = self.dp.effective_policy("acme")
        self.assertEqual(policy.plan, "enterprise")
        self.assertEqual(policy.region, "eu-central")

    def test_tampered_snapshot_rejected(self) -> None:
        forged = ConfigSnapshot(
            tenant_id="acme",
            version=99,
            issued_at=self.snapshot.issued_at,
            expires_at=self.snapshot.expires_at,
            policy=_policy(plan="free"),
            signature=self.snapshot.signature,
        )
        with self.assertRaises(SnapshotVerificationError):
            self.dp.apply_snapshot(forged)

    def test_expired_snapshot_rejected_on_arrival(self) -> None:
        expired = self.cp.set_policy("acme", _policy(), ttl_seconds=-1)
        with self.assertRaises(SnapshotVerificationError):
            self.dp.apply_snapshot(expired)

    def test_last_known_good_serves_while_control_plane_down(self) -> None:
        self.dp.apply_snapshot(self.snapshot)
        self.dp.set_control_plane_available(False)
        policy = self.dp.effective_policy("acme")
        self.assertEqual(policy.plan, "enterprise")

    def test_high_risk_change_rejected_while_degraded(self) -> None:
        self.dp.apply_snapshot(self.snapshot)
        self.dp.set_control_plane_available(False)
        changed = self.cp.set_policy("acme", _policy(region="us-east"))
        with self.assertRaises(ControlPlaneError):
            self.dp.apply_snapshot(changed)
        # Last-known-good still serves the old policy.
        self.assertEqual(self.dp.effective_policy("acme").region, "eu-central")

    def test_low_risk_change_allowed_while_degraded(self) -> None:
        self.dp.apply_snapshot(self.snapshot)
        self.dp.set_control_plane_available(False)
        feature_flip = self.cp.set_policy(
            "acme", _policy(feature_policy=frozenset({"copilot", "reports", "bulk"}))
        )
        self.dp.apply_snapshot(feature_flip)
        self.assertIn("bulk", self.dp.effective_policy("acme").feature_policy)

    def test_expired_lkg_fails_closed(self) -> None:
        expired = self.cp.set_policy("acme", _policy(), ttl_seconds=1)
        clock = {"now": 1_000_000.0}
        dp = DataPlaneConfig(self.db, SECRET, clock=lambda: clock["now"])
        from datetime import datetime

        # Accept while unexpired…
        expiry_epoch = datetime.fromisoformat(expired.expires_at).timestamp()
        clock["now"] = expiry_epoch - 10
        dp.apply_snapshot(expired)
        # …then the TTL runs out.
        clock["now"] = expiry_epoch + 1
        with self.assertRaises(PolicyUnavailableError):
            dp.effective_policy("acme")

    def test_no_policy_at_all_fails_closed(self) -> None:
        self.db.ensure_tenant("fresh")
        with self.assertRaises(PolicyUnavailableError):
            self.dp.effective_policy("fresh")


if __name__ == "__main__":
    unittest.main()
