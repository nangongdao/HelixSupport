"""Tests for shadow traffic system (ROADMAP 2.1.x).

Verifies:
- Sampling logic respects configuration
- Request snapshots capture necessary fields
- Response comparison detects field-level diffs
- Comparison results are persisted correctly
- Shadow monitor evaluates health thresholds
"""

from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from fastapi.testclient import TestClient
from app.database import Database
from app.shadow_monitor import (
    ShadowMonitorThresholds,
    ShadowSignals,
    collect_shadow_signals,
    evaluate_shadow_health,
)
import httpx

from app.shadow_traffic import (
    ShadowComparison,
    ShadowRequest,
    is_v2_shadow_eligible,
    response_json_body,
    shadow_request_to_v2,
    _compare_responses,
    _deep_equal,
    _record_comparison,
    should_shadow_request,
)


class ShadowTrafficTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "shadow.db"
        self.settings = Settings(
            database_path=db_path,
            shadow_traffic_enabled=True,
            shadow_traffic_sample_rate=0.05,
        )
        self.db = Database(
            path=db_path,
            pool_size=1,
            busy_timeout_ms=5000,
        )
        self.db.initialize()

        # Create shadow_traffic_comparisons table for tests
        with self.db.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS shadow_traffic_comparisons (
                    id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    request_id TEXT NOT NULL,
                    route TEXT NOT NULL,
                    v1_status_code INTEGER,
                    v2_status_code INTEGER,
                    fields_matched TEXT NOT NULL DEFAULT '[]',
                    fields_mismatched TEXT NOT NULL DEFAULT '[]',
                    v1_latency_ms INTEGER,
                    v2_latency_ms INTEGER,
                    sampling_rate REAL NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_shadow_comparisons_tenant_route "
                "ON shadow_traffic_comparisons(tenant_id, route, created_at)"
            )
            # The comparison rows are tenant-scoped; seed the referenced tenant
            # so the foreign key holds.
            conn.execute(
                "INSERT OR IGNORE INTO tenants (id, name, created_at) "
                "VALUES ('tenant-test', 'Test Tenant', '2026-01-01T00:00:00Z')"
            )

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_sampling_disabled_when_shadow_traffic_disabled(self) -> None:
        settings_disabled = Settings(
            shadow_traffic_enabled=False,
            shadow_traffic_sample_rate=1.0,
        )
        self.assertFalse(should_shadow_request(settings_disabled))

    def test_sampling_always_shadows_at_100_percent(self) -> None:
        settings_full = Settings(
            shadow_traffic_enabled=True,
            shadow_traffic_sample_rate=1.0,
        )
        self.assertTrue(should_shadow_request(settings_full))

    def test_sampling_never_shadows_at_zero_percent(self) -> None:
        settings_zero = Settings(
            shadow_traffic_enabled=True,
            shadow_traffic_sample_rate=0.0,
        )
        self.assertFalse(should_shadow_request(settings_zero))

    def test_sampling_respects_probabilistic_rate(self) -> None:
        settings_50pct = Settings(
            shadow_traffic_enabled=True,
            shadow_traffic_sample_rate=0.5,
        )
        # Run 100 trials and expect roughly 50% to sample
        with patch("random.random", side_effect=[i / 100 for i in range(100)]):
            samples = [should_shadow_request(settings_50pct) for _ in range(100)]
            sampled_count = sum(samples)
            self.assertTrue(40 <= sampled_count <= 60)  # tolerance for probabilistic test

    def test_deep_equal_primitives(self) -> None:
        self.assertTrue(_deep_equal(1, 1))
        self.assertTrue(_deep_equal("foo", "foo"))
        self.assertFalse(_deep_equal(1, 2))
        self.assertFalse(_deep_equal("foo", "bar"))
        self.assertFalse(_deep_equal(1, "1"))

    def test_deep_equal_lists(self) -> None:
        self.assertTrue(_deep_equal([1, 2, 3], [1, 2, 3]))
        self.assertFalse(_deep_equal([1, 2], [1, 2, 3]))
        self.assertFalse(_deep_equal([1, 2], [2, 1]))

    def test_deep_equal_dicts(self) -> None:
        self.assertTrue(_deep_equal({"a": 1, "b": 2}, {"a": 1, "b": 2}))
        self.assertTrue(_deep_equal({"b": 2, "a": 1}, {"a": 1, "b": 2}))  # order-independent
        self.assertFalse(_deep_equal({"a": 1}, {"a": 2}))
        self.assertFalse(_deep_equal({"a": 1}, {"a": 1, "b": 2}))

    def test_deep_equal_nested(self) -> None:
        v1 = {"user": {"id": "123", "roles": ["admin"]}}
        v2 = {"user": {"id": "123", "roles": ["admin"]}}
        v3 = {"user": {"id": "123", "roles": ["user"]}}
        self.assertTrue(_deep_equal(v1, v2))
        self.assertFalse(_deep_equal(v1, v3))

    def test_compare_responses_identical(self) -> None:
        v1 = {"id": "123", "status": "active", "count": 42}
        v2 = {"id": "123", "status": "active", "count": 42}
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(set(matched), {"id", "status", "count"})
        self.assertEqual(mismatched, [])

    def test_compare_responses_partial_mismatch(self) -> None:
        v1 = {"id": "123", "status": "active", "count": 42}
        v2 = {"id": "123", "status": "inactive", "count": 42}
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(set(matched), {"id", "count"})
        self.assertEqual(mismatched, ["status"])

    def test_compare_responses_v1_only_keys_ignored(self) -> None:
        # v2 is a slim projection: fields only v1 declares are by-design
        # omissions (measured live: 31-key v1 item vs the 8-key v2 contract),
        # not drift. Only fields v2 DECLARE are compared.
        v1 = {"id": "123", "status": "active", "labels": ["x"]}
        v2 = {"id": "123"}
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(matched, ["id"])
        self.assertEqual(mismatched, [])

    def test_compare_responses_extra_fields(self) -> None:
        v1 = {"id": "123"}
        v2 = {"id": "123", "extra": "field"}
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(matched, ["id"])
        self.assertEqual(mismatched, ["extra"])

    def test_compare_responses_lists_element_wise(self) -> None:
        v1 = [{"id": "1", "status": "open"}, {"id": "2", "status": "resolved"}]
        v2 = [{"id": "1", "status": "open"}, {"id": "2", "status": "resolved"}]
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(matched, ["items"])
        self.assertEqual(mismatched, [])

    def test_compare_responses_list_value_diff_is_per_element(self) -> None:
        v1 = [{"id": "1", "status": "open"}, {"id": "2", "status": "resolved"}]
        v2 = [{"id": "1", "status": "open"}, {"id": "2", "status": "open"}]
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(matched, [])
        self.assertEqual(mismatched, ["items[1].status"])

    def test_compare_responses_list_length_difference(self) -> None:
        v1 = [{"id": "1"}, {"id": "2"}]
        v2 = [{"id": "1"}]
        matched, mismatched = _compare_responses(v1, v2)
        self.assertEqual(mismatched, ["items.length 2 != 1"])

    def test_compare_responses_list_shape_mismatch(self) -> None:
        matched, mismatched = _compare_responses([{"id": "1"}], {"id": "1"})
        self.assertEqual(mismatched, ["payload_type"])

    def test_compare_responses_handles_none(self) -> None:
        matched, mismatched = _compare_responses(None, {"id": "123"})
        self.assertEqual(matched, [])
        self.assertEqual(mismatched, [])

        matched, mismatched = _compare_responses({"id": "123"}, None)
        self.assertEqual(matched, [])
        self.assertEqual(mismatched, [])

    def test_record_comparison_persists_to_database(self) -> None:
        comparison = ShadowComparison(
            id="shadow-req-001",
            tenant_id="tenant-test",
            request_id="req-001",
            route="/api/conversations",
            v1_status_code=200,
            v2_status_code=200,
            fields_matched=["id", "status"],
            fields_mismatched=["updated_at"],
            v1_latency_ms=120,
            v2_latency_ms=135,
            sampling_rate=0.05,
        )

        _record_comparison(self.db, comparison)

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shadow_traffic_comparisons WHERE id = ?",
                ("shadow-req-001",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["tenant_id"], "tenant-test")
        self.assertEqual(row["request_id"], "req-001")
        self.assertEqual(row["route"], "/api/conversations")
        self.assertEqual(row["v1_status_code"], 200)
        self.assertEqual(row["v2_status_code"], 200)
        self.assertEqual(json.loads(row["fields_matched"]), ["id", "status"])
        self.assertEqual(json.loads(row["fields_mismatched"]), ["updated_at"])
        self.assertEqual(row["v1_latency_ms"], 120)
        self.assertEqual(row["v2_latency_ms"], 135)
        self.assertEqual(row["sampling_rate"], 0.05)

    def test_evaluate_shadow_health_insufficient_sample(self) -> None:
        signals = ShadowSignals(
            total_comparisons=50,
            mismatch_count=5,
            mismatch_rate=0.10,
            v1_p95_latency_ms=100.0,
            v2_p95_latency_ms=110.0,
            latency_regression_ms=10.0,
        )
        thresholds = ShadowMonitorThresholds(min_comparisons=100)

        is_healthy, reason = evaluate_shadow_health(signals, thresholds)
        self.assertTrue(is_healthy)
        self.assertEqual(reason, "insufficient_sample")

    def test_evaluate_shadow_health_mismatch_rate_breach(self) -> None:
        signals = ShadowSignals(
            total_comparisons=200,
            mismatch_count=15,
            mismatch_rate=0.075,  # 7.5% exceeds 5% threshold
            v1_p95_latency_ms=100.0,
            v2_p95_latency_ms=110.0,
            latency_regression_ms=10.0,
        )
        thresholds = ShadowMonitorThresholds(max_mismatch_rate=0.05)

        is_healthy, reason = evaluate_shadow_health(signals, thresholds)
        self.assertFalse(is_healthy)
        self.assertIn("mismatch_rate", reason)
        self.assertIn("7.50%", reason)

    def test_evaluate_shadow_health_latency_regression_breach(self) -> None:
        signals = ShadowSignals(
            total_comparisons=200,
            mismatch_count=5,
            mismatch_rate=0.025,
            v1_p95_latency_ms=100.0,
            v2_p95_latency_ms=350.0,
            latency_regression_ms=250.0,  # exceeds 200ms threshold
        )
        thresholds = ShadowMonitorThresholds(max_latency_regression_ms=200.0)

        is_healthy, reason = evaluate_shadow_health(signals, thresholds)
        self.assertFalse(is_healthy)
        self.assertIn("latency_regression", reason)
        self.assertIn("250ms", reason)

    def test_evaluate_shadow_health_all_green(self) -> None:
        signals = ShadowSignals(
            total_comparisons=200,
            mismatch_count=8,
            mismatch_rate=0.04,  # 4% under 5%
            v1_p95_latency_ms=100.0,
            v2_p95_latency_ms=180.0,
            latency_regression_ms=80.0,  # under 200ms
        )
        thresholds = ShadowMonitorThresholds()

        is_healthy, reason = evaluate_shadow_health(signals, thresholds)
        self.assertTrue(is_healthy)
        self.assertEqual(reason, "healthy")

    def test_collect_shadow_signals_empty_window(self) -> None:
        signals = collect_shadow_signals(
            self.db,
            tenant_id=None,
            route=None,
            window_hours=24,
        )
        self.assertEqual(signals.total_comparisons, 0)
        self.assertEqual(signals.mismatch_count, 0)
        self.assertEqual(signals.mismatch_rate, 0.0)
        self.assertIsNone(signals.v1_p95_latency_ms)
        self.assertIsNone(signals.v2_p95_latency_ms)

    def test_collect_shadow_signals_aggregates_correctly(self) -> None:
        # Insert 10 comparisons: 8 match, 2 mismatch
        for i in range(10):
            mismatched = ["field"] if i < 2 else []
            comparison = ShadowComparison(
                id=f"shadow-{i}",
                tenant_id="tenant-test",
                request_id=f"req-{i}",
                route="/api/conversations",
                v1_status_code=200,
                v2_status_code=200,
                fields_matched=["id"],
                fields_mismatched=mismatched,
                v1_latency_ms=100 + i * 10,
                v2_latency_ms=110 + i * 10,
                sampling_rate=0.05,
            )
            _record_comparison(self.db, comparison)

        signals = collect_shadow_signals(self.db, tenant_id=None, route=None, window_hours=24)

        self.assertEqual(signals.total_comparisons, 10)
        self.assertEqual(signals.mismatch_count, 2)
        self.assertAlmostEqual(signals.mismatch_rate, 0.20, places=2)  # 20%
        self.assertIsNotNone(signals.v1_p95_latency_ms)
        self.assertIsNotNone(signals.v2_p95_latency_ms)
        # v2 latencies are consistently 10ms higher
        self.assertIsNotNone(signals.latency_regression_ms)
        self.assertTrue(8 <= signals.latency_regression_ms <= 12)  # roughly 10ms


