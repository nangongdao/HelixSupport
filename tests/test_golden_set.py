"""Golden-set release gate.

The deployment plan treats the golden set as the acceptance gate for model,
prompt, or routing changes: no regression is allowed before a broad release.
This test runs the full offline evaluation and fails on any case that stops
matching its expected outcome, so the gate is enforced by the normal CI suite.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from scripts.evaluate import evaluate

GOLDEN = Path(__file__).resolve().parent.parent / "golden" / "set.json"


class GoldenSetGateTests(unittest.TestCase):
    def test_all_golden_cases_pass(self) -> None:
        """Every golden case must still match its expected routing outcome."""
        report = evaluate(GOLDEN)
        failures = [c for c in report["cases"] if not c["passed"]]
        self.assertEqual(
            failures,
            [],
            f"golden-set regression in {len(failures)} case(s): {failures}",
        )
        self.assertEqual(report["passed"], report["total"])


if __name__ == "__main__":
    unittest.main()
