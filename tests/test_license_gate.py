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

    def test_npm_manifest_is_opt_in(self) -> None:
        """Omitting ``npm_path`` must not pull in the repository's manifest.

        The default used to be ``DEFAULT_NPM_MANIFEST``, so every caller above
        — synthetic lock, synthetic policy — silently reviewed the real
        frontend dependencies against a three-package policy and failed.
        """
        lock, policy = _files(self.root, ["alpha==1.0"])
        self.assertEqual(review(lock, policy, today=FAR_FUTURE), [])


class NpmLicenseTrackTests(unittest.TestCase):
    """The npm runtime closure shipped in ``dist/assets`` (D2).

    React, ReactDOM, Zustand, TanStack Query and the four xterm packages are
    bundled by Vite and distributed with both the desktop app and the web
    build, but the gate read only ``requirements.lock`` — so eight shipped
    dependencies were never license-reviewed, and a ninth could land with any
    license at all and still pass.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.lock = self.root / "requirements.lock"
        self.lock.write_text("alpha==1.0\n", encoding="utf-8")
        self.policy = self.root / "license-policy.json"
        self.policy.write_text(json.dumps(POLICY), encoding="utf-8")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _manifest(self, payload: dict) -> Path:
        path = self.root / "package.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _review(self, payload: dict, today: date = FAR_FUTURE) -> list[str]:
        return review(self.lock, self.policy, today=today, npm_path=self._manifest(payload))

    def test_registered_npm_dependency_passes(self) -> None:
        self.assertEqual(self._review({"dependencies": {"alpha": "^19.0.0"}}), [])

    def test_unregistered_npm_dependency_fails(self) -> None:
        violations = self._review({"dependencies": {"newcomer": "^1.0.0"}})
        self.assertEqual(len(violations), 1)
        self.assertIn("newcomer", violations[0])
        self.assertIn("package.json", violations[0])

    def test_dev_dependencies_are_outside_the_closure(self) -> None:
        """vite/vitest never reach a user's machine, so they are not reviewed."""
        self.assertEqual(self._review({"devDependencies": {"vitest": "^3.0.0"}}), [])

    def test_scoped_package_name_is_matched(self) -> None:
        policy = {**POLICY, "packages": {**POLICY["packages"], "@scope/thing": "MIT"}}
        self.policy.write_text(json.dumps(policy), encoding="utf-8")
        self.assertEqual(self._review({"dependencies": {"@scope/thing": "^5.0.0"}}), [])

    def test_disallowed_npm_license_fails(self) -> None:
        policy = {**POLICY, "packages": {**POLICY["packages"], "copyleft": "GPL-3.0"}}
        self.policy.write_text(json.dumps(policy), encoding="utf-8")
        violations = self._review({"dependencies": {"copyleft": "^1.0.0"}})
        self.assertEqual(len(violations), 1)
        self.assertIn("GPL-3.0", violations[0])

    def test_expired_npm_tbd_approval_fails(self) -> None:
        violations = self._review({"dependencies": {"gamma": "^1.0.0"}}, today=date(2100, 1, 1))
        self.assertEqual(len(violations), 1)
        self.assertIn("到期", violations[0])

    def test_missing_manifest_is_not_a_violation(self) -> None:
        """A checkout without the frontend tree must not red-light the gate."""
        absent = self.root / "no-such-package.json"
        self.assertEqual(review(self.lock, self.policy, today=FAR_FUTURE, npm_path=absent), [])


class ShippedPolicyTests(unittest.TestCase):
    """The committed policy must actually cover what the repository ships."""

    def test_live_gate_passes_on_both_tracks(self) -> None:
        from scripts.license_gate import (
            DEFAULT_LOCK,
            DEFAULT_NPM_MANIFEST,
            DEFAULT_POLICY,
            _npm_package_names,
        )

        violations = review(
            DEFAULT_LOCK, DEFAULT_POLICY, today=date(2026, 9, 1), npm_path=DEFAULT_NPM_MANIFEST
        )
        self.assertEqual(violations, [], f"shipped license policy has gaps: {violations}")
        # Guards the wiring itself: if the manifest were ever read as empty the
        # assertion above would pass while reviewing nothing.
        self.assertGreaterEqual(len(_npm_package_names(DEFAULT_NPM_MANIFEST)), 8)


if __name__ == "__main__":
    unittest.main()