class ResponseJsonBodyTests(unittest.TestCase):
    """The v1 body extractor used by the request-controls middleware."""

    def test_parses_json_dict(self) -> None:
        headers = {"content-type": "application/json"}
        self.assertEqual(
            response_json_body(headers, b'{"items": [1, 2, 3], "total": 3}'),
            {"items": [1, 2, 3], "total": 3},
        )

    def test_rejects_non_json_content_type(self) -> None:
        self.assertIsNone(response_json_body({"content-type": "text/event-stream"}, b"data: x"))
        self.assertIsNone(response_json_body({}, b"{}"))

    def test_rejects_oversized_bodies(self) -> None:
        headers = {"content-type": "application/json"}
        self.assertIsNone(response_json_body(headers, b"x" * 262145, max_bytes=262144))

    def test_rejects_non_dict_json(self) -> None:
        # A JSON list is not comparable field-by-field; the v1 side records
        # no field comparison rather than guessing.
        self.assertIsNone(response_json_body({"content-type": "application/json"}, b"[1, 2]"))
        self.assertIsNone(response_json_body({"content-type": "application/json"}, b"null"))

    def test_rejects_undecodable_bytes(self) -> None:
        self.assertIsNone(response_json_body({"content-type": "application/json"}, b"\xff\xfe"))


