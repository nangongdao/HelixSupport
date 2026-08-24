"""Online drift monitoring with automated canary stop (ROADMAP 43.5 item 4).

The monitor is the safety net *behind* the eval gate (ADR-014): the gate
blocks a bad candidate before it enters canary routing, this module pulls it
back out when live traffic proves it worse than offline evaluation believed.

Signals come from two existing surfaces — nothing new is written at turn
time:

- **quality buckets** (:meth:`QualityService.list_buckets`) supply
  escalation-rate and negative-feedback-rate per day/prompt-version;
- **audit events** (:meth:`Database.export_audit_events`) supply refusal
  counts: ``turn.model_denied``, ``turn.budget_exceeded`` and
  ``tool.denied``.

When any configured threshold breaches over the look-back window, every
currently-canary prompt version of the affected tenant is cleared back to
draft (:meth:`PromptRegistry.clear_canary`, which audits
``prompt_version.canary_cleared``) and the sweep itself audits one
``ai.drift_canary_stopped`` event naming the breached signals. A tenant with
no canary still records the breach event so persistent drift on stable
traffic stays visible; the sweep never touches active versions — rollback to
a retired version remains an explicit operator decision.

The run is a housekeeping callable: failures are logged, never fatal, and
the whole sweep is disabled unless ``DRIFT_ENABLED=1``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.database import Database
from app.prompts import PromptRegistry
from app.quality import QualityService

logger = logging.getLogger("helix")

# Audit event types counted as refusal signals.
MODEL_DENIAL_EVENTS = ("turn.model_denied", "turn.budget_exceeded")
TOOL_DENIAL_EVENTS = ("tool.denied",)

__all__ = [
    "DriftMonitor",
    "DriftReport",
    "MODEL_DENIAL_EVENTS",
    "TOOL_DENIAL_EVENTS",
]


@dataclass(frozen=True)
class DriftSignal:
    """One breached threshold over the monitoring window."""

    kind: str
    value: float
    limit: float


@dataclass(frozen=True)
class DriftReport:
    """Outcome of one monitoring sweep for one tenant."""

    tenant_id: str
    signals: list[DriftSignal]
    stopped_canaries: list[dict[str, Any]]

    @property
    def breached(self) -> bool:
        return bool(self.signals)


class DriftMonitor:
    """Threshold sweep over quality buckets and denial audit counts."""

    def __init__(
        self,
        database: Database,
        settings: Settings,
        *,
        quality_service: QualityService | None = None,
        prompt_registry: PromptRegistry | None = None,
        now: Any = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.quality_service = quality_service or QualityService(database)
        self.prompt_registry = prompt_registry or PromptRegistry(database)
        # Injectable clock keeps the window math testable.
        self._now = now or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------- signals

    def collect_signals(self, tenant_id: str) -> list[DriftSignal]:
        """Evaluate every configured threshold for one tenant."""
        settings = self.settings
        until = self._now()
        since = until - timedelta(days=settings.drift_window_days - 1)
        since_date = since.strftime("%Y-%m-%d")
        until_date = until.strftime("%Y-%m-%d")
        signals: list[DriftSignal] = []

        turns = self._turn_count(tenant_id, since_date, until_date)
        if turns >= settings.drift_min_turns:
            signals.extend(self._rate_signals(tenant_id, since_date, until_date, turns))
        else:
            logger.info(
                "drift.sample_too_small tenant=%s turns=%s min=%s",
                tenant_id,
                turns,
                settings.drift_min_turns,
            )
        signals.extend(self._denial_signals(tenant_id, until))
        return signals

    def _rate_signals(
        self, tenant_id: str, since_date: str, until_date: str, turns: int
    ) -> list[DriftSignal]:
        settings = self.settings
        signals: list[DriftSignal] = []
        escalations = 0
        negatives = 0
        try:
            buckets = self.quality_service.list_buckets(
                tenant_id, since=since_date, until=until_date
            )
        except ValueError:
            return signals
        for bucket in buckets:
            escalations += int(bucket.get("escalation_count") or 0)
            negatives += int(bucket.get("negative_feedback_count") or 0)
        if (
            settings.drift_max_escalation_rate is not None
            and escalations / turns > settings.drift_max_escalation_rate
        ):
            signals.append(
                DriftSignal(
                    "escalation_rate",
                    round(escalations / turns, 4),
                    settings.drift_max_escalation_rate,
                )
            )
        if (
            settings.drift_max_negative_rate is not None
            and negatives / turns > settings.drift_max_negative_rate
        ):
            signals.append(
                DriftSignal(
                    "negative_feedback_rate",
                    round(negatives / turns, 4),
                    settings.drift_max_negative_rate,
                )
            )
        return signals

    def _denial_signals(self, tenant_id: str, until: datetime) -> list[DriftSignal]:
        settings = self.settings
        since_iso = (until - timedelta(days=settings.drift_window_days)).isoformat(
            timespec="microseconds"
        )
        signals: list[DriftSignal] = []
        model_denials = self._count_events(tenant_id, MODEL_DENIAL_EVENTS, since_iso)
        tool_denials = self._count_events(tenant_id, TOOL_DENIAL_EVENTS, since_iso)
        if (
            settings.drift_max_model_denials is not None
            and model_denials > settings.drift_max_model_denials
        ):
            signals.append(
                DriftSignal(
                    "model_denials", float(model_denials), float(settings.drift_max_model_denials)
                )
            )
        if (
            settings.drift_max_tool_denials is not None
            and tool_denials > settings.drift_max_tool_denials
        ):
            signals.append(
                DriftSignal(
                    "tool_denials", float(tool_denials), float(settings.drift_max_tool_denials)
                )
            )
        return signals

    def _turn_count(self, tenant_id: str, since_date: str, until_date: str) -> int:
        try:
            buckets = self.quality_service.list_buckets(
                tenant_id, since=since_date, until=until_date
            )
        except ValueError:
            return 0
        return sum(int(bucket.get("turn_count") or 0) for bucket in buckets)

    def _count_events(self, tenant_id: str, event_types: tuple[str, ...], since_iso: str) -> int:
        total = 0
        for event_type in event_types:
            rows = self.database.export_audit_events(
                tenant_id,
                event_type=event_type,
                since=since_iso,
                limit=2000,
            )
            total += len(rows)
        return total

    # ------------------------------------------------------------ response

    def run_once(self, tenants: list[str] | None = None) -> list[DriftReport]:
        """Sweep every (or given) tenant; stop canaries where thresholds breach.

        Returns one report per tenant that produced at least one signal or one
        canary stop. Safe to call repeatedly: clearing an already-draft canary
        is a no-op and the audit event documents each sweep's findings.
        """
        if not self.settings.drift_enabled:
            return []
        tenant_ids = tenants if tenants is not None else self._list_tenants()
        reports: list[DriftReport] = []
        for tenant_id in tenant_ids:
            signals = self.collect_signals(tenant_id)
            if not signals:
                continue
            stopped = self._stop_canaries(tenant_id, signals)
            reports.append(
                DriftReport(tenant_id=tenant_id, signals=signals, stopped_canaries=stopped)
            )
        return reports

    def _list_tenants(self) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute("SELECT id FROM tenants").fetchall()
        return [str(row["id"]) for row in rows]

    def _stop_canaries(self, tenant_id: str, signals: list[DriftSignal]) -> list[dict[str, Any]]:
        """Clear every canary of the tenant and audit the drift response."""
        stopped: list[dict[str, Any]] = []
        reason = "; ".join(f"{signal.kind}={signal.value}>{signal.limit}" for signal in signals)
        for row in self._canary_rows(tenant_id):
            name = str(row["name"])
            version = self.prompt_registry.clear_canary(
                tenant_id, name, "drift-monitor", reason=reason
            )
            if version is not None:
                stopped.append({"name": name, "version": version.version})
        self.database.audit(
            tenant_id,
            None,
            "drift-monitor",
            "ai.drift_canary_stopped",
            {
                "signals": [{"kind": s.kind, "value": s.value, "limit": s.limit} for s in signals],
                "stopped_canaries": stopped,
            },
        )
        return stopped

    def _canary_rows(self, tenant_id: str) -> list[Any]:
        with self.database.connect() as connection:
            return connection.execute(
                """SELECT id, name, version FROM prompt_versions
                WHERE tenant_id = ? AND status = 'canary'""",
                (tenant_id,),
            ).fetchall()


def report_payload(reports: list[DriftReport]) -> str:
    """Compact JSON summary for logs/ops tooling."""
    return json.dumps(
        [
            {
                "tenant_id": report.tenant_id,
                "signals": [s.kind for s in report.signals],
                "stopped": [c["name"] for c in report.stopped_canaries],
            }
            for report in reports
        ],
        ensure_ascii=False,
    )
