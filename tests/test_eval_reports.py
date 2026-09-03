"""Phase 41.5 / AI-001: WORM evaluation reports and the promotion gate.

Covers the acceptance criteria of ADR-014 decision 5 / Gate B item 5:
- an evaluation report persists exactly once under its run id (a second
  write is refused) and is readable back intact;
- tampering with a stored object surfaces as ``WormIntegrityError``;
- ``decide_promotion`` requires all five gate conditions (adversarial floor,
  golden floor, quality not below active, p95 budget, cost budget) and fails
  closed when a mandatory report is missing;
- the promotion record is a separate WORM object keyed by candidate.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.eval_reports import (
    EvalReportStore,
    decide_promotion,
    generate_report_object_id,
    promotion_object_id,
    promotion_record,
)
from app.worm_store import WormIntegrityError


def _settings() -> Settings:
    return Settings(
        database_path=Path(tempfile.mkdtemp()) / "gate.db",
        auth_mode="api_key",
        api_keys_json="{}",
        eval_worm_dir=Path(tempfile.mkdtemp()) / "reports",
        eval_adversarial_floor=1.0,
        eval_golden_floor=1.0,
        eval_p95_budget_ms=5000,
        eval_cost_budget_usd=0.01,
    )


def _report(pass_rate: float = 1.0, **overrides: float) -> dict[str, object]:
    report: dict[str, object] = {
        "total": 10,
        "passed": round(10 * pass_rate),
        "failed": round(10 * (1 - pass_rate)),
        "pass_rate": pass_rate,
        "mean_confidence": 0.9,
        "citation_coverage": 0.8,
        "p95_latency_ms": 120.0,
        "estimated_cost_usd": 0.0,
    }
    report.update(overrides)
    return report


class EvalReportStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = EvalReportStore(Path(self._tmp.name))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_report_written_once_under_run_id(self) -> None:
        run_id = generate_report_object_id("adv")
        self.store.write_evaluation_report(run_id, _report())
        with self.assertRaises(WormIntegrityError):
            self.store.write_evaluation_report(run_id, _report())  # second write refused
        objects = self.store.read_all()
        matching = [obj for obj in objects if obj.get("run_id") == run_id]
        self.assertEqual(len(matching), 1)

    def test_read_all_round_trip_preserves_report(self) -> None:
        run_id = generate_report_object_id("adv")
        report = _report(pass_rate=1.0, mean_confidence=0.92)
        self.store.write_evaluation_report(run_id, report)
        objects = self.store.read_all()
        self.assertEqual(objects[0]["kind"], "evaluation_report")
        self.assertEqual(objects[0]["report"]["pass_rate"], 1.0)
        self.assertEqual(objects[0]["report"]["mean_confidence"], 0.92)

    def test_tampered_object_surfaces_integrity_error(self) -> None:
        run_id = generate_report_object_id("adv")
        self.store.write_evaluation_report(run_id, _report())
        payload_path = Path(self._tmp.name) / f"{run_id}.json"
        payload = json.loads(payload_path.read_text(encoding="utf-8"))
        payload["report"]["pass_rate"] = 0.5
        payload_path.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaises(WormIntegrityError):
            self.store.read_all()

    def test_promotion_record_object_id_is_stable_per_candidate(self) -> None:
        self.assertEqual(
            promotion_object_id("deterministic-eval"),
            promotion_object_id("deterministic-eval"),
        )
        self.assertNotEqual(
            promotion_object_id("deterministic-eval"),
            promotion_object_id("other-candidate"),
        )


class PromotionGateTests(unittest.TestCase):
    def test_all_conditions_pass_allows_promotion(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=_report(pass_rate=1.0),
            golden_report=_report(pass_rate=1.0),
            active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
            settings=settings,
        )
        self.assertTrue(passed, reasons)
        self.assertEqual(reasons, [])

    def test_missing_report_fails_closed(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=None,
            golden_report=_report(),
            active_metrics={},
            settings=settings,
        )
        self.assertFalse(passed)
        self.assertIn("no adversarial report", reasons)

    def test_adversarial_below_floor_blocks(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=_report(pass_rate=0.8),
            golden_report=_report(pass_rate=1.0),
            active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
            settings=settings,
        )
        self.assertFalse(passed)
        self.assertTrue(any("adversarial pass rate" in reason for reason in reasons))

    def test_quality_below_active_blocks(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=_report(pass_rate=1.0, mean_confidence=0.7, citation_coverage=0.5),
            golden_report=_report(pass_rate=1.0),
            active_metrics={
                "pass_rate": 1.0,
                "mean_confidence": 0.9,
                "citation_coverage": 0.8,
            },
            settings=settings,
        )
        self.assertFalse(passed)
        self.assertTrue(any("mean_confidence" in reason for reason in reasons))
        self.assertTrue(any("citation_coverage" in reason for reason in reasons))

    def test_p95_budget_blocks(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=_report(pass_rate=1.0, p95_latency_ms=9000.0),
            golden_report=_report(pass_rate=1.0, p95_latency_ms=100.0),
            active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
            settings=settings,
        )
        self.assertFalse(passed)
        self.assertTrue(any("p95" in reason for reason in reasons))

    def test_cost_budget_blocks(self) -> None:
        settings = _settings()
        passed, reasons = decide_promotion(
            adversarial_report=_report(pass_rate=1.0, estimated_cost_usd=0.5),
            golden_report=_report(pass_rate=1.0),
            active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
            settings=settings,
        )
        self.assertFalse(passed)
        self.assertTrue(any("cost" in reason for reason in reasons))

    def test_promotion_record_is_a_disclosure_document(self) -> None:
        settings = _settings()
        record = promotion_record(
            run_id="adv-abc123",
            candidate="deterministic-eval",
            active="v1",
            passed=True,
            reasons=[],
            adversarial_report=_report(),
            golden_report=_report(),
            active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
            settings=settings,
        )
        self.assertEqual(record["passed"], True)
        self.assertEqual(record["candidate"], "deterministic-eval")
        self.assertEqual(record["floors"]["adversarial"], 1.0)
        self.assertIn("adversarial", record)

    def test_gate_persists_promotion_record_to_worm(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = EvalReportStore(Path(tmp))
            settings = Settings(
                database_path=Path(tmp) / "gate.db",
                auth_mode="api_key",
                api_keys_json="{}",
                eval_worm_dir=Path(tmp),
            )
            passed, reasons = decide_promotion(
                adversarial_report=_report(),
                golden_report=_report(),
                active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
                settings=settings,
            )
            self.assertTrue(passed, reasons)
            record = promotion_record(
                run_id="adv-xyz789",
                candidate="deterministic-eval",
                active="v1",
                passed=passed,
                reasons=reasons,
                adversarial_report=_report(),
                golden_report=_report(),
                active_metrics={"pass_rate": 1.0, "mean_confidence": 0.9, "citation_coverage": 0.8},
                settings=settings,
            )
            store.write_promotion_record(record["candidate"], record)
            objects = store.read_all()
            self.assertEqual(len(objects), 1)
            self.assertEqual(objects[0]["kind"], "promotion_record")


if __name__ == "__main__":
    unittest.main()
