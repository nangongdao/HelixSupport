"""ROADMAP 43.5 AI Governance v2 — automated-surface contracts.

Covers the four §43.5 items that are verifiable without real vendors or a
control-plane deployment:

1. **Provider metadata & tenant disable surface** (``app/model_provider.py``
   + ``app/turn_policy.py``): providers declare data-retention /
   training-opt-out / region; the control-plane ``model_policy`` can disable
   providers, models, and data egress per tenant; refusals audit
   ``turn.model_denied`` with the reason; no control plane keeps the
   fail-open pre-43.5 behaviour.
2. **Capability tokens & side-effect classification** (``app/tools.py`` +
   ``app/tool_governance.py``): token signature/expiry/subject/schema-digest
   verification fails closed; argument schema violations refuse before any
   connector runs; high-risk tools require an approved human record.
3. **Eval registry & online feedback review** (``app/ai_governance.py``):
   datasets are versioned with content hashes; unreviewed feedback never
   reaches a dataset; maker-checker forbids self-approval.
4. **Drift monitoring** (``app/drift_monitor.py``): threshold breaches over
   quality buckets and refusal audits stop canaries and audit
   ``ai.drift_canary_stopped``; below-min-sample windows stay silent;
   disabled monitors do nothing.
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from app.ai_governance import (
    AiGovernanceService,
    ApprovalRequiredError,
    GovernanceError,
    SelfApprovalError,
)
from app.config import Settings
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.database import Database
from app.drift_monitor import DriftMonitor
from app.model_provider import (
    PROVIDER_METADATA,
    ProviderMetadata,
    check_model_policy,
    provider_for_model_ref,
)
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.tool_governance import (
    CapabilityError,
    ToolPolicy,
    issue_capability_token,
    validate_arguments,
    verify_capability_token,
)

SECRET = "control-plane-signing-secret-0123456789abcdef"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {"plan": "standard", "region": "local"}
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class ProviderMetadataTests(unittest.TestCase):
    def test_registry_declares_governance_fields(self) -> None:
        meta = PROVIDER_METADATA["openai"]
        self.assertIsInstance(meta, ProviderMetadata)
        self.assertTrue(meta.data_retention)
        self.assertIsInstance(meta.training_opt_out, bool)
        self.assertTrue(meta.region)

    def test_prefixed_ref_maps_to_its_provider(self) -> None:
        self.assertEqual(provider_for_model_ref("anthropic/claude"), "anthropic")
        self.assertEqual(provider_for_model_ref("openai:gpt-4.1-mini"), "openai")

    def test_bare_ref_attributed_to_default(self) -> None:
        self.assertEqual(provider_for_model_ref("gpt-4.1-mini"), "openai")

    def test_empty_ref_has_no_provider(self) -> None:
        self.assertIsNone(provider_for_model_ref(None))
        self.assertIsNone(provider_for_model_ref(""))


class CheckModelPolicyTests(unittest.TestCase):
    def test_empty_policy_allows_everything(self) -> None:
        decision = check_model_policy(None, "anthropic/claude", tenant_region="eu")
        self.assertTrue(decision.allowed)

    def test_disabled_provider_blocks_prefixed_and_bare_refs(self) -> None:
        for model_ref in ("anthropic/claude", "gpt-4.1-mini"):
            decision = check_model_policy(
                {"disabled_providers": ["anthropic" if "/" in model_ref else "openai"]}, model_ref
            )
            self.assertFalse(decision.allowed, model_ref)
            self.assertIn("provider", decision.reason)

    def test_disabled_models_exact_match(self) -> None:
        decision = check_model_policy({"disabled_models": ["gpt-4.1-mini"]}, "gpt-4.1-mini")
        self.assertFalse(decision.allowed)
        self.assertIn("model", decision.reason)

    def test_data_egress_blocked_across_regions(self) -> None:
        policy = {"allow_data_egress": False}
        blocked = check_model_policy(policy, "openai/gpt-4.1-mini", tenant_region="eu")
        self.assertFalse(blocked.allowed)
        allowed = check_model_policy(policy, "openai/gpt-4.1-mini", tenant_region="us")
        self.assertTrue(allowed.allowed)


class TurnGovernanceGateTests(unittest.TestCase):
    """The full turn pipeline honours the 43.5 disable surface."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gov.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.cp = TenantControlPlane(self.db, SECRET)
        self.dp = DataPlaneConfig(self.db, SECRET)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _register_active_prompt(self, model_ref: str) -> None:
        registry = PromptRegistry(self.db)
        version = registry.create_version(None, "triage_prompt", "1.0", "body", model_ref, "admin")
        registry.activate(None, version.id, "admin")

    def _denied_events(self) -> list[dict]:
        from json import loads

        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT payload_json FROM audit_events WHERE tenant_id='acme' "
                "AND event_type='turn.model_denied'"
            ).fetchall()
        return [loads(row["payload_json"]) for row in rows]

    def test_disabled_provider_denies_turn_with_reason(self) -> None:
        from app.orchestrator import ConversationOrchestrator

        self.cp.set_policy("acme", _policy(model_policy={"disabled_providers": ["openai"]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot("acme"))
        self._register_active_prompt("openai/gpt-4.1-mini")
        orchestrator = ConversationOrchestrator(
            self.db, Settings(database_path=self.db_path, auth_mode="demo")
        )
        orchestrator.data_plane_config = self.dp
        conv = self.db.create_conversation("acme", "C", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            "acme", conv["id"], "配送一般多久能到？", "admin", "idem-gov-1"
        )
        events = self._denied_events()
        self.assertEqual(len(events), 1)
        self.assertIn("provider", events[0].get("reason") or "")

    def test_no_control_plane_keeps_fail_open(self) -> None:
        from app.orchestrator import ConversationOrchestrator

        self._register_active_prompt("openai/gpt-4.1-mini")
        orchestrator = ConversationOrchestrator(
            self.db, Settings(database_path=self.db_path, auth_mode="demo")
        )
        conv = self.db.create_conversation("acme", "C2", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            "acme", conv["id"], "配送一般多久能到？", "admin", "idem-gov-2"
        )
        self.assertEqual(self._denied_events(), [])

    def test_expired_plane_fails_open_like_absent(self) -> None:
        """An unreachable plane must not brick turns — 19.4 surface still runs."""
        from app.orchestrator import ConversationOrchestrator

        self.cp.set_policy("acme", _policy(model_policy={"disabled_providers": ["openai"]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot("acme"))
        self.dp.set_control_plane_available(False)
        # Expire every stored snapshot by re-issuing with negative TTL is not
        # enough once accepted; simulate expiry via the clock hook instead.
        from datetime import datetime as dt

        future_epoch = dt.now(UTC).timestamp() + 10_000
        dp = DataPlaneConfig(self.db, SECRET, clock=lambda: future_epoch + 100)
        dp._accepted.update({})  # fresh plane: nothing accepted
        dp.set_control_plane_available(False)
        self._register_active_prompt("openai/gpt-4.1-mini")
        orchestrator = ConversationOrchestrator(
            self.db, Settings(database_path=self.db_path, auth_mode="demo")
        )
        orchestrator.data_plane_config = dp
        conv = self.db.create_conversation("acme", "C3", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            "acme", conv["id"], "配送一般多久能到？", "admin", "idem-gov-3"
        )
        # No usable snapshot → gate skipped → only the allow-list applies.
        self.assertEqual(len(self._denied_events()), 0)


class CapabilityTokenTests(unittest.TestCase):
    SECRET_BYTES = b"capability-token-secret-0123456789abcdef"

    def _grant(self, tool: str = "orders.lookup", digest: str = "", ttl: int = 300) -> dict:
        return issue_capability_token(
            secret=self.SECRET_BYTES,
            tool=tool,
            tenant_id="acme",
            ttl_seconds=ttl,
            schema_digest=digest,
            now=1_000.0,
        )

    def test_valid_grant_verifies(self) -> None:
        body = verify_capability_token(
            self.SECRET_BYTES, self._grant(), tool="orders.lookup", tenant_id="acme", now=1_100.0
        )
        self.assertEqual(body["tool"], "orders.lookup")

    def test_tampered_signature_rejected(self) -> None:
        grant = self._grant()
        grant["signature"] = "0" * len(grant["signature"])
        with self.assertRaises(CapabilityError):
            verify_capability_token(
                self.SECRET_BYTES, grant, tool="orders.lookup", tenant_id="acme", now=1_100.0
            )

    def test_expired_grant_rejected(self) -> None:
        with self.assertRaises(CapabilityError):
            verify_capability_token(
                self.SECRET_BYTES,
                self._grant(ttl=10),
                tool="orders.lookup",
                tenant_id="acme",
                now=2_000.0,
            )

    def test_foreign_tool_or_tenant_rejected(self) -> None:
        grant = self._grant()
        with self.assertRaises(CapabilityError):
            verify_capability_token(
                self.SECRET_BYTES, grant, tool="customers.resolve", tenant_id="acme", now=1_100.0
            )
        with self.assertRaises(CapabilityError):
            verify_capability_token(
                self.SECRET_BYTES, grant, tool="orders.lookup", tenant_id="other", now=1_100.0
            )

    def test_schema_digest_pinning_invalidates_outstanding_tokens(self) -> None:
        old = self._grant(digest="aaaaaaaaaaaaaaaa")
        with self.assertRaises(CapabilityError):
            verify_capability_token(
                self.SECRET_BYTES,
                old,
                tool="orders.lookup",
                tenant_id="acme",
                schema_digest="bbbbbbbbbbbbbbbb",
                now=1_100.0,
            )

    def test_validate_arguments_shape_checks(self) -> None:
        schema = {
            "type": "object",
            "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}, "count": {"type": "integer"}},
        }
        self.assertEqual(validate_arguments(schema, {"order_id": "ORD-1"}), [])
        problems = validate_arguments(schema, {"count": "not-an-int"})
        self.assertTrue(any("order_id" in p for p in problems), problems)
        self.assertTrue(any("count" in p for p in problems), problems)


class GatewayGovernanceTests(unittest.TestCase):
    """ToolGateway.enforce_governance refuses without running connectors."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "gw.db")
        self.db.initialize()
        self.db.ensure_tenant("acme")

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _gateway(self, governance_service: object | None = None) -> object:
        from app.tools import ToolGateway

        return ToolGateway(
            self.db,
            capability_secret=b"capability-token-secret-0123456789abcdef",
            governance_service=governance_service,
            tool_policies={
                "refund.order": ToolPolicy(
                    name="refund.order",
                    side_effect="high_risk",
                    parameter_schema={
                        "type": "object",
                        "required": ["order_id"],
                        "properties": {"order_id": {"type": "string"}},
                    },
                )
            },
        )

    def test_unregistered_tool_refused(self) -> None:
        gateway = self._gateway()
        with self.assertRaises(Exception) as caught:
            gateway.enforce_governance("unknown.tool", "acme", {})
        self.assertIn("no registered governance policy", str(caught.exception))

    def test_schema_violation_refused(self) -> None:
        gateway = self._gateway()
        from app.tools import ToolGovernanceDenied

        with self.assertRaises(ToolGovernanceDenied) as caught:
            gateway.enforce_governance("orders.lookup", "acme", {"customer_ref": 123})
        self.assertEqual(caught.exception.reason, "schema")

    def test_high_risk_without_approval_fail_closed(self) -> None:
        from app.tools import ToolGovernanceDenied

        gateway = self._gateway(governance_service=None)
        with self.assertRaises(ToolGovernanceDenied) as caught:
            gateway.enforce_governance("refund.order", "acme", {"order_id": "ORD-1"})
        self.assertEqual(caught.exception.reason, "approval_required")

    def test_high_risk_with_pending_approval_still_refused(self) -> None:
        service = AiGovernanceService(self.db)
        service.request_approval(
            tenant_id="acme",
            subject_kind="tool_enablement",
            subject_id="refund.order",
            requested_by="maker",
        )
        gateway = self._gateway(governance_service=service)
        from app.tools import ToolGovernanceDenied

        with self.assertRaises(ToolGovernanceDenied) as caught:
            gateway.enforce_governance("refund.order", "acme", {"order_id": "ORD-1"})
        self.assertEqual(caught.exception.reason, "approval_required")


class EvalRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "eval.db")
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.service = AiGovernanceService(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_dataset_versions_monotonically_and_verifies_hash(self) -> None:
        first = self.service.register_dataset(
            tenant_id="acme",
            name="golden",
            strategy="golden",
            items=[{"q": "1"}],
            created_by="admin",
        )
        second = self.service.register_dataset(
            tenant_id="acme",
            name="golden",
            strategy="golden",
            items=[{"q": "2"}],
            created_by="admin",
        )
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertNotEqual(first["content_hash"], second["content_hash"])
        items = self.service.load_dataset_items(first["id"])
        self.assertEqual(items, [{"q": "1"}])

    def test_run_links_dataset_and_report(self) -> None:
        dataset = self.service.register_dataset(
            tenant_id=None,
            name="adv",
            strategy="adversarial",
            items=[{"case": "injection"}],
            created_by="admin",
        )
        run_id = self.service.record_eval_run(
            dataset_id=dataset["id"],
            candidate="prompt-v3",
            report_object_id="obj-123",
            passed=True,
            metrics={"pass_rate": 1.0},
        )
        row = self.service.latest_run_for(dataset_id=dataset["id"], candidate="prompt-v3")
        assert row is not None
        self.assertEqual((row["id"], row["report_object_id"]), (run_id, "obj-123"))

    def test_self_approval_forbidden_and_gate_fails_closed(self) -> None:
        approval = self.service.request_approval(
            tenant_id="acme",
            subject_kind="prompt_promotion",
            subject_id="v9",
            requested_by="maker",
        )
        with self.assertRaises(SelfApprovalError):
            self.service.decide_approval(approval["id"], decided_by="maker", approve=True)
        with self.assertRaises(ApprovalRequiredError):
            self.service.require_approved(
                tenant_id="acme", subject_kind="prompt_promotion", subject_id="v9"
            )
        self.service.decide_approval(approval["id"], decided_by="checker", approve=True)
        row = self.service.require_approved(
            tenant_id="acme", subject_kind="prompt_promotion", subject_id="v9"
        )
        self.assertEqual(row["decision"], "approved")

    def test_unreviewed_feedback_never_enters_dataset(self) -> None:
        staged = self.service.ingest_online_feedback(
            tenant_id="acme",
            conversation_id=None,
            source="csat",
            payload={
                "text": "the reply was wrong",
                "api_key": "sk-abcdef0123456789abcdef0123456789",
                "note": "bad reply",
            },
        )
        # The restricted key is stripped wholesale before persistence.
        self.assertEqual(staged["redacted"]["api_key"], "[REDACTED]")
        with self.assertRaises(GovernanceError):
            self.service.promote_feedback_to_dataset(
                feedback_ids=[staged["id"]],
                tenant_id="acme",
                dataset_name="feedback-set",
                requested_by="admin",
            )
        self.service.review_feedback(staged["id"], reviewed_by="human", accept=True)
        dataset = self.service.promote_feedback_to_dataset(
            feedback_ids=[staged["id"]],
            tenant_id="acme",
            dataset_name="feedback-set",
            requested_by="admin",
        )
        items = self.service.load_dataset_items(dataset["id"])
        self.assertEqual(items, [staged["redacted"]])


class DriftMonitorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Database(Path(self._tmp.name) / "drift.db")
        self.db.initialize()
        self.db.ensure_tenant("acme")
        self.quality = QualityService(self.db)
        self.registry = PromptRegistry(self.db)
        self.now = lambda: datetime(2026, 8, 23, 12, 0, tzinfo=UTC)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _settings(self, **overrides: object) -> Settings:
        kwargs: dict = {
            "drift_enabled": True,
            "drift_min_turns": 10,
            "drift_window_days": 1,
        }
        kwargs.update(overrides)
        return Settings(**kwargs)

    def _seed_canary(self) -> None:
        active = self.registry.create_version("acme", "triage_prompt", "1.0", "a", None, "admin")
        self.registry.activate("acme", active.id, "admin")
        canary = self.registry.create_version("acme", "triage_prompt", "1.1", "b", None, "admin")
        self.registry.set_canary("acme", canary.id, "admin")

    def test_escalation_breach_stops_canary_and_audits(self) -> None:
        for i in range(10):
            self.quality.record_turn(
                "acme",
                intent="support",
                prompt_version="v1.1",
                escalated=i < 8,
                latency_ms=50,
                first_response_seconds=None,
                estimated_tokens=10,
                date_str="2026-08-23",
            )
        self._seed_canary()
        monitor = DriftMonitor(
            self.db,
            self._settings(drift_max_escalation_rate=0.5),
            quality_service=self.quality,
            prompt_registry=self.registry,
            now=self.now,
        )
        reports = monitor.run_once(tenants=["acme"])
        self.assertEqual(len(reports), 1)
        self.assertIn("escalation_rate", {s.kind for s in reports[0].signals})
        self.assertEqual(reports[0].stopped_canaries, [{"name": "triage_prompt", "version": "1.1"}])
        self.assertIsNone(self.registry.get_canary_prompt("acme", "triage_prompt"))
        active = self.registry.get_active_prompt("acme", "triage_prompt")
        assert active is not None
        self.assertEqual(active.version, "1.0")
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE tenant_id='acme' "
                "AND event_type='ai.drift_canary_stopped'"
            ).fetchone()
        self.assertEqual(int(rows["n"]), 1)

    def test_small_sample_does_not_fire_rate_signals(self) -> None:
        self.quality.record_turn(
            "acme",
            intent="support",
            prompt_version="v1",
            escalated=True,
            latency_ms=50,
            first_response_seconds=None,
            estimated_tokens=10,
            date_str="2026-08-23",
        )
        monitor = DriftMonitor(
            self.db,
            self._settings(drift_max_escalation_rate=0.5),
            quality_service=self.quality,
            prompt_registry=self.registry,
            now=self.now,
        )
        self.assertEqual(monitor.collect_signals("acme"), [])

    def test_tool_denial_count_triggers(self) -> None:
        for _ in range(3):
            self.db.audit("acme", None, "gateway", "tool.denied", {"reason": "schema"})
        monitor = DriftMonitor(
            self.db,
            self._settings(drift_max_tool_denials=2),
            quality_service=self.quality,
            prompt_registry=self.registry,
            now=self.now,
        )
        signals = monitor.collect_signals("acme")
        kinds = {s.kind for s in signals}
        self.assertIn("tool_denials", kinds)

    def test_disabled_monitor_is_inert(self) -> None:
        monitor = DriftMonitor(
            self.db,
            self._settings(drift_enabled=False, drift_max_tool_denials=0),
            quality_service=self.quality,
            prompt_registry=self.registry,
            now=self.now,
        )
        self.assertEqual(monitor.run_once(tenants=["acme"]), [])


if __name__ == "__main__":
    unittest.main()
