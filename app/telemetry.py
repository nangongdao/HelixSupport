"""Structured tracing and metrics without a hard OpenTelemetry dependency.

This module provides a lightweight tracing API that mirrors OpenTelemetry
concepts (spans, attributes, events) so that it can be backed by the
``opentelemetry`` package when available, while remaining zero-dependency
for local and single-node deployments.

Configuration:
- ``OTEL_EXPORTER_OTLP_ENDPOINT`` – when set, spans are forwarded via OTLP.
- ``OTEL_SERVICE_NAME`` – defaults to ``helix-support``.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from statistics import quantiles
from typing import Any

logger = logging.getLogger(__name__)

_otel_available = False
_tracer_provider = None

try:
    from opentelemetry import trace as _otel_trace  # type: ignore[reportMissingImports]
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore[reportMissingImports]
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.trace import TracerProvider  # type: ignore[reportMissingImports]
    from opentelemetry.sdk.trace.export import (  # type: ignore[reportMissingImports]
        BatchSpanProcessor,
    )

    _otel_available = True
except ImportError:
    pass

_current_span: ContextVar[Span | None] = ContextVar("helix_current_span", default=None)


@dataclass
class Span:
    """A lightweight span record."""

    name: str
    start_time: float = field(default_factory=time.monotonic)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    parent: Span | None = None
    # Set while the span is active and backed by the OpenTelemetry SDK, so
    # attributes/events added after creation also reach the exported span.
    _otel_span: Any = None

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value
        if self._otel_span is not None:
            self._otel_span.set_attribute(key, value)

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        self.events.append({"name": name, "attributes": attributes or {}})
        if self._otel_span is not None:
            self._otel_span.add_event(name, attributes or {})

    def end(self) -> None:
        self.end_time = time.monotonic()

    @property
    def duration_ms(self) -> float | None:
        if self.end_time is None:
            return None
        return round((self.end_time - self.start_time) * 1000, 2)


def configure_tracing() -> None:
    """Initialize OpenTelemetry if available and configured."""
    global _tracer_provider
    if not _otel_available:
        logger.debug("opentelemetry not installed; using lightweight tracing")
        return
    if _tracer_provider is not None:
        return
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
    service_name = os.getenv("OTEL_SERVICE_NAME", "helix-support")
    resource = Resource.create({"service.name": service_name})
    _tracer_provider = TracerProvider(resource=resource)
    if endpoint:
        exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        _tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
    _otel_trace.set_tracer_provider(_tracer_provider)
    logger.info("OpenTelemetry tracing configured: service=%s, endpoint=%s", service_name, endpoint)


@contextmanager
def span(name: str, **attributes: Any) -> Iterator[Span]:
    """Create a tracing span.

    When OpenTelemetry is available and configured, spans are forwarded to
    the OTLP exporter. Otherwise, spans are tracked locally and logged at
    DEBUG level for development.
    """
    parent = _current_span.get()
    record = Span(name=name, parent=parent, attributes=dict(attributes))

    if _otel_available and _tracer_provider is not None:
        # Use the provider built by ``configure_tracing`` directly rather than
        # re-resolving the OTel global: the SDK only accepts the global being
        # set once per process, so relying on ``get_tracer`` here would silently
        # route spans to whatever provider was installed first.
        tracer = _tracer_provider.get_tracer("helix-support")
        with tracer.start_as_current_span(name) as otel_span:
            record._otel_span = otel_span
            for key, value in attributes.items():
                otel_span.set_attribute(key, value)
            token = _current_span.set(record)
            try:
                yield record
            finally:
                record.end()
                _current_span.reset(token)
    else:
        token = _current_span.set(record)
        try:
            yield record
        finally:
            record.end()
            _current_span.reset(token)
            if logger.isEnabledFor(logging.DEBUG):
                logger.debug(
                    "span.complete name=%s duration_ms=%s attrs=%s",
                    name,
                    record.duration_ms,
                    record.attributes,
                )


class TelemetryMetrics:
    """Thread-safe counter and histogram registry."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._histograms: dict[str, list[float]] = {}

    def increment(self, name: str, value: float = 1, **tags: Any) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0) + value

    def observe(self, name: str, value: float, **tags: Any) -> None:
        key = self._key(name, tags)
        with self._lock:
            self._histograms.setdefault(key, []).append(value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            counters = dict(self._counters)
            histograms = {
                key: self._histogram_summary(values) for key, values in self._histograms.items()
            }
        return {"counters": counters, "histograms": histograms}

    @staticmethod
    def _histogram_summary(values: list[float]) -> dict[str, Any]:
        """Aggregate observed values into an add-only summary document.

        Adds P50/P95 percentiles (per ROADMAP 18.2a: the turn segments need
        percentile baselines to reveal the real bottleneck) alongside the
        existing count/sum/avg/min/max fields. ``statistics.quantiles`` works
        for lists with at least one value; a single sample maps to all
        percentiles trivially.
        """
        n = len(values)
        summary: dict[str, Any] = {
            "count": n,
            "sum": round(sum(values), 2) if n else 0,
            "avg": round(sum(values) / n, 2) if n else 0,
            "min": round(min(values), 2) if n else 0,
            "max": round(max(values), 2) if n else 0,
        }
        if n == 1:
            summary.update({"p50": summary["avg"], "p95": summary["avg"], "p99": summary["avg"]})
        elif n:
            # method="inclusive": linear interpolation between nearest data
            # points, with the extremes counted as the 0th/100th percentiles —
            # the common latency percentiles reading. E.g. [10,20,30,40,100]
            # gives p50=30.0, p95=88.0.
            q = quantiles(values, n=100, method="inclusive")
            summary.update(
                {
                    "p50": round(q[49], 2),
                    "p95": round(q[94], 2),
                    "p99": round(q[98], 2),
                }
            )
        else:
            summary.update({"p50": 0, "p95": 0, "p99": 0})
        return summary

    @staticmethod
    def _key(name: str, tags: dict[str, Any]) -> str:
        if not tags:
            return name
        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


metrics = TelemetryMetrics()


def record_shadow_comparison(
    result: str,
    latency_diff_ms: int | None = None,
    route: str | None = None,
) -> None:
    """Record a shadow traffic comparison result.

    Args:
        result: "match", "mismatch", or "error"
        latency_diff_ms: v2_latency - v1_latency (positive = v2 slower)
        route: optional route identifier for per-endpoint tracking
    """
    tags = {"result": result}
    if route:
        tags["route"] = route

    metrics.increment("shadow.comparison_result", 1, **tags)

    if latency_diff_ms is not None:
        metrics.observe("shadow.latency_diff_ms", float(latency_diff_ms), **tags)
