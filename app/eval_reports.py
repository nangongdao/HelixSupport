"""Immutable evaluation reports and promotion gate (Phase 41.5, AI-001).

The adversarial + golden runs must leave audit evidence a model/prompt change
cannot re-write after the fact.  Every run persists its report into a write-once
store (``DiskWormStore`` over ``EVAL_WORM_DIR``); a second write to the same
run id is refused, and a later replacement/loss of the object surfaces as
``WormIntegrityError``.  Promotion (canary enable) is a separate record in the
same store, so "model X passed the safety gate at version Y" stays traceable
(Gate B item 5).

``decide_promotion`` is the gate itself: five conditions must all hold
(adversarial floor, golden floor, quality not below active, p95 budget, cost
budget).  On any failure it returns the blocking reasons plus a disclosure
record the caller persists to the same WORM store — a canary bump has no
silent side door.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import Settings
from app.worm_store import DiskWormStore, WormUnavailableError, worm_path

logger = logging.getLogger(__name__)

REPORT_KIND = "evaluation_report"
PROMOTION_KIND = "promotion_record"
_QUALITY_KEYS = ("pass_rate", "mean_confidence", "citation_coverage")

# ---------------------------------------------------------------------------
# Immutable report persistence
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvalReportStore:
    """WORM-backed store for evaluation reports and promotion records."""

    directory: Path

    def _store(self) -> DiskWormStore:
        return DiskWormStore(self.directory)

    def write_evaluation_report(self, run_id: str, report: dict[str, Any]) -> None:
        """Persist an evaluation report exactly once under ``run_id``."""
        self._store().write_once(
            run_id,
            {"kind": REPORT_KIND, "run_id": run_id, "report": report},
        )

    def write_promotion_record(self, candidate: str, record: dict[str, Any]) -> None:
        """Persist a promotion record under a stable per-candidate object id."""
        object_id = promotion_object_id(candidate)
        self._store().write_once(
            object_id,
            {"kind": PROMOTION_KIND, "candidate": candidate, "record": record},
        )

    def read_all(self) -> list[dict[str, Any]]:
        """Return every stored object; raises ``WormIntegrityError`` on tamper."""
        return list(self._store().read_all())


def generate_report_object_id(prefix: str = "eval") -> str:
    """Return a fresh run id the harness stamps into the persistent report."""
    return f"{prefix}-{uuid4().hex[:8]}"


def promotion_object_id(candidate: str) -> str:
    """Stable WORM object id for one candidate (charset ``[a-zA-Z0-9_-]``)."""
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in candidate)
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:8]
    return f"{slug}-{digest}promo"


# ---------------------------------------------------------------------------
# Promotion gate
# ---------------------------------------------------------------------------


def decide_promotion(
    *,
    adversarial_report: dict[str, Any] | None,
    golden_report: dict[str, Any] | None,
    active_metrics: dict[str, Any],
    settings: Settings,
) -> tuple[bool, list[str]]:
    """Evaluate every gate condition; all must pass to promote.

    A missing mandatory report (adversarial or golden) blocks promotion —
    the safety gate fails closed.
    """
    reasons: list[str] = []
    if adversarial_report is None:
        reasons.append("no adversarial report")
    if golden_report is None:
        reasons.append("no golden report")
    if reasons:
        return False, reasons
    assert adversarial_report is not None and golden_report is not None

    adversarial_rate = pass_rate(adversarial_report)
    golden_rate = pass_rate(golden_report)
    if adversarial_rate < settings.eval_adversarial_floor:
        reasons.append(
            f"adversarial pass rate {adversarial_rate:.2%} < floor "
            f"{settings.eval_adversarial_floor:.2%}"
        )
    if golden_rate < settings.eval_golden_floor:
        reasons.append(
            f"golden pass rate {golden_rate:.2%} < floor {settings.eval_golden_floor:.2%}"
        )

    for key in _QUALITY_KEYS:
        new = metric(adversarial_report, key)
        baseline = metric(active_metrics, key)
        if new < baseline:
            reasons.append(f"{key} {new:.4f} < active {baseline:.4f}")

    p95 = max(metric(adversarial_report, "p95_latency_ms"), metric(golden_report, "p95_latency_ms"))
    if p95 > settings.eval_p95_budget_ms:
        reasons.append(f"p95 {p95:.0f} ms > budget {settings.eval_p95_budget_ms:.0f} ms")
    cost = max(
        metric(adversarial_report, "estimated_cost_usd"),
        metric(golden_report, "estimated_cost_usd"),
    )
    if cost > settings.eval_cost_budget_usd:
        reasons.append(f"cost {cost:.4f} USD > budget {settings.eval_cost_budget_usd:.4f} USD")
    return not reasons, reasons


def promotion_record(
    *,
    run_id: str,
    candidate: str,
    active: str,
    passed: bool,
    reasons: list[str],
    adversarial_report: dict[str, Any],
    golden_report: dict[str, Any],
    active_metrics: dict[str, Any],
    settings: Settings,
) -> dict[str, Any]:
    """Build the full gate-disclosure record for one promotion decision."""
    return {
        "run_id": run_id,
        "candidate": candidate,
        "active": active,
        "passed": passed,
        "reasons": reasons,
        "evaluated_at": _iso_now(),
        "environment": settings.eval_environment,
        "floors": {
            "adversarial": settings.eval_adversarial_floor,
            "golden": settings.eval_golden_floor,
            "p95_ms": settings.eval_p95_budget_ms,
            "cost_usd": settings.eval_cost_budget_usd,
        },
        "adversarial": _summary(adversarial_report),
        "golden": _summary(golden_report),
        "active_metrics": active_metrics,
    }


def pass_rate(report: dict[str, Any]) -> float:
    """Pass rate of a report, honouring either the raw counts or the rate."""
    if isinstance(report.get("pass_rate"), (int, float)):
        return float(report["pass_rate"])
    total = report.get("total") or 0
    passed = report.get("passed") or 0
    return float(passed / total) if total else 0.0


def metric(report: dict[str, Any], key: str) -> float:
    value = report.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _summary(report: dict[str, Any]) -> dict[str, float]:
    return {
        "pass_rate": round(pass_rate(report), 4),
        "mean_confidence": round(metric(report, "mean_confidence"), 4),
        "citation_coverage": round(metric(report, "citation_coverage"), 4),
        "p95_latency_ms": round(metric(report, "p95_latency_ms"), 1),
        "estimated_cost_usd": round(metric(report, "estimated_cost_usd"), 4),
    }


def _iso_now() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat(timespec="milliseconds")


__all__ = [
    "PROMOTION_KIND",
    "REPORT_KIND",
    "EvalReportStore",
    "WormUnavailableError",
    "decide_promotion",
    "generate_report_object_id",
    "metric",
    "pass_rate",
    "promotion_object_id",
    "promotion_record",
    "worm_path",
]
