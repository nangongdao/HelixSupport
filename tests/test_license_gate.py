"""Phase 41.2 / SEC-003: license policy gate tests.

Covers the reviewed-only dependency closure (every new locked package must be
consciously registered), the allowed-license set, and the temporary
``LicenseRef-TBD`` approval that expires into a red light.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.license_gate import review

FAR_PAST = date(2020, 1, 1)
FAR_FUTURE = date(2099, 1, 1)

POLICY = {
    "schema_version": 1,
    "allowed_licenses": ["MIT", "BSD-3-Clause", "Apache-2.0"],
    "packages": {
        "alpha": "MIT",
        "beta": {"license": "BSD-3-Clause"},
        "gamma": {"license": "LicenseRef-TBD", "approved_until": "2099-01-01"},
        "pyyaml": "MIT",
    },
}


def _files(root: Path, lock_lines: list[str]) -> tuple[Path, Path]:
    lock = root / "requirements.lock"
    lock.write_text("# pinned\n" + "\n".join(lock_lines) + "\n", encoding="utf-8")
    policy = root / "license-policy.json"
    policy.write_text(json.dumps(POLICY), encoding="utf-8")
    return lock, policy


class LicenseGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_all_registered_and_allowed_passes(self) -> None:
        lock, policy = _files(self.root, ["alpha==1.0", "beta==2.0"])
        self.assertEqual(review(lock, policy, today=FAR_FUTURE), [])

    def test_unregistered_package_fails(self) -> None:
        lock, policy = _files(self.root, ["alpha==1.0", "newcomer==0.1"])
        violations = review(lock, policy, today=FAR_FUTURE)
        self.assertEqual(len(violations), 1)
        self.assertIn("newcomer", violations[0])

    def test_disallowed_license_fails(self) -> None:
        policy = dict(POLICY)
        policy["packages"] = {**POLICY["packages"], "alpha": "GPL-3.0-only"}
        lock = self.root / "requirements.lock"
        lock.write_text("alpha==1.0\n", encoding="utf-8")
        policy_path = self.root / "license-policy.json"
        policy_path.write_text(json.dumps(policy), encoding="utf-8")
        violations = review(lock, policy_path, today=FAR_FUTURE)
        self.assertEqual(len(violations), 1)
        self.assertIn("GPL-3.0-only", violations[0])

    def test_tbd_approval_within_deadline_passes(self) -> None:
        lock, policy = _files(self.root, ["gamma==1.0"])
        # today exactly on the approval boundary is still approved.
        self.assertEqual(review(lock, policy, today=date(2099, 1, 1)), [])

    def test_tbd_approval_past_its_deadline_fails(self) -> None:
        lock, policy = _files(self.root, ["gamma==1.0"])
        violations = review(lock, policy, today=date(2100, 1, 1))
        self.assertEqual(len(violations), 1)
        self.assertIn("到期", violations[0])

    def test_case_insensitive_package_matching(self) -> None:
        # lock uses PyYAML while the policy registers ``pyyaml``.
        lock, policy = _files(self.root, ["PyYAML==6.0.3"])
        self.assertEqual(review(lock, policy, today=FAR_FUTURE), [])


if __name__ == "__main__":
    unittest.main()
