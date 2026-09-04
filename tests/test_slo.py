"""Phase 42.6: SLO multi-window burn-rate alerting.

The firing rules: an alert fires only when BOTH windows of a pair breach
their burn-rate thresholds; page (14.4× over 1h+5m) and ticket (6× over
6h+30m) severities stay separated; a quiet long window suppresses a fast
spike; and the fired alert carries the numbers on-call needs to correlate.
"""

from __future__ import annotations

import unittest

from app.slo import (
    DEFAULT_SLOS,
    PAGE_SLO,
    TICKET_SLO,
    evaluate_all,
    evaluate_slo,
)


def _counts(rate: float, total: float = 1_000_000) -> tuple[float, float]:
    return round(total * rate), total


class BurnRateRuleTests(unittest.TestCase):
    def test_page_fires_when_both_windows_breach(self) -> None:
        # 14.4× of the 0.5% budget = 7.2% error rate needed on both windows.
        alert = evaluate_slo(PAGE_SLO, {"fast": _counts(0.10), "long": _counts(0.10)})
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.severity, "page")
        self.assertEqual(alert.slo_name, "api-availability-page")

    def test_fast_spike_alone_does_not_page(self) -> None:
        # 50% errors for five minutes, but the hour is clean — no page.
        alert = evaluate_slo(PAGE_SLO, {"fast": _counts(0.5), "long": _counts(0.001)})
        self.assertIsNone(alert)

    def test_slow_sustained_burn_tickets_not_pages(self) -> None:
        # 4% error rate for hours: above the 6× ticket threshold (3%), far
        # below the 14.4× page threshold (7.2%).
        counts = {"fast": _counts(0.04), "long": _counts(0.04)}
        self.assertIsNone(evaluate_slo(PAGE_SLO, counts))
        alert = evaluate_slo(TICKET_SLO, counts)
        self.assertIsNotNone(alert)
        assert alert is not None
        self.assertEqual(alert.severity, "ticket")

    def test_empty_long_window_suppresses(self) -> None:
        alert = evaluate_slo(PAGE_SLO, {"fast": _counts(0.5), "long": (0, 0)})
        self.assertIsNone(alert)
        # A missing window entry is equally unknown — never fires.
        self.assertIsNone(evaluate_slo(PAGE_SLO, {"fast": _counts(0.5)}))

    def test_healthy_traffic_never_fires(self) -> None:
        counts = {"fast": _counts(0.001), "long": _counts(0.001)}
        for slo in DEFAULT_SLOS:
            self.assertIsNone(evaluate_slo(slo, counts))

    def test_alert_carries_correlation_numbers(self) -> None:
        alert = evaluate_slo(PAGE_SLO, {"fast": _counts(0.10), "long": _counts(0.08)})
        assert alert is not None
        self.assertIn("fast", alert.window_rates)
        self.assertIn("long", alert.thresholds)
        self.assertGreater(alert.error_budget_consumed_percent, 0)

    def test_evaluate_all_pages_first_and_skips_absent_slos(self) -> None:
        counts_by_slo = {
            PAGE_SLO.name: {"fast": _counts(0.10), "long": _counts(0.10)},
            TICKET_SLO.name: {"fast": _counts(0.001), "long": _counts(0.001)},
        }
        fired = evaluate_all(window_counts_by_slo=counts_by_slo)
        self.assertEqual([alert.severity for alert in fired], ["page"])

    def test_invalid_definitions_rejected(self) -> None:
        from app.slo import SLODefinition, SLOWindow

        with self.assertRaises(ValueError):
            SLODefinition("x", 1.5, "page", (SLOWindow("a", 5, 14.4), SLOWindow("b", 60, 14.4)))
        with self.assertRaises(ValueError):
            SLODefinition("x", 0.99, "wake", (SLOWindow("a", 5, 14.4), SLOWindow("b", 60, 14.4)))
        with self.assertRaises(ValueError):
            SLODefinition("x", 0.99, "page", (SLOWindow("a", 5, 14.4),))


if __name__ == "__main__":
    unittest.main()
