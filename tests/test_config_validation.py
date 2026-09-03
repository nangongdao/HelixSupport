"""Settings validation branches — one failing env per case.

app.config.Settings.from_env() reads everything from the environment and
validates it in __post_init__. These tests drive each validation branch with
a deliberately misconfigured env var so the fail-fast contract is pinned:
a bad value raises ValueError with the exact setting name, never a silent
fallback.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.config import Settings


def _env(**overrides: str) -> dict[str, str]:
    # Reuse the test suite's normal environment and layer one bad value on top.
    env = {
        "DATABASE_PATH": "data/support.db",
        "AUTH_MODE": "demo",
        "TURN_WORKER_ENABLED": "0",
        "RATE_LIMIT_PER_MINUTE": "10000",
    }
    env.update(overrides)
    return env


class SettingsValidationTests(unittest.TestCase):
    def _assert_rejects(self, env_var: str, value: str, message: str) -> None:
        with mock.patch.dict(os.environ, _env(**{env_var: value}), clear=False):
            with self.assertRaisesRegex(ValueError, message):
                Settings.from_env()

    def test_rejects_negative_stream_pacing(self) -> None:
        self._assert_rejects("TURN_JOB_STREAM_PACING_MS", "-1", "TURN_JOB_STREAM_PACING_MS")

    def test_rejects_low_sse_poll_interval(self) -> None:
        self._assert_rejects("TURN_JOB_SSE_POLL_INTERVAL_MS", "5", "TURN_JOB_SSE_POLL_INTERVAL_MS")

    def test_rejects_bad_claim_ttl(self) -> None:
        self._assert_rejects("CLAIM_TTL_SECONDS", "10", "CLAIM_TTL_SECONDS")

    def test_rejects_zero_pool_size(self) -> None:
        self._assert_rejects("DATABASE_POOL_SIZE", "0", "DATABASE_POOL_SIZE")

    def test_rejects_low_busy_timeout(self) -> None:
        self._assert_rejects("DATABASE_BUSY_TIMEOUT_MS", "50", "DATABASE_BUSY_TIMEOUT_MS")

    def test_rejects_zero_cache_ttl(self) -> None:
        self._assert_rejects("KNOWLEDGE_CACHE_TTL_SECONDS", "0", "Cache TTL")

    def test_rejects_zero_cache_max_entries(self) -> None:
        self._assert_rejects("CACHE_MAX_ENTRIES", "0", "CACHE_MAX_ENTRIES")

    def test_rejects_bad_local_draft_ttl(self) -> None:
        self._assert_rejects("LOCAL_DRAFT_TTL_MINUTES", "0", "LOCAL_DRAFT_TTL_MINUTES")

    def test_rejects_bad_queue_backend(self) -> None:
        self._assert_rejects("QUEUE_BACKEND", "kafka", "QUEUE_BACKEND")

    def test_rejects_bad_attachment_scan_mode(self) -> None:
        self._assert_rejects("ATTACHMENT_SCAN_MODE", "async", "ATTACHMENT_SCAN_MODE")

    def test_rejects_redis_backend_without_url(self) -> None:
        with mock.patch.dict(
            os.environ, _env(QUEUE_BACKEND="redis", REDIS_URL=""), clear=False
        ):
            with self.assertRaisesRegex(ValueError, "REDIS_URL is required"):
                Settings.from_env()

    def test_rejects_bad_deployment_profile(self) -> None:
        self._assert_rejects("DEPLOYMENT_PROFILE", "multi-region", "DEPLOYMENT_PROFILE")

    def test_rejects_bad_queue_failure_mode(self) -> None:
        self._assert_rejects("QUEUE_FAILURE_MODE", "silent", "QUEUE_FAILURE_MODE")

    def test_rejects_bad_process_role(self) -> None:
        self._assert_rejects("PROCESS_ROLE", "migrator", "PROCESS_ROLE")

    def test_rejects_multi_with_sqlite(self) -> None:
        with mock.patch.dict(
            os.environ,
            _env(DEPLOYMENT_PROFILE="multi", DATABASE_BACKEND="sqlite"),
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "multi requires DATABASE_BACKEND"):
                Settings.from_env()

    def test_rejects_multi_with_redis_fallback_mode(self) -> None:
        with mock.patch.dict(
            os.environ,
            _env(
                DEPLOYMENT_PROFILE="multi",
                DATABASE_BACKEND="postgresql",
                QUEUE_BACKEND="redis",
                QUEUE_FAILURE_MODE="fallback",
            ),
            clear=False,
        ):
            with self.assertRaisesRegex(ValueError, "multi requires QUEUE_FAILURE_MODE"):
                Settings.from_env()

    def test_rejects_bad_anchor_cadence(self) -> None:
        self._assert_rejects("AUDIT_ANCHOR_CADENCE_HOURS", "0", "AUDIT_ANCHOR_CADENCE_HOURS")

    def test_rejects_bad_anchor_batch(self) -> None:
        self._assert_rejects("AUDIT_ANCHOR_BATCH", "0", "AUDIT_ANCHOR_BATCH")

    def test_rejects_invalid_trusted_kid(self) -> None:
        self._assert_rejects("AUDIT_TRUSTED_KIDS", "not-a-kid", "AUDIT_TRUSTED_KIDS")

    def test_rejects_max_attempts_out_of_range(self) -> None:
        self._assert_rejects("TURN_JOB_MAX_ATTEMPTS", "11", "TURN_JOB_MAX_ATTEMPTS")

    def test_rejects_retention_out_of_range(self) -> None:
        self._assert_rejects("TURN_JOB_RETENTION_DAYS", "0", "TURN_JOB_RETENTION_DAYS")

    def test_rejects_eval_adversarial_floor(self) -> None:
        self._assert_rejects("EVAL_ADVERSARIAL_FLOOR", "0", "EVAL_ADVERSARIAL_FLOOR")

    def test_rejects_eval_golden_floor(self) -> None:
        self._assert_rejects("EVAL_GOLDEN_FLOOR", "0", "EVAL_GOLDEN_FLOOR")

    def test_rejects_eval_p95_budget_negative(self) -> None:
        self._assert_rejects("EVAL_P95_BUDGET_MS", "-1", "EVAL_P95_BUDGET_MS")

    def test_rejects_eval_cost_budget_negative(self) -> None:
        self._assert_rejects("EVAL_COST_BUDGET_USD", "-1", "EVAL_COST_BUDGET_USD")

    def test_rejects_bad_database_backend(self) -> None:
        self._assert_rejects("DATABASE_BACKEND", "mongo", "DATABASE_BACKEND")

    def test_rejects_postgres_without_url(self) -> None:
        with mock.patch.dict(
            os.environ, _env(DATABASE_BACKEND="postgresql", DATABASE_URL=""), clear=False
        ):
            with self.assertRaisesRegex(ValueError, "DATABASE_URL is required"):
                Settings.from_env()

    def test_rejects_rls_without_postgres(self) -> None:
        self._assert_rejects("DATABASE_RLS_ENABLED", "1", "DATABASE_RLS_ENABLED")

    def test_rejects_llm_without_key(self) -> None:
        self._assert_rejects("ENABLE_LLM", "1", "OPENAI_API_KEY")

    def test_rejects_bad_envelope_rewrap_cadence(self) -> None:
        self._assert_rejects("ENVELOPE_REWRAP_CADENCE_HOURS", "0", "ENVELOPE_REWRAP_CADENCE_HOURS")

    def test_rejects_drift_window_too_small(self) -> None:
        self._assert_rejects("DRIFT_WINDOW_DAYS", "0", "DRIFT_WINDOW_DAYS")

    def test_rejects_drift_min_turns(self) -> None:
        self._assert_rejects("DRIFT_MIN_TURNS", "0", "DRIFT_MIN_TURNS")

    def test_rejects_drift_rate_out_of_bounds(self) -> None:
        self._assert_rejects("DRIFT_MAX_ESCALATION_RATE", "1.5", "DRIFT_MAX_ESCALATION_RATE")

    def test_rejects_drift_count_zero(self) -> None:
        self._assert_rejects("DRIFT_MAX_MODEL_DENIALS", "0", "DRIFT_MAX_MODEL_DENIALS")

    def test_rejects_drift_window_above_90(self) -> None:
        self._assert_rejects("DRIFT_WINDOW_DAYS", "91", "DRIFT_WINDOW_DAYS")

    def test_rejects_prompt_canary_ratio(self) -> None:
        self._assert_rejects("PROMPT_CANARY_RATIO", "1.5", "PROMPT_CANARY_RATIO")

    def test_rejects_webhook_interval_out_of_range(self) -> None:
        self._assert_rejects("WEBHOOK_DELIVERY_INTERVAL_SECONDS", "1", "WEBHOOK_DELIVERY_INTERVAL_SECONDS")

    def test_rejects_channel_replay_window_out_of_range(self) -> None:
        self._assert_rejects("CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS", "10", "CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS")

    def test_rejects_archive_after_days(self) -> None:
        self._assert_rejects("CONVERSATION_ARCHIVE_AFTER_DAYS", "3", "CONVERSATION_ARCHIVE_AFTER_DAYS")

    def test_rejects_widget_frame_ancestors_empty(self) -> None:
        self._assert_rejects("WIDGET_FRAME_ANCESTORS", "", "WIDGET_FRAME_ANCESTORS")

    def test_rejects_widget_frame_ancestors_bad_source(self) -> None:
        self._assert_rejects("WIDGET_FRAME_ANCESTORS", "javascript:alert(1)", "WIDGET_FRAME_ANCESTORS")


if __name__ == "__main__":
    unittest.main()