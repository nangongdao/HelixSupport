"""Telemetry OpenTelemetry-backed branches — mocked, no real OTel needed.

app.telemetry tries to import opentelemetry at module load; when absent it
falls back to lightweight tracing. These tests reload the module with mocked
OTel modules in sys.modules so the SDK-backed branches
(configure_tracing / span with a provider / Span.set_attribute +
add_event forwarding) execute without the package installed.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
import unittest
from unittest import mock

# Mock the opentelemetry package tree so the guarded import inside
# app.telemetry succeeds and flips _otel_available.
_OTEL_MOCKS = {
    "opentelemetry": mock.MagicMock(),
    "opentelemetry.trace": mock.MagicMock(),
    "opentelemetry.exporter": mock.MagicMock(),
    "opentelemetry.exporter.otlp": mock.MagicMock(),
    "opentelemetry.exporter.otlp.proto": mock.MagicMock(),
    "opentelemetry.exporter.otlp.proto.http": mock.MagicMock(),
    "opentelemetry.exporter.otlp.proto.http.trace_exporter": mock.MagicMock(),
    "opentelemetry.sdk": mock.MagicMock(),
    "opentelemetry.sdk.resources": mock.MagicMock(),
    "opentelemetry.sdk.trace": mock.MagicMock(),
    "opentelemetry.sdk.trace.export": mock.MagicMock(),
}


@mock.patch.dict(sys.modules, _OTEL_MOCKS)
def _reload_with_otel() -> tuple[object, mock.MagicMock]:
    # Import after the mocks are in place: the module-level try/except sees a
    # successful import and sets _otel_available = True.
    tel = importlib.import_module("app.telemetry")
    tel = importlib.reload(tel)
    return tel, _OTEL_MOCKS["opentelemetry"]


def _reload_without_otel() -> object:
    for key in list(_OTEL_MOCKS):
        sys.modules.pop(key, None)
    tel = importlib.import_module("app.telemetry")
    tel = importlib.reload(tel)
    return tel


class TelemetryOtelTests(unittest.TestCase):
    def test_configure_tracing_initializes_provider(self) -> None:
        tel, _otel = _reload_with_otel()
        self.addCleanup(lambda: _reload_without_otel())
        sdk_trace = _OTEL_MOCKS["opentelemetry.sdk.trace"]
        with mock.patch.dict(os.environ, {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}):
            tel.configure_tracing()
        sdk_trace.TracerProvider.assert_called()
        # Idempotent: a second call returns without rebuilding.
        tel.configure_tracing()
        self.assertEqual(sdk_trace.TracerProvider.call_count, 1)

    def test_span_forwards_attributes_and_events_to_otel(self) -> None:
        tel, _otel = _reload_with_otel()
        self.addCleanup(lambda: _reload_without_otel())
        tel.configure_tracing()
        provider = tel._tracer_provider
        otel_span = provider.get_tracer("helix-support").start_as_current_span().__enter__()
        with tel.span("op", attr1="v1") as record:
            record.set_attribute("k2", "v2")
            record.add_event("midpoint", {"k": "v"})
            self.assertIsNotNone(record._otel_span)
        otel_span.set_attribute.assert_any_call("attr1", "v1")
        otel_span.set_attribute.assert_any_call("k2", "v2")
        otel_span.add_event.assert_called_once_with("midpoint", {"k": "v"})
        self.assertIsNotNone(record.end_time)

    def test_configure_logs_nothing_when_otel_absent(self) -> None:
        tel = _reload_without_otel()
        with self.assertLogs("app.telemetry", level=logging.DEBUG) as captured:
            tel.configure_tracing()
        self.assertTrue(any("opentelemetry not installed" in line for line in captured.output))


if __name__ == "__main__":
    unittest.main()
