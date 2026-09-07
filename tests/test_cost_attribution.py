"""Tests for cost attribution service (ROADMAP 2.3.3)."""

from __future__ import annotations

import ast
import json
import re
import tempfile
import unittest
from pathlib import Path

from app.agents import TriageAgent
from app.cost_attribution import CostAttributionService, CostTolerance
from app.database import Database, utc_now
from app.model_provider import ModelResponse


def _fresh_service(tmp: str) -> CostAttributionService:
    db = Database(Path(tmp) / "costs.db")
    db.initialize()
    db.ensure_tenant("tenant-1")
    return CostAttributionService(db)


class TestRecordInferenceCost(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.service = _fresh_service(self._tmp.name)

    def tearDown(self) -> None:
        self.service.database.close()
        self._tmp.cleanup()

    def test_record_upserts_daily_aggregate(self) -> None:
        service = self.service
        day = "2026-09-01"
        for _ in range(2):
            service.record_inference_cost(
                "tenant-1",
                provider="openai",
                model="gpt-4.1-mini",
                prompt_tokens=1000,
                completion_tokens=500,
                cost_usd=0.0048,
                date_str=day,
            )
        summary = service.get_tenant_cost_summary("tenant-1", since=day, until=day)
        self.assertEqual(summary["turn_count"], 2)
        self.assertEqual(summary["prompt_tokens"], 2000)
        self.assertEqual(summary["completion_tokens"], 1000)
        self.assertAlmostEqual(summary["cost_usd"], 0.0096, places=6)

    def test_null_cost_is_not_counted(self) -> None:
        service = self.service
        service.record_inference_cost(
            "tenant-1",
            provider="openai",
            model="unknown-model",
            prompt_tokens=10,
            completion_tokens=10,
            cost_usd=None,
            date_str="2026-09-01",
        )
        summary = service.get_tenant_cost_summary("tenant-1", since="2026-09-01")
        self.assertEqual(summary["turn_count"], 1)
        self.assertEqual(summary["cost_usd"], 0.0)
        # NULL 口径：明细表与聚合表都不落 0.0（无定价时不计 USD）
        with self.service.database.connect() as connection:
            daily = connection.execute(
                "SELECT cost_usd FROM tenant_cost_daily WHERE date='2026-09-01'"
            ).fetchone()
            detail = connection.execute(
                "SELECT cost_usd FROM inference_costs WHERE provider='openai'"
            ).fetchone()
        self.assertIsNone(daily["cost_usd"])
        self.assertIsNone(detail["cost_usd"])

    def test_invalid_dimension_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.service.get_cost_by_dimension("tenant-1", "banana")


class TestCostByDimension(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.service = _fresh_service(self._tmp.name)

    def tearDown(self) -> None:
        self.service.database.close()
        self._tmp.cleanup()

    def test_breakdown_by_provider(self) -> None:
        service = self.service
        service.record_inference_cost(
            "tenant-1",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=100,
            completion_tokens=50,
            cost_usd=0.001,
            date_str="2026-09-01",
        )
        service.record_inference_cost(
            "tenant-1",
            provider="anthropic",
            model="claude-3.5",
            prompt_tokens=200,
            completion_tokens=100,
            cost_usd=0.002,
            date_str="2026-09-01",
        )
        rows = service.get_cost_by_dimension("tenant-1", "provider", date_str="2026-09-01")
        self.assertEqual([r["provider"] for r in rows], ["anthropic", "openai"])
        self.assertEqual(rows[0]["cost_usd"], 0.002)


class TestAnomalyDetection(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.service = _fresh_service(self._tmp.name)

    def tearDown(self) -> None:
        self.service.database.close()
        self._tmp.cleanup()

    def test_no_anomaly_within_baseline(self) -> None:
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).date()
        service = self.service
        for day in (
            today - timedelta(days=3),
            today - timedelta(days=2),
            today - timedelta(days=1),
        ):
            service.record_inference_cost(
                "tenant-1",
                provider="openai",
                model="gpt-4.1-mini",
                prompt_tokens=1000,
                completion_tokens=500,
                cost_usd=0.01,
                date_str=day.isoformat(),
            )
        # Today's cost at the same level as the baseline: no anomaly.
        service.record_inference_cost(
            "tenant-1",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=0.01,
            date_str=utc_now()[:10],
        )
        result = service.check_anomaly("tenant-1")
        self.assertFalse(result["anomaly"])
        self.assertAlmostEqual(result["baseline_cost_usd"], 0.01, places=6)
        self.assertAlmostEqual(result["factor"], 1.0, places=2)

    def test_anomaly_when_cost_spikes(self) -> None:
        service = CostAttributionService(
            self.service.database, tolerance=CostTolerance(baseline_days=7, anomaly_factor=2.0)
        )
        # Baseline days relative to the real today: check_anomaly compares
        # against utc_now()'s 7-day window, so an absolute date drifts out of
        # the window as time passes (2026-08-30 baselines broke ~2026-09-06).
        from datetime import datetime, timedelta, timezone

        today = datetime.now(timezone.utc).date()
        baseline_days = [today - timedelta(days=offset) for offset in (3, 2, 1)]
        for day in baseline_days:
            service.record_inference_cost(
                "tenant-1",
                provider="openai",
                model="gpt-4.1-mini",
                prompt_tokens=1000,
                completion_tokens=500,
                cost_usd=0.01,
                date_str=day.isoformat(),
            )
        # A 10x spike on the real today: the anomaly check compares against
        # the real today, so the spike row must land there.
        service.record_inference_cost(
            "tenant-1",
            provider="openai",
            model="gpt-4.1-mini",
            prompt_tokens=1000,
            completion_tokens=500,
            cost_usd=0.10,
            date_str=utc_now()[:10],
        )
        result = service.check_anomaly("tenant-1")
        self.assertTrue(result["anomaly"])
        self.assertGreaterEqual(result["factor"], 2.0)

    def test_no_anomaly_when_baseline_zero(self) -> None:
        result = self.service.check_anomaly("tenant-1")
        self.assertFalse(result["anomaly"])
        self.assertEqual(result["baseline_cost_usd"], 0.0)


class _UsageProvider:
    """ModelProvider stub that reports token usage like a real vendor."""

    def __init__(self, payload: dict, usage: dict) -> None:
        self.payload = payload
        self.usage = usage

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        return ModelResponse(
            content=json.dumps(self.payload, ensure_ascii=False),
            usage=self.usage,
            model="gpt-4.1-mini",
            provider="openai",
            cost_usd=0.0024,
            latency_ms=320,
            model_ref=model_ref,
        )


class TurnPathCostWiringTests(unittest.TestCase):
    """The triage model path must persist cost rows (ROADMAP 2.3.3 wiring)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.service = _fresh_service(self._tmp.name)

    def tearDown(self) -> None:
        self.service.database.close()
        self._tmp.cleanup()

    def test_triage_model_decision_records_cost(self) -> None:
        provider = _UsageProvider(
            {
                "route": "order",
                "intent": "order_status",
                "confidence": 0.9,
                "urgency": "high",
                "reasons": ["order id"],
            },
            {"prompt_tokens": 1200, "completion_tokens": 450},
        )
        agent = TriageAgent(provider, cost_attribution=self.service)
        decision = agent.decide("something vague", tenant_id="tenant-1")
        self.assertEqual(decision.route.value, "order")

        rows = self.service.get_cost_by_dimension("tenant-1", "agent")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["agent"], "triage")
        self.assertAlmostEqual(rows[0]["cost_usd"], 0.0024, places=6)
        self.assertEqual(rows[0]["prompt_tokens"], 1200)
        self.assertEqual(rows[0]["completion_tokens"], 450)

        summary = self.service.get_tenant_cost_summary("tenant-1")
        self.assertEqual(summary["turn_count"], 1)
        self.assertAlmostEqual(summary["cost_usd"], 0.0024, places=6)

    def test_rule_path_skipped_when_no_model_inference(self) -> None:
        agent = TriageAgent(None, cost_attribution=self.service)
        agent.decide("退款", tenant_id="tenant-1")
        self.assertEqual(self.service.get_cost_by_dimension("tenant-1", "agent"), [])


# SQL patterns that are SQLite-only and fail on PostgreSQL: the two-argument
# date(text, modifier) call (no PG equivalent, no pg_compat shim) and MIN/MAX
# wrapping COUNT(*) (PG's COUNT(*) is bigint and misses the int/int max()
# shim). Both shipped in 2.3.0's check_anomaly and were reproduced live.
_DATE_MODIFIER = re.compile(r"\bdate\(\s*[^()]*,")
_MINMAX_COUNT = re.compile(r"\b(?:MIN|MAX)\(\s*[^()]*\bCOUNT\s*\(")
_GUARDS = (
    (
        _DATE_MODIFIER,
        "two-argument date(..., modifier) — compute window bounds in Python and "
        "compare ISO strings instead",
    ),
    (
        _MINMAX_COUNT,
        "MIN/MAX wrapping COUNT(*) — PG's COUNT(*) is bigint and misses the "
        "int/int max() shim; use NULLIF(COUNT(*), 0) instead",
    ),
)


class CostDialectPortabilityTests(unittest.TestCase):
    """The cost SQL must stay valid under the PostgreSQL dialect.

    PostgreSQL has no two-argument ``date(text, modifier)`` (the pg_compat
    shims deliberately don't add one) and its ``COUNT(*)`` returns ``bigint``,
    which does not match the int/int ``max()`` shim — so the 2.3.0 baseline
    query failed on real PostgreSQL (reproduced live; the SQLite-only default
    test run cannot see it, and the PG integration suite only runs when
    HELIX_PG_INTEGRATION=1). The portable rewrite uses Python window bounds
    and ``NULLIF(COUNT(*), 0)``; guard the source so neither pattern can be
    reintroduced.
    """

    def test_no_sqlite_date_modifier_or_count_wrapped_in_minmax(self) -> None:
        # The guard must keep its teeth: both guarded regressions match.
        self.assertIsNotNone(_DATE_MODIFIER.search("AND date >= date(?, ?)"))
        self.assertIsNotNone(_MINMAX_COUNT.search("SUM(cost_usd) / MAX(1, COUNT(*))"))

        app_dir = Path(__file__).resolve().parents[1] / "app"
        for path in sorted(app_dir.rglob("*.py")):
            if path.name == "pg_compat.py":
                continue  # that file *is* the shim layer
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
                    continue  # SQL only — prose in docstrings/comments may name the patterns
                for pattern, message in _GUARDS:
                    match = pattern.search(node.value)
                    if match is None:
                        continue
                    self.fail(
                        f"{path.relative_to(app_dir.parent)} SQL {match.group()!r}: {message}"
                    )


if __name__ == "__main__":
    unittest.main()