class ShadowMiddlewareIntegrationTests(unittest.TestCase):
    """The request-controls middleware captures the REAL v1 response.

    The 2.1.x wiring hardcoded ``v1_response_body={"status": "ok"}`` and
    recorded ``v1_status_code=200`` unconditionally — every field comparison
    compared v2's real payload against a fake, so the health monitor would
    read a permanent mismatch rate. These tests pin the fixed wiring through
    the actual app middleware.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.settings = Settings(
            database_path=Path(self._tmp.name) / "shadow-mw.db",
            auth_mode="demo",
            shadow_traffic_enabled=True,
            shadow_traffic_base_url="http://127.0.0.1:9",  # v2 replay target unreachable
            rate_limit_per_minute=10000,
            docs_enabled=False,
        )
        from app.main import create_app

        self.client = TestClient(create_app(self.settings))

    def tearDown(self) -> None:
        self.client.app.state.services.database.close()
        self._tmp.cleanup()

    def test_middleware_captures_real_body_and_status(self) -> None:
        captured: dict = {}
        with patch(
            "app.shadow_traffic.maybe_shadow_request",
            side_effect=lambda **kw: captured.update(kw),
        ):
            response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        # The captured body is the REAL response payload, not {"status": "ok"}.
        self.assertEqual(captured["v1_response_body"]["status"], "ready")
        self.assertEqual(captured["v1_status_code"], 200)

    def test_middleware_captures_404_status(self) -> None:
        captured: dict = {}
        with patch(
            "app.shadow_traffic.maybe_shadow_request",
            side_effect=lambda **kw: captured.update(kw),
        ):
            response = self.client.get("/api/nonexistent-endpoint")
        self.assertEqual(response.status_code, 404)
        # The old wiring recorded 200 unconditionally; the real status now
        # reaches the comparison.
        self.assertEqual(captured["v1_status_code"], 404)
        self.assertIsNotNone(captured["v1_response_body"])

    def test_shadow_replay_marker_is_never_shadowed(self) -> None:
        captured: dict = {}
        with patch(
            "app.shadow_traffic.maybe_shadow_request",
            side_effect=lambda **kw: captured.update(kw),
        ):
            response = self.client.get("/health/ready", headers={"X-Shadow-Request": "true"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured, {})


class ShadowEligibilityTests(unittest.TestCase):
    """Only v1 GETs with a proven-comparable v2 counterpart are shadowed."""

    def test_conversation_list_and_messages_are_eligible(self) -> None:
        self.assertTrue(is_v2_shadow_eligible("/api/conversations"))
        self.assertTrue(is_v2_shadow_eligible("/api/conversations/conv_1/messages"))

    def test_surfaces_without_v2_counterparts_are_not_eligible(self) -> None:
        self.assertFalse(is_v2_shadow_eligible("/api/turn-jobs"))
        self.assertFalse(is_v2_shadow_eligible("/api/labels"))
        self.assertFalse(is_v2_shadow_eligible("/health/ready"))
        # The detail endpoint nests what v2 returns flat — zero shared
        # top-level keys (measured live), so a field comparison is noise.
        self.assertFalse(is_v2_shadow_eligible("/api/conversations/conv_1"))


class FullChainComparisonTests(unittest.TestCase):
    """v1 real list body vs the real v2 envelope -> a recorded comparison
    whose fields MATCH (the eight-key contract, zero diffs measured live)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db_path = Path(self._tmp.name) / "chain.db"
        self.settings = Settings(
            database_path=db_path,
            auth_mode="demo",
            shadow_traffic_enabled=True,
            shadow_traffic_base_url="http://v2.test",
            shadow_traffic_sample_rate=1.0,
            rate_limit_per_minute=10000,
            docs_enabled=False,
            turn_worker_enabled=False,
        )
        self.db = Database(db_path)
        self.db.initialize()
        self.db.ensure_tenant("demo")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_v1_list_against_enveloped_v2_payload_records_matches(self) -> None:
        import httpx

        v1_body = [
            {
                "id": "conv_1",
                "status": "open",
                "channel": "web",
                "priority": "normal",
                "customer_name": "C",
                "customer_ref": None,
                "created_at": "t1",
                "updated_at": "t1",
            },
            {
                "id": "conv_2",
                "status": "resolved",
                "channel": "web",
                "priority": "high",
                "customer_name": "C2",
                "customer_ref": None,
                "created_at": "t2",
                "updated_at": "t2",
            },
        ]
        v2_envelope = {
            "data": [
                {
                    "id": "conv_1",
                    "status": "open",
                    "channel": "web",
                    "priority": "normal",
                    "customer_name": "C",
                    "customer_ref": None,
                    "created_at": "t1",
                    "updated_at": "t1",
                },
                {
                    "id": "conv_2",
                    "status": "resolved",
                    "channel": "web",
                    "priority": "high",
                    "customer_name": "C2",
                    "customer_ref": None,
                    "created_at": "t2",
                    "updated_at": "t2",
                },
            ],
            "next_cursor": "cursor-abc",
        }
        snapshot = ShadowRequest(
            method="GET",
            path="/api/conversations",
            headers={},
            body=None,
            tenant_id="demo",
            request_id="req-chain-1",
        )

        def v2_handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v2/conversations"
            return httpx.Response(200, json=v2_envelope)

        real_client = httpx.AsyncClient(transport=httpx.MockTransport(v2_handler))
        with patch("app.shadow_traffic.httpx.AsyncClient", return_value=real_client):
            asyncio.run(
                shadow_request_to_v2(
                    snapshot=snapshot,
                    v1_response_body=v1_body,
                    v1_status_code=200,
                    v1_latency_ms=10,
                    settings=self.settings,
                    db=self.db,
                )
            )

        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT * FROM shadow_traffic_comparisons WHERE id = ?",
                ("shadow-req-chain-1",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row["fields_matched"]), ["items"])
        self.assertEqual(json.loads(row["fields_mismatched"]), [])
        self.assertEqual(row["v1_status_code"], 200)


if __name__ == "__main__":
    unittest.main()
