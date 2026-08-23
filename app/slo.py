"""SLO burn-rate alerting (ROADMAP 42.6).

Replaces single-threshold alerting with the multi-window, multi-burn-rate
approach from the Google SRE workbook: an alert fires only when BOTH a fast
window and a long window exceed their burn-rate thresholds simultaneously —
a momentary spike no longer pages anyone, and a slow sustained burn cannot
hide under a quiet last five minutes.

Severity separation (42.6): ``page`` burns budget fast (14.4× over 1h+5m —
2% of a 30-day budget in one hour) and wakes a human; ``ticket`` catches the
slower 6× burn (6h+30m) and files work instead.

The evaluator is pure: callers supply per-window ``(bad_events,
total_events)`` counts from any metrics source (Prometheus query, telemetry
snapshot, or a drill's synthetic series), so the firing rules are unit-test
able without a scraper.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True)
class SLOWindow:
    """One half of a multi-window pair."""

    name: str
    minutes: int
    burn_rate_threshold: float


@dataclass(frozen=True)
class SLODefinition:
    """A service-level objective with its severity class and window pair."""

    name: str
    target_availability: float
    severity: str  # "page" | "ticket"
    windows: tuple[SLOWindow, ...]
    description: str = ""

    def __post_init__(self) -> None:
        if not 0 < self.target_availability <= 1:
            raise ValueError("target_availability must be in (0, 1]")
        if self.severity not in {"page", "ticket"}:
            raise ValueError("severity must be 'page' or 'ticket'")
        if len(self.windows) != 2:
            raise ValueError("multi-window alerting requires exactly two windows")


# Standard presets (SRE workbook ch.5): page = 14.4× fast pair, ticket = 6×
# slow pair. Both windows of a pair must breach for the alert to fire.
PAGE_SLO = SLODefinition(
    name="api-availability-page",
    target_availability=0.995,
    severity="page",
    windows=(
        SLOWindow(name="fast", minutes=5, burn_rate_threshold=14.4),
        SLOWindow(name="long", minutes=60, burn_rate_threshold=14.4),
    ),
    description="API availability — wake a human on a 2%-budget-per-hour burn",
)
TICKET_SLO = SLODefinition(
    name="api-availability-ticket",
    target_availability=0.995,
    severity="ticket",
    windows=(
        SLOWindow(name="fast", minutes=30, burn_rate_threshold=6.0),
        SLOWindow(name="long", minutes=360, burn_rate_threshold=6.0),
    ),
    description="API availability — file work on a 5%-budget-per-6h burn",
)

DEFAULT_SLOS: tuple[SLODefinition, ...] = (PAGE_SLO, TICKET_SLO)


@dataclass(frozen=True)
class SloAlert:
    """A fired alert with everything on-call needs to start correlating."""

    slo_name: str
    severity: str
    error_budget_consumed_percent: float
    window_rates: dict[str, float] = field(default_factory=dict)
    thresholds: dict[str, float] = field(default_factory=dict)


def _error_rate(bad: float, total: float) -> float | None:
    if total <= 0:
        return None
    return max(0.0, bad) / total


def allowed_error_rate(slo: SLODefinition) -> float:
    """The error rate that consumes budget exactly at 1×."""
    return 1.0 - slo.target_availability


def evaluate_slo(
    slo: SLODefinition,
    window_counts: Mapping[str, tuple[float, float]],
) -> SloAlert | None:
    """Fire only when every window breaches its threshold simultaneously.

    ``window_counts`` maps each :class:`SLOWindow` name to that window's
    ``(bad_events, total_events)``. A window with no traffic never blocks
    (and never fires): absent/empty windows are treated as unknown, so a
    quiet long window suppresses the page instead of amplifying it.
    """
    rates: dict[str, float] = {}
    thresholds: dict[str, float] = {}
    breached_all = True
    for window in slo.windows:
        counts = window_counts.get(window.name)
        threshold_rate = window.burn_rate_threshold * allowed_error_rate(slo)
        thresholds[window.name] = round(threshold_rate, 6)
        if counts is None or counts[1] <= 0:
            breached_all = False
            continue
        bad, total = counts
        rate = _error_rate(bad, total)
        assert rate is not None
        rates[window.name] = round(rate, 6)
        if rate <= threshold_rate:
            breached_all = False
    if not breached_all:
        return None
    long_window = max(slo.windows, key=lambda w: w.minutes)
    counts = window_counts.get(long_window.name, (0.0, 0.0))
    budget_burn = (
        (_error_rate(counts[0], counts[1]) or 0.0) / allowed_error_rate(slo)
        if counts[1] > 0
        else 0.0
    )
    return SloAlert(
        slo_name=slo.name,
        severity=slo.severity,
        error_budget_consumed_percent=round(
            min(100.0, budget_burn * 100.0 * (long_window.minutes / (30 * 24 * 60))),
            4,
        ),
        window_rates=rates,
        thresholds=thresholds,
    )


def evaluate_all(
    slos: tuple[SLODefinition, ...] = DEFAULT_SLOS,
    window_counts_by_slo: Mapping[str, Mapping[str, tuple[float, float]]] | None = None,
) -> list[SloAlert]:
    """Evaluate every SLO; returns the fired alerts (page first)."""
    fired: list[SloAlert] = []
    for slo in slos:
        counts = (window_counts_by_slo or {}).get(slo.name)
        if counts is None:
            continue
        alert = evaluate_slo(slo, counts)
        if alert is not None:
            fired.append(alert)
    fired.sort(key=lambda alert: {"page": 0, "ticket": 1}.get(alert.severity, 2))
    return fired