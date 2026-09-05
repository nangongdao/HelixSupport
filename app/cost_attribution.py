"""Inference cost attribution and anomaly detection (ROADMAP 2.3.3).

Records per-inference token usage and USD cost into ``inference_costs``,
upserts the daily tenant/provider/model rollup in the same transaction, and
flags spend anomalies when a tenant's day-over-day cost exceeds its baseline.

``cost_usd`` is always recorded exactly as computed by the provider layer —
``record_inference_cost`` never estimates; rows without a known price carry
``cost_usd = NULL`` and are excluded from the USD aggregates while still
counting toward token totals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import uuid4

from app.database import Database, utc_now
from app.model_provider import ModelResponse

logger = logging.getLogger(__name__)

ANOMALY_FACTOR = 2.0


def record_model_response(
    service: CostAttributionService | None,
    tenant_id: str | None,
    response: ModelResponse | None,
    context: InferenceContext | None = None,
) -> None:
    """Best-effort record of one model inference; never raises.

    Wired into every model call site (triage, translation, copilot,
    summaries) so the attribution tables reflect real inference. Rows without
    vendor-reported usage are skipped -- there is nothing to attribute.
    """
    if service is None or tenant_id is None or response is None:
        return
    usage = response.usage or {}
    if not usage.get("prompt_tokens") and not usage.get("completion_tokens"):
        return
    try:
        service.record_inference_cost(
            tenant_id,
            provider=response.provider or "unknown",
            model=response.model or "unknown",
            prompt_tokens=int(usage.get("prompt_tokens", 0)),
            completion_tokens=int(usage.get("completion_tokens", 0)),
            cost_usd=response.cost_usd,
            context=context,
            latency_ms=response.latency_ms,
        )
    except Exception:
        logger.debug(
            "cost.record_failed",
            extra={"tenant_id": tenant_id, "model": response.model},
        )


@dataclass(frozen=True)
class CostTolerance:
    """Baseline window and spike factor for the anomaly check."""

    baseline_days: int = 7
    anomaly_factor: float = ANOMALY_FACTOR


@dataclass(frozen=True)
class InferenceContext:
    """Attribution dimensions for one model inference (ROADMAP 2.3.3).

    Optional per-inference context recorded alongside the numeric cost so the
    dashboard can attribute spend to conversations, agents, and prompt
    versions. All fields are best-effort: callers that lack a value pass
    ``None`` and the row still records.
    """

    conversation_id: str | None = None
    turn_id: str | None = None
    message_id: str | None = None
    agent: str | None = None
    prompt_version: str | None = None


class CostAttributionService:
    """Writes inference cost rows and reads attribution summaries."""

    def __init__(self, database: Database, *, tolerance: CostTolerance | None = None) -> None:
        self.database = database
        self.tolerance = tolerance or CostTolerance()

    # ---------------------------------------------------------------- writing

    def record_inference_cost(
        self,
        tenant_id: str,
        *,
        provider: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float | None,
        context: InferenceContext | dict[str, Any] | None = None,
        latency_ms: int | None = None,
        date_str: str | None = None,
    ) -> None:
        """Persist one inference and upsert the daily aggregate atomically."""
        day = date_str or utc_now()[:10]
        row_id = str(uuid4())
        prompt = max(0, int(prompt_tokens))
        completion = max(0, int(completion_tokens))
        if isinstance(context, dict):
            context = InferenceContext(**context)
        ctx = context or InferenceContext()
        # Explicit date_str backfills historical rows: the per-inference
        # created_at aligns with the rollup date so date-filtered detail
        # queries match. Real calls omit date_str and use the true timestamp.
        created_at = f"{day}T00:00:00+00:00" if date_str else utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """
                INSERT INTO inference_costs (
                    id, tenant_id, conversation_id, turn_id, message_id,
                    agent, prompt_version, provider, model, prompt_tokens,
                    completion_tokens, cost_usd, latency_ms, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row_id,
                    tenant_id,
                    ctx.conversation_id,
                    ctx.turn_id,
                    ctx.message_id,
                    ctx.agent,
                    ctx.prompt_version,
                    provider,
                    model,
                    prompt,
                    completion,
                    cost_usd,
                    latency_ms,
                    created_at,
                ),
            )
            connection.execute(
                """
                INSERT INTO tenant_cost_daily (
                    tenant_id, date, provider, model,
                    turn_count, prompt_tokens, completion_tokens, cost_usd
                ) VALUES (?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(tenant_id, date, provider, model) DO UPDATE SET
                    turn_count = turn_count + 1,
                    prompt_tokens = prompt_tokens + excluded.prompt_tokens,
                    completion_tokens = completion_tokens + excluded.completion_tokens,
                    cost_usd = COALESCE(cost_usd, 0) + COALESCE(excluded.cost_usd, 0)
                """,
                (tenant_id, day, provider, model, prompt, completion, cost_usd),
            )

    # ---------------------------------------------------------------- reading

    def get_tenant_cost_summary(
        self, tenant_id: str, since: str | None = None, until: str | None = None
    ) -> dict[str, Any]:
        """Aggregate a tenant's cost over a date range."""
        clauses = ["tenant_id = ?"]
        values: list[Any] = [tenant_id]
        if since:
            clauses.append("date >= ?")
            values.append(since)
        if until:
            clauses.append("date <= ?")
            values.append(until)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(SUM(turn_count), 0) AS turn_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(cost_usd), 0.0) AS cost_usd "
                f"FROM tenant_cost_daily WHERE {' AND '.join(clauses)}",
                values,
            ).fetchone()
        return {
            "tenant_id": tenant_id,
            "turn_count": int(row["turn_count"]),
            "prompt_tokens": int(row["prompt_tokens"]),
            "completion_tokens": int(row["completion_tokens"]),
            "cost_usd": round(float(row["cost_usd"]), 6),
        }

    def get_cost_by_dimension(
        self,
        tenant_id: str,
        dimension: str,
        *,
        date_str: str | None = None,
    ) -> list[dict[str, Any]]:
        """Break down daily spend by ``provider``, ``model``, ``agent`` or ``date``.

        ``agent`` and ``prompt_version`` are attributed from the per-inference
        rows rather than the daily rollup (which only groups by
        provider/model); rows with no agent (e.g. pre-2.3.5 inference) fall
        under ``"unknown"``.
        """
        if dimension not in {"provider", "model", "agent", "prompt_version", "date"}:
            raise ValueError(
                "dimension must be one of provider, model, agent, prompt_version, date"
            )
        if dimension in {"provider", "model", "date"}:
            clauses = ["tenant_id = ?"]
            values: list[Any] = [tenant_id]
            if date_str:
                clauses.append("date = ?")
                values.append(date_str)
            with self.database.connect() as connection:
                rows = connection.execute(
                    f"SELECT {dimension}, COALESCE(SUM(turn_count), 0) AS turn_count, "
                    "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                    "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                    "COALESCE(SUM(cost_usd), 0.0) AS cost_usd "
                    f"FROM tenant_cost_daily WHERE {' AND '.join(clauses)} "
                    f"GROUP BY {dimension} ORDER BY cost_usd DESC",
                    values,
                ).fetchall()
            return [
                {
                    dimension: row[dimension],
                    "turn_count": int(row["turn_count"]),
                    "prompt_tokens": int(row["prompt_tokens"]),
                    "completion_tokens": int(row["completion_tokens"]),
                    "cost_usd": round(float(row["cost_usd"]), 6),
                }
                for row in rows
            ]
        dimension_column = "agent" if dimension == "agent" else "prompt_version"
        clauses = ["tenant_id = ?"]
        values = [tenant_id]
        if date_str:
            clauses.append("substr(created_at, 1, 10) = ?")
            values.append(date_str)
        with self.database.connect() as connection:
            rows = connection.execute(
                f"SELECT COALESCE({dimension_column}, 'unknown') AS {dimension_column}, "
                "COUNT(*) AS turn_count, "
                "COALESCE(SUM(prompt_tokens), 0) AS prompt_tokens, "
                "COALESCE(SUM(completion_tokens), 0) AS completion_tokens, "
                "COALESCE(SUM(cost_usd), 0.0) AS cost_usd "
                f"FROM inference_costs WHERE {' AND '.join(clauses)} "
                f"GROUP BY {dimension_column} ORDER BY cost_usd DESC",
                values,
            ).fetchall()
        return [
            {
                dimension: row[dimension_column],
                "turn_count": int(row["turn_count"]),
                "prompt_tokens": int(row["prompt_tokens"]),
                "completion_tokens": int(row["completion_tokens"]),
                "cost_usd": round(float(row["cost_usd"]), 6),
            }
            for row in rows
        ]

    # -------------------------------------------------------------- anomalies

    def check_anomaly(self, tenant_id: str, *, today: str | None = None) -> dict[str, Any]:
        """Compare the current day's cost to the tenant's recent baseline.

        Returns ``{"anomaly": bool, "today_cost_usd": float,
        "baseline_cost_usd": float, "factor": float}``. The baseline is the
        average daily cost over ``baseline_days`` (default 7) ending the day
        before ``today``; an anomaly fires when that day's cost exceeds
        ``factor`` (default 2.0) times the baseline (ignored when the
        baseline is 0). ``today`` defaults to the real current date; the
        drift monitor injects its own clock so window math stays testable.

        The window bounds are computed in Python (ISO date strings) rather
        than with SQLite's two-argument ``date()`` modifier — PostgreSQL has
        no such overload and the pg_compat shims don't add one — and the
        divide guard is ``NULLIF(COUNT(*), 0)`` because PG's ``COUNT(*)``
        returns ``bigint``, which does not match the int/int ``max()`` shim.
        Both backends run the identical query.
        """
        day = today or utc_now()[:10]
        cutoff = (
            datetime.strptime(day, "%Y-%m-%d").date() - timedelta(days=self.tolerance.baseline_days)
        ).isoformat()
        with self.database.connect() as connection:
            today_row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd), 0.0) AS cost_usd "
                "FROM tenant_cost_daily WHERE tenant_id = ? AND date = ?",
                (tenant_id, day),
            ).fetchone()
            baseline_row = connection.execute(
                "SELECT COALESCE(SUM(cost_usd) / NULLIF(COUNT(*), 0), 0.0) AS avg_cost "
                "FROM tenant_cost_daily "
                "WHERE tenant_id = ? AND date < ? AND date >= ?",
                (tenant_id, day, cutoff),
            ).fetchone()
        today_cost = float(today_row["cost_usd"])
        baseline = float(baseline_row["avg_cost"])
        factor = (today_cost / baseline) if baseline > 0 else 0.0
        return {
            "anomaly": baseline > 0 and factor >= self.tolerance.anomaly_factor,
            "today_cost_usd": round(today_cost, 6),
            "baseline_cost_usd": round(baseline, 6),
            "factor": round(factor, 2),
        }


__all__ = [
    "ANOMALY_FACTOR",
    "CostAttributionService",
    "CostTolerance",
    "InferenceContext",
    "record_model_response",
]
