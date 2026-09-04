"""Phase 41.2 / SEC-003: vulnerability exception review gate tests.

Covers schema validation, the automatic red light for expired exceptions, the
required unreachable-evidence / compensating-control fields on open entries,
and pip-audit coverage -- no CVE may go unregistered.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.vuln_review import ReviewError, audit_coverage, review

FAR_PAST = date(2020, 1, 1)
FAR_FUTURE = date(2099, 1, 1)


def _exceptions_path(root: Path, exceptions: list[dict[str, object]]) -> Path:
    path = root / "vulnerability-exceptions.json"
    path.write_text(json.dumps({"schema_version": 1, "exceptions": exceptions}), encoding="utf-8")
    return path


def _open_entry(**overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "id": "CVE-2026-9999",
        "component": "example-pkg==1.2.3",
        "not_reachable_evidence": "调用链在启动自检即短路，生产依赖路径不可达",
        "compensating_control": "网络层 ACL 阻断受影响端点；监控覆盖",
        "owner": "platform-security",
        "due_date": "2099-01-01",
        "status": "open",
    }
    entry.update(overrides)
    return entry


class VulnerabilityReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_empty_exceptions_are_clean(self) -> None:
        path = _exceptions_path(self.root, [])
        self.assertEqual(review(path, today=FAR_FUTURE), [])

    def test_expired_open_exception_red_lights(self) -> None:
        path = _exceptions_path(self.root, [_open_entry(due_date="2020-01-01")])
        violations = review(path, today=FAR_FUTURE)
        self.assertEqual(len(violations), 1)
        self.assertIn("到期", violations[0])

    def test_open_exception_missing_evidence_fails(self) -> None:
        path = _exceptions_path(
            self.root, [_open_entry(not_reachable_evidence="", compensating_control="")]
        )
        violations = review(path, today=FAR_PAST)
        self.assertEqual(len(violations), 2)

    def test_open_exception_missing_owner_and_due_date_fails(self) -> None:
        path = _exceptions_path(self.root, [_open_entry(owner="", due_date="")])
        violations = review(path, today=FAR_PAST)
        self.assertGreaterEqual(len(violations), 2)

    def test_closed_exception_without_evidence_is_clean(self) -> None:
        entry = _open_entry(status="closed", due_date="2020-01-01")
        entry.pop("not_reachable_evidence")
        entry.pop("compensating_control")
        path = _exceptions_path(self.root, [entry])
        self.assertEqual(review(path, today=FAR_FUTURE), [])

    def test_invalid_status_fails(self) -> None:
        path = _exceptions_path(self.root, [_open_entry(status="quarantine")])
        violations = review(path, today=FAR_PAST)
        self.assertIn("status", violations[0])

    def test_unregistered_cve_in_audit_fails_coverage(self) -> None:
        exceptions = _exceptions_path(self.root, [_open_entry(id="CVE-2026-9998")])
        audit = self.root / "pip-audit.json"
        audit.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "example-pkg",
                            "vulns": [{"id": "CVE-2026-9999"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        violations = audit_coverage(audit, exceptions)
        self.assertEqual(len(violations), 1)
        self.assertIn("CVE-2026-9999", violations[0])

    def test_legacy_vulnerabilities_key_still_read(self) -> None:
        """古い成果物の "vulnerabilities" キーも読めること。"""
        exceptions = _exceptions_path(self.root, [])
        audit = self.root / "pip-audit.json"
        audit.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "example-pkg",
                            "vulnerabilities": [{"id": "CVE-2026-9997"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        violations = audit_coverage(audit, exceptions)
        self.assertEqual(len(violations), 1)
        self.assertIn("CVE-2026-9997", violations[0])

    def test_registered_cve_in_audit_passes_coverage(self) -> None:
        exceptions = _exceptions_path(self.root, [_open_entry(id="CVE-2026-9999")])
        audit = self.root / "pip-audit.json"
        audit.write_text(
            json.dumps(
                {
                    "dependencies": [
                        {
                            "name": "example-pkg",
                            "vulns": [{"id": "CVE-2026-9999"}],
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        self.assertEqual(audit_coverage(audit, exceptions), [])

    def test_missing_audit_file_is_reported(self) -> None:
        exceptions = _exceptions_path(self.root, [])
        violations = audit_coverage(self.root / "missing.json", exceptions)
        self.assertEqual(len(violations), 1)

    def test_missing_exceptions_list_raises_review_error(self) -> None:
        path = self.root / "bad.json"
        path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        with self.assertRaises(ReviewError):
            review(path, today=FAR_PAST)


if __name__ == "__main__":
    unittest.main()
