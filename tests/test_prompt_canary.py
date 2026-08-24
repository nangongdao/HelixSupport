"""Phase 19.2: canary routing, prompt-version resolution, and telemetry.

These tests pin the contract the release gate depends on:
- ``canary_bucket`` is deterministic and bounded in [0, 1).
- ``resolve_prompt`` picks tenant-scoped before global, canary before active
  only when the stable bucket falls under the ratio, and falls back to
  ``"default"`` when nothing is registered (backward compatibility).
- The orchestrator records the resolved prompt version on every turn's
  assistant metadata, emits a ``prompt_version.resolved`` audit event, and
  increments the ``turn.processed`` telemetry counter tagged by channel.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.database import Database
from app.migrations import all_migrations, run_migrations
from app.orchestrator import ConversationOrchestrator
from app.prompts import PromptRegistry


class CanaryBucketTests(unittest.TestCase):
    def test_stable_across_calls(self) -> None:
        """Same conversation id must always hash to the same bucket."""
        a = PromptRegistry.canary_bucket("conv-12345")
        b = PromptRegistry.canary_bucket("conv-12345")
        self.assertEqual(a, b)

    def test_bounded_in_unit_interval(self) -> None:
        for cid in ("a", "", "0" * 100, "conv-α-β", "x" * 4096):
            value = PromptRegistry.canary_bucket(cid)
            self.assertGreaterEqual(value, 0.0)
            self.assertLess(value, 1.0)

    def test_different_ids_likely_differ(self) -> None:
        """A large sample of distinct ids must spread across the range."""
        buckets = {PromptRegistry.canary_bucket(f"conv-{i}") for i in range(1000)}
        self.assertGreater(len(buckets), 500)


class ResolvePromptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        with self.database.connect() as conn:
            run_migrations(conn, all_migrations())
        self.database.ensure_tenant("tenant-1", "Test")
        self.registry = PromptRegistry(self.database)

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def _make(self, tenant: str | None, version: str, status: str) -> str:
        """Create and promote a version to the requested status in one step."""
        v = self.registry.create_version(
            tenant, "triage_prompt", version, f"body-{version}", "model-x", "admin"
        )
        if status == "active":
            self.registry.activate(tenant, v.id, "admin")
        elif status == "canary":
            self.registry.set_canary(tenant, v.id, "admin")
        return v.id

    def test_no_registry_returns_default(self) -> None:
        version, channel = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 0.5)
        self.assertIsNone(version)
        self.assertEqual(channel, "default")

    def test_active_only_uses_active(self) -> None:
        vid = self._make("tenant-1", "v1", "active")
        version, channel = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 0.5)
        assert version is not None
        self.assertEqual(version.id, vid)
        self.assertEqual(channel, "active")

    def test_canary_ratio_zero_uses_active(self) -> None:
        self._make("tenant-1", "v1", "active")
        self._make("tenant-1", "v2", "canary")
        version, channel = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 0.0)
        assert version is not None
        self.assertEqual(channel, "active")
        self.assertEqual(version.version, "v1")

    def test_canary_ratio_one_uses_canary(self) -> None:
        self._make("tenant-1", "v1", "active")
        self._make("tenant-1", "v2", "canary")
        version, channel = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 1.0)
        assert version is not None
        self.assertEqual(channel, "canary")
        self.assertEqual(version.version, "v2")

    def test_canary_bucketing_is_stable_per_conversation(self) -> None:
        """The same conversation always lands on the same channel for a ratio."""
        self._make("tenant-1", "v1", "active")
        self._make("tenant-1", "v2", "canary")
        first = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-stable", 0.5)
        second = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-stable", 0.5)
        self.assertEqual(first[1], second[1])
        if first[0] is not None and second[0] is not None:
            self.assertEqual(first[0].id, second[0].id)

    def test_canary_split_near_half_at_ratio_half(self) -> None:
        """A 0.5 ratio must route roughly half of a sample to canary."""
        self._make("tenant-1", "v1", "active")
        self._make("tenant-1", "v2", "canary")
        canary = sum(
            1
            for i in range(400)
            if self.registry.resolve_prompt("tenant-1", "triage_prompt", f"conv-{i}", 0.5)[1]
            == "canary"
        )
        # 200 expected; allow a wide band because this is a hash distribution.
        self.assertGreater(canary, 120)
        self.assertLess(canary, 280)

    def test_tenant_scoped_overrides_global(self) -> None:
        """A tenant-scoped active version wins over a global one."""
        global_id = self._make(None, "g1", "active")
        tenant_id = self._make("tenant-1", "t1", "active")
        version, _ = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 0.0)
        assert version is not None
        self.assertEqual(version.id, tenant_id)
        self.assertNotEqual(version.id, global_id)

    def test_global_fallback_when_no_tenant_version(self) -> None:
        global_id = self._make(None, "g1", "active")
        version, channel = self.registry.resolve_prompt("tenant-1", "triage_prompt", "conv-1", 0.0)
        assert version is not None
        self.assertEqual(version.id, global_id)
        self.assertEqual(channel, "active")


class OrchestratorPromptVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        self.database.initialize()
        self.database.ensure_tenant("tenant-1", "Test")
        self.settings = Settings(database_path=self.db_path, auth_mode="demo")
        self.orchestrator = ConversationOrchestrator(self.database, self.settings)
        self.registry = PromptRegistry(self.database)
        conv = self.database.create_conversation("tenant-1", "Customer", None, "web", "admin", 120)
        self.conv_id = conv["id"]
        # Reset the global telemetry counter diff baseline.
        from app.telemetry import metrics as telemetry_metrics

        self._telemetry = telemetry_metrics
        self._baseline = telemetry_metrics.snapshot().get("counters", {})

    def tearDown(self) -> None:
        self.database.close()
        self.temp_dir.cleanup()

    def _send(self, content: str, key: str) -> dict[str, object]:
        return self.orchestrator.handle_customer_message(
            "tenant-1", self.conv_id, content, "admin", key
        )

    def _audit_events(self) -> list[dict[str, object]]:
        with self.database.connect() as conn:
            rows = conn.execute(
                "SELECT event_type, payload_json FROM audit_events "
                "WHERE tenant_id = ? ORDER BY created_at, seq",
                ("tenant-1",),
            ).fetchall()
        import json

        return [
            {"event_type": r["event_type"], "payload": json.loads(r["payload_json"])} for r in rows
        ]

    def test_default_channel_when_no_prompt_registered(self) -> None:
        response = self._send("配送一般多久能到？", "idem-default")
        assistant = response["assistant_message"]
        assert assistant is not None
        metadata = assistant["metadata"]  # type: ignore[index]
        self.assertEqual(metadata["prompt_channel"], "default")
        self.assertIsNone(metadata["prompt_version_id"])
        # No resolved event when nothing was registered.
        types = [e["event_type"] for e in self._audit_events()]
        self.assertNotIn("prompt_version.resolved", types)

    def test_active_channel_recorded_when_registered(self) -> None:
        v = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1", "body-v1", "model-x", "admin"
        )
        self.registry.activate("tenant-1", v.id, "admin")
        response = self._send("配送一般多久能到？", "idem-active")
        metadata = response["assistant_message"]["metadata"]  # type: ignore[index]
        self.assertEqual(metadata["prompt_channel"], "active")
        self.assertEqual(metadata["prompt_version_id"], v.id)
        self.assertEqual(metadata["prompt_version"], "v1")
        events = self._audit_events()
        resolved = [e for e in events if e["event_type"] == "prompt_version.resolved"]
        self.assertEqual(len(resolved), 1)
        payload = resolved[0]["payload"]
        assert isinstance(payload, dict)
        self.assertEqual(payload["channel"], "active")

    def test_canary_channel_when_ratio_full(self) -> None:
        self.settings = Settings(
            database_path=self.db_path, auth_mode="demo", prompt_canary_ratio=1.0
        )
        self.orchestrator = ConversationOrchestrator(self.database, self.settings)
        active = self.registry.create_version(
            "tenant-1", "triage_prompt", "v1", "body-v1", "model-x", "admin"
        )
        self.registry.activate("tenant-1", active.id, "admin")
        canary = self.registry.create_version(
            "tenant-1", "triage_prompt", "v2", "body-v2", "model-y", "admin"
        )
        self.registry.set_canary("tenant-1", canary.id, "admin")
        response = self._send("配送一般多久能到？", "idem-canary")
        metadata = response["assistant_message"]["metadata"]  # type: ignore[index]
        self.assertEqual(metadata["prompt_channel"], "canary")
        self.assertEqual(metadata["prompt_version_id"], canary.id)

    def test_telemetry_counter_incremented_by_channel(self) -> None:
        self._send("配送一般多久能到？", "idem-telemetry")
        after = self._telemetry.snapshot().get("counters", {})
        prompt_keys = [k for k in after if k.startswith("turn.processed")]
        self.assertTrue(
            any("prompt_channel=default" in k for k in prompt_keys),
            f"expected a default-channel counter, got {prompt_keys}",
        )


if __name__ == "__main__":
    unittest.main()
