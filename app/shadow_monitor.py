"""Shadow traffic monitoring and automated degradation.

Monitors shadow traffic comparison results over a rolling window and triggers
automated responses when v2 exhibits unacceptable drift from v1:
- Mismatch rate exceeds threshold → reduce sampling rate
- Latency regression exceeds threshold → alert and optionally disable

Architecture follows the drift_monitor.py pattern: pure-function evaluators
that operate on windowed metrics, with fail-safe defaults when signals are
unavailable.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.config import Settings
from app.database import Database

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ShadowSignals:
    """Aggregated shadow traffic metrics over a window."""

    total_comparisons: int
    mismatch_count: int
    mismatch_rate: float
    v1_p95_latency_ms: float | None
    v2_p95_latency_ms: float | None
    latency_regression_ms: float | None


@dataclass(frozen=True)
class ShadowMonitorThresholds:
    """Thresholds for shadow traffic quality gates."""

    max_mismatch_rate: float = 0.05  # 5%
    max_latency_regression_ms: float = 200.0  # v2 can be 200ms slower than v1 P95
    min_comparisons: int = 100  # minimum sample size before triggering


def evaluate_shadow_health(
    signals: ShadowSignals,
    thresholds: ShadowMonitorThresholds,
) -> tuple[bool, str]:
    """Evaluate whether v2 shadow traffic is healthy.

    Returns (is_healthy, reason). When unhealthy, reason describes the breach.
    """
    if signals.total_comparisons < thresholds.min_comparisons:
        return True, "insufficient_sample"

    if signals.mismatch_rate > thresholds.max_mismatch_rate:
        return (
            False,
            f"mismatch_rate={signals.mismatch_rate:.2%} exceeds {thresholds.max_mismatch_rate:.2%}",
        )

    if (
        signals.latency_regression_ms is not None
        and signals.latency_regression_ms > thresholds.max_latency_regression_ms
    ):
        return (
            False,
            (
                f"latency_regression={signals.latency_regression_ms:.0f}ms exceeds "
                f"{thresholds.max_latency_regression_ms:.0f}ms"
            ),
        )

    return True, "healthy"


def collect_shadow_signals(
    db: Database,
    tenant_id: str | None,
    route: str | None,
    window_hours: int = 24,
) -> ShadowSignals:
    """Collect shadow traffic metrics over the specified rolling window.

    When tenant_id is None, aggregates across all tenants.
    When route is None, aggregates across all routes.
    """
    from app.db._util import utc_now

    window_start = (datetime.fromisoformat(utc_now()) - timedelta(hours=window_hours)).isoformat()

    query_parts = [
        "SELECT",
        "  COUNT(*) as total,",
        (
            "  SUM(CASE WHEN json_array_length(fields_mismatched) > 0 "
            "      THEN 1 ELSE 0 END) as mismatches,"
        ),
        "  json_group_array(v1_latency_ms) as v1_latencies,",
        "  json_group_array(v2_latency_ms) as v2_latencies",
        "FROM shadow_traffic_comparisons",
        "WHERE created_at >= ?",
    ]
    params: list[str | int] = [window_start]

    if tenant_id:
        query_parts.append("  AND tenant_id = ?")
        params.append(tenant_id)

    if route:
        query_parts.append("  AND route = ?")
        params.append(route)

    with db.connect() as conn:
        row = conn.execute("\n".join(query_parts), tuple(params)).fetchone()

    if not row or row["total"] == 0:
        return ShadowSignals(
            total_comparisons=0,
            mismatch_count=0,
            mismatch_rate=0.0,
            v1_p95_latency_ms=None,
            v2_p95_latency_ms=None,
            latency_regression_ms=None,
        )

    total = row["total"]
    mismatches = row["mismatches"]
    mismatch_rate = mismatches / total if total > 0 else 0.0

    v1_latencies_raw = json.loads(row["v1_latencies"])
    v2_latencies_raw = json.loads(row["v2_latencies"])

    v1_latencies = [x for x in v1_latencies_raw if x is not None]
    v2_latencies = [x for x in v2_latencies_raw if x is not None]

    v1_p95 = _percentile(v1_latencies, 0.95) if v1_latencies else None
    v2_p95 = _percentile(v2_latencies, 0.95) if v2_latencies else None

    latency_regression = None
    if v1_p95 is not None and v2_p95 is not None:
        latency_regression = v2_p95 - v1_p95

    return ShadowSignals(
        total_comparisons=total,
        mismatch_count=mismatches,
        mismatch_rate=mismatch_rate,
        v1_p95_latency_ms=v1_p95,
        v2_p95_latency_ms=v2_p95,
        latency_regression_ms=latency_regression,
    )


def _percentile(values: list[float], p: float) -> float:
    """Compute the p-th percentile (0 < p < 1) of a sorted list."""
    if not values:
        return 0.0
    sorted_values = sorted(values)
    k = (len(sorted_values) - 1) * p
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return sorted_values[f]


def monitor_shadow_traffic(db: Database, settings: Settings) -> None:
    """Periodic housekeeping: check shadow traffic health and respond to drift.

    This function is called by the turn worker housekeeping loop when
    shadow traffic is enabled. It aggregates comparison results and triggers
    automated responses when thresholds are breached.
    """
    if not settings.shadow_traffic_enabled:
        return

    thresholds = ShadowMonitorThresholds()
    signals = collect_shadow_signals(db, tenant_id=None, route=None, window_hours=24)

    is_healthy, reason = evaluate_shadow_health(signals, thresholds)

    if not is_healthy:
        logger.warning(
            "shadow.traffic_unhealthy",
            extra={
                "reason": reason,
                "total_comparisons": signals.total_comparisons,
                "mismatch_rate": signals.mismatch_rate,
                "v1_p95_ms": signals.v1_p95_latency_ms,
                "v2_p95_ms": signals.v2_p95_latency_ms,
                "latency_regression_ms": signals.latency_regression_ms,
            },
        )

        # Automated response: in a full implementation, this would dynamically
        # reduce the sampling rate or disable shadow traffic entirely.
        # For now, we just log the alert.
        # TODO: implement dynamic sampling rate adjustment or auto-disable
