"""OpenTelemetry production end-to-end verification.

These tests run only when the optional OTel SDK is installed (``pip install -e
'.[otel]'``); otherwise they are skipped.  They verify the roadmap claim that
traces are actually forwarded to OpenTelemetry when configured:

* ``configure_tracing`` wires an OTLP span exporter at the configured endpoint.
* Every HTTP request produces a finished ``http.request`` span carrying method,
  path, route, status code, and a duration.
* Attributes added to a span after creation are forwarded to the exported OTel
  span (e.g. the route resolved by the router after the middleware starts).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def _otel_importable() -> bool:
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # noqa: F401
            OTLPSpanExporter,
        )
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import (  # noqa: F401
            InMemorySpanExporter,
        )
        from opentelemetry.sdk.trace import TracerProvider  # noqa: F401

        return True
    except Exception:
        return False


@unittest.skipUnless(_otel_importable(), "Requires the optional opentelemetry extras")
class OpenTelemetryEndToEndTests(unittest.TestCase):
    ADMIN_KEY = "otel-admin-key-0001"

    def setUp(self) -> None:
        import app.telemetry as telemetry
        from opentelemetry import trace as otel_trace

        self._telemetry = telemetry
        self._otel_trace = otel_trace
        self._original_provider = telemetry._tracer_provider
        self._original_global = otel_trace.get_tracer_provider()

        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "otel.db"
        principals = {
            self.ADMIN_KEY: {"tenant_id": "t1", "actor_id": "agent.admin", "role": "admin"}
        }
        settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=1000,
            docs_enabled=False,
        )
        self.client = TestClient(create_app(settings))
        self.headers = {"X-API-Key": self.ADMIN_KEY, "X-Tenant-Id": "t1"}

    def tearDown(self) -> None:
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = getattr(self, "_exporter", None)
        if isinstance(exporter, InMemorySpanExporter):
            exporter.clear()
        services = cast(Any, cast(Any, self.client.app).state).services
        services.database.close()
        self.client.close()
        self._telemetry._tracer_provider = self._original_provider
        self._otel_trace.set_tracer_provider(self._original_global)
        self._tmp.cleanup()

    def _route_spans_to_memory(self) -> Any:
        """Point the OTel global provider at an in-memory exporter."""
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        self._otel_trace.set_tracer_provider(provider)
        self._telemetry._tracer_provider = provider
        self._exporter = exporter
        return exporter

    def test_configure_tracing_wires_otlp_exporter_at_endpoint(self) -> None:
        """``configure_tracing`` builds an OTLP exporter at ``{endpoint}/v1/traces``."""
        captured: list[str] = []

        class FakeExporter:
            def __init__(self, endpoint: str) -> None:
                captured.append(endpoint)

            def shutdown(self) -> None:
                return None

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        self._telemetry._otel_available = True
        self._telemetry._tracer_provider = None
        with (
            patch.dict("os.environ", {"OTEL_EXPORTER_OTLP_ENDPOINT": "http://collector:4318"}),
            patch.object(self._telemetry, "OTLPSpanExporter", FakeExporter),
        ):
            self._telemetry.configure_tracing()
        self.assertEqual(captured, ["http://collector:4318/v1/traces"])
        self.assertIsNotNone(self._telemetry._tracer_provider)

    def test_http_request_produces_finished_span_with_attributes(self) -> None:
        exporter = self._route_spans_to_memory()

        created = self.client.post(
            "/api/conversations",
            json={"customer_name": "OTel E2E", "channel": "web"},
            headers=self.headers,
        )
        self.assertEqual(created.status_code, 201, created.text)
        self.client.get("/api/conversations", headers=self.headers)

        spans = exporter.get_finished_spans()
        request_spans = [s for s in spans if s.name == "http.request"]
        self.assertTrue(request_spans, "no http.request span reached the exporter")

        by_method_route = {
            (s.attributes.get("method"), s.attributes.get("route")): s for s in request_spans
        }
        # The route attribute is added after the span starts (resolved by the
        # router), proving post-creation attributes are forwarded.
        get_span = by_method_route.get(("GET", "/api/conversations"))
        post_span = by_method_route.get(("POST", "/api/conversations"))
        assert get_span is not None, "no GET /api/conversations span exported"
        assert post_span is not None, "no POST /api/conversations span exported"
        self.assertEqual(get_span.attributes.get("status_code"), 200)
        self.assertEqual(post_span.attributes.get("status_code"), 201)
        for span in request_spans:
            self.assertGreaterEqual(span.end_time, span.start_time)

    def test_span_nesting_is_forwarded_to_otel(self) -> None:
        exporter = self._route_spans_to_memory()
        from app.telemetry import span

        with span("parent", service="helix") as parent:
            with span("child", depth=1) as child:
                child.set_attribute("after_start", "forwarded")
                parent.add_event("note", {"k": "v"})

        finished = exporter.get_finished_spans()
        by_name = {s.name: s for s in finished}
        self.assertIn("parent", by_name)
        self.assertIn("child", by_name)
        self.assertEqual(by_name["parent"].attributes.get("service"), "helix")
        self.assertEqual(by_name["child"].attributes.get("after_start"), "forwarded")
        self.assertEqual(by_name["child"].parent.span_id, by_name["parent"].context.span_id)


if __name__ == "__main__":
    unittest.main()
