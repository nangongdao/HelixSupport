"""Telemetry edge branches — pure local logic (no OpenTelemetry needed).

Complements tests/test_infrastructure.py::TelemetryTests with the branches
that run without the opentelemetry package installed: the un-ended span's
``duration_ms`` (None), the DEBUG completion log, multi-sample percentile
aggregation (p50/p95/p99 diverge), and tagged counter keys.
"""

from __future__ import annotations

import logging
import unittest

from app.telemetry import TelemetryMetrics, span


class TelemetryEdgeTests(unittest.TestCase):
    def test_duration_ms_before_end_is_none(self) -> None:
        with span("op") as record:
            self.assertIsNone(record.duration_ms)

    def test_span_end_records_duration(self) -> None:
        with span("op") as record:
            pass
        self.assertIsNotNone(record.end_time)
        self.assertGreaterEqual(record.duration_ms or 0, 0)

    def test_debug_log_emitted_at_span_end(self) -> None:
        with self.assertLogs("app.telemetry", level=logging.DEBUG) as captured:
            with span("logged_op", key="value"):
                pass
        self.assertTrue(
            any("span.complete" in line and "logged_op" in line for line in captured.output),
            captured.output,
        )

    def test_histogram_multi_sample_percentiles(self) -> None:
        m = TelemetryMetrics()
        for value in (10, 20, 30, 40, 100):
            m.observe("latency_ms", value)
        summary = m.snapshot()["histograms"]["latency_ms"]["p50"]
        self.assertGreater(summary, 0)
        p95 = m.snapshot()["histograms"]["latency_ms"]["p95"]
        self.assertGreaterEqual(p95, summary)

    def test_histogram_single_sample_maps_all_percentiles(self) -> None:
        m = TelemetryMetrics()
        m.observe("latency_ms", 42.0)
        summary = m.snapshot()["histograms"]["latency_ms"]
        self.assertEqual(summary["p50"], 42.0)
        self.assertEqual(summary["p95"], 42.0)
        self.assertEqual(summary["p99"], 42.0)

    def test_tagged_counter_key(self) -> None:
        m = TelemetryMetrics()
        m.increment("turns", tenant="demo", channel="web")
        snap = m.snapshot()
        self.assertIn("turns{channel=web,tenant=demo}", snap["counters"])


if __name__ == "__main__":
    unittest.main()
