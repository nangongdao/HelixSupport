"""Phase 41.7 / SEC-008: threat-model delta + security drill governance gate tests.

Covers missing delta fields, empty control/evidence lists, unnamed and
placeholder owners, future-dated deltas, missing/future/expired drills, and
nil-tolerant CI behavior (empty registry is not a violation without
``--release``/``--check-today``).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date
from pathlib import Path

from scripts.threat_model_gate import ReviewError, review

DELTAS = {
    "schema_version": 1,
    "deltas": [
        {
            "release": "1.4.0",
            "date": "2026-08-20",
            "owner": "Security Lead A",
            "approved_by": "Danny Chen",
            "controls": ["credential rotation API", "audit external anchoring"],
            "verification_evidence": [
                "tests/test_audit_anchors.py passes",
                "scripts/verify_audit_chain.py exit 0 on scratch restore",
            ],
        }
    ],
}

DRILLS = {
    "schema_version": 1,
    "drills": [
        {
            "drill_type": "report_intake",
            "started_at": "2026-08-20T10:00:00+00:00",
            "owner": "Security Lead A",
            "scenario": "inbound report intake",
            "duration_minutes": 45,
        }
    ],
}


def _files(
    root: Path,
    deltas: dict[str, object] | None = None,
    drills: dict[str, object] | None = None,
    *,
    delta_path: str = "threat-model-deltas.json",
    drill_path: str = "security-drills.json",
) -> tuple[Path, Path]:
    deltas_path = root / delta_path
    deltas_path.write_text(json.dumps(deltas if deltas is not None else DELTAS), encoding="utf-8")
    drills_path = root / drill_path
    drills_path.write_text(json.dumps(drills if drills is not None else DRILLS), encoding="utf-8")
    return deltas_path, drills_path


class ThreatModelGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.today = date(2026, 8, 20)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_clean_delta_and_fresh_drill_pass(self) -> None:
        deltas_path, drills_path = _files(self.root)
        self.assertEqual(review(deltas_path, drills_path, today=self.today), [])
        self.assertEqual(
            review(
                deltas_path,
                drills_path,
                today=self.today,
                required_release="1.4.0",
                check_today=True,
            ),
            [],
        )

    def test_nil_tolerant_when_registry_is_empty_in_ci(self) -> None:
        deltas_path, drills_path = _files(self.root, deltas={"schema_version": 1, "deltas": []})
        self.assertEqual(review(deltas_path, drills_path, today=self.today), [])

    def test_missing_required_field_fails(self) -> None:
        bad = json.loads(json.dumps(DELTAS))
        del bad["deltas"][0]["verification_evidence"]
        deltas_path, drills_path = _files(self.root, deltas=bad)
        violations = review(deltas_path, drills_path, today=self.today)
        self.assertEqual(len(violations), 1)
        self.assertIn("verification_evidence", violations[0])

    def test_empty_controls_and_evidence_fail(self) -> None:
        bad = json.loads(json.dumps(DELTAS))
        bad["deltas"][0]["controls"] = []
        bad["deltas"][0]["verification_evidence"] = []
        deltas_path, drills_path = _files(self.root, deltas=bad)
        violations = review(deltas_path, drills_path, today=self.today)
        self.assertEqual(len(violations), 2)
        self.assertTrue(all("不能为空列表" in violation for violation in violations))

    def test_placeholder_owner_fails(self) -> None:
        for owner in [
            "security@helix.example",
            "TBD",
            "<to be filled>",
            "",
            "security@example.com",
        ]:
            with self.subTest(owner=owner):
                bad = json.loads(json.dumps(DELTAS))
                bad["deltas"][0]["owner"] = owner
                deltas_path, drills_path = _files(self.root, deltas=bad)
                violations = review(deltas_path, drills_path, today=self.today)
                self.assertTrue(any("owner" in v and "占位符" in v for v in violations), violations)

    def test_missing_release_delta_fails(self) -> None:
        deltas_path, drills_path = _files(self.root)
        violations = review(deltas_path, drills_path, today=self.today, required_release="1.9.9")
        self.assertEqual(len(violations), 1)
        self.assertIn("1.9.9", violations[0])
        self.assertIn("缺失", violations[0])

    def test_future_dated_release_delta_fails(self) -> None:
        future = json.loads(json.dumps(DELTAS))
        future["deltas"][0]["date"] = "2026-09-01"
        deltas_path, drills_path = _files(self.root, deltas=future)
        violations = review(deltas_path, drills_path, today=self.today, required_release="1.4.0")
        self.assertEqual(len(violations), 1)
        self.assertIn("在未来", violations[0])

    def test_no_drills_with_check_today_fails(self) -> None:
        deltas_path, drills_path = _files(
            self.root,
            deltas={"schema_version": 1, "deltas": []},
            drills={"schema_version": 1, "drills": []},
        )
        violations = review(deltas_path, drills_path, today=self.today, check_today=True)
        self.assertEqual(len(violations), 1)
        self.assertIn("尚无任何安全演练记录", violations[0])

    def test_expired_drill_fails(self) -> None:
        stale = json.loads(json.dumps(DRILLS))
        stale["drills"][0]["started_at"] = "2026-05-01T10:00:00+00:00"
        deltas_path, drills_path = _files(self.root, drills=stale)
        violations = review(deltas_path, drills_path, today=self.today, check_today=True)
        self.assertEqual(len(violations), 1)
        self.assertIn("演练过期", violations[0])

    def test_future_dated_drill_fails(self) -> None:
        future = json.loads(json.dumps(DRILLS))
        future["drills"][0]["started_at"] = "2026-09-01T10:00:00+00:00"
        deltas_path, drills_path = _files(self.root, drills=future)
        violations = review(deltas_path, drills_path, today=self.today)
        self.assertEqual(len(violations), 1)
        self.assertIn("在未来", violations[0])

    def test_invalid_drill_type_fails(self) -> None:
        bad = json.loads(json.dumps(DRILLS))
        bad["drills"][0]["drill_type"] = "moonwalk"
        deltas_path, drills_path = _files(self.root, drills=bad)
        violations = review(deltas_path, drills_path, today=self.today)
        self.assertEqual(len(violations), 1)
        self.assertIn("drill_type", violations[0])

    def test_missing_date_is_not_a_review_error(self) -> None:
        bad = json.loads(json.dumps(DELTAS))
        del bad["deltas"][0]["date"]
        deltas_path, drills_path = _files(self.root, deltas=bad)
        violations = review(deltas_path, drills_path, today=self.today)
        self.assertTrue(any("缺少必填字段 date" in v for v in violations))

    def test_unreadable_config_raises_review_error(self) -> None:
        deltas_path = self.root / "missing-deltas.json"
        drills_path = self.root / "security-drills.json"
        (self.root / "security-drills.json").write_text(json.dumps(DRILLS), encoding="utf-8")
        with self.assertRaises(ReviewError):
            review(deltas_path, drills_path, today=self.today)

    def test_bad_json_raises_review_error(self) -> None:
        deltas_path = self.root / "threat-model-deltas.json"
        deltas_path.write_text("{not json", encoding="utf-8")
        drills_path = self.root / "security-drills.json"
        (self.root / "security-drills.json").write_text(json.dumps(DRILLS), encoding="utf-8")
        with self.assertRaises(ReviewError):
            review(deltas_path, drills_path, today=self.today)


if __name__ == "__main__":
    unittest.main()
