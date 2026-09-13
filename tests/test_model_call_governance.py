"""H04 / T02 slice: every model transport clears the tenant policy first.

Before this slice the auxiliary model calls ran with no tenant policy at all:
``LanguageService`` (detection + translation), ``SummaryService`` and
``CopilotService`` each called ``ModelProvider.complete`` directly, and the
language detector ran at intake *before* the turn path's own budget /
allow-list / disable-surface checks. A tenant that denied the provider still
egressed through every one of them.

These tests pin the fix at both ends:

* the single :class:`~app.model_gateway.ModelCallGate` denies on the three
  policy facets (daily budget, allowed-model allow-list, 43.5 disable surface
  including region egress) and records the refusal;
* with a denying policy the fake provider records **zero** transports and each
  caller takes its existing deterministic fallback, while a permissive tenant
  still uses the model exactly once — the gate must not simply disable
  everything.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.control_plane import DataPlaneConfig, TenantControlPlane, TenantPolicy
from app.copilot import CopilotService
from app.database import Database, utc_now
from app.language import LanguageService
from app.model_gateway import (
    MODEL_CALL_PURPOSES,
    PURPOSE_LANGUAGE_DETECT,
    ModelCallDenied,
    ModelCallGate,
)
from app.model_provider import ModelResponse
from app.orchestrator import ConversationOrchestrator
from app.summaries import SummaryService

SECRET = "control-plane-signing-secret-0123456789abcdef"
TENANT = "acme"
DEFAULT_MODEL = "gpt-4.1-mini"


def _policy(**overrides: object) -> TenantPolicy:
    kwargs: dict = {"plan": "standard", "region": "local"}
    kwargs.update(overrides)
    return TenantPolicy(**kwargs)


class FakeModelProvider:
    """Deterministic provider stub that counts every transport attempt."""

    def __init__(self, *, detect: str = "ja", translation: str = "Translated reply.") -> None:
        self.detect = detect
        self.translation = translation
        self.calls: list[tuple[str, str]] = []

    def complete(
        self, system_prompt: str, user_prompt: str, model_ref: str | None = None
    ) -> ModelResponse:
        self.calls.append((system_prompt, user_prompt))
        if "language identification" in system_prompt:
            payload = {"language": self.detect}
        elif "translator" in system_prompt:
            payload = {"translation": self.translation}
        elif "suggestions" in system_prompt:
            payload = {"suggestions": ["We can help with that."]}
        elif "rewritten" in system_prompt:
            payload = {"rewritten": "Rewritten draft."}
        else:
            payload = {"summary": "Model summary of the conversation."}
        return ModelResponse(content=json.dumps(payload, ensure_ascii=False))


class GateFixture(unittest.TestCase):
    """Shared tenant/plane fixture for the policy-backed gate cases."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "gateway.db"
        self.db = Database(self.db_path)
        self.db.initialize()
        self.db.ensure_tenant(TENANT)
        self.cp = TenantControlPlane(self.db, SECRET)
        self.dp = DataPlaneConfig(self.db, SECRET)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def _gate(self, **kwargs: object) -> ModelCallGate:
        return ModelCallGate(
            self.db,
            plane_resolver=lambda: self.dp,
            default_model_ref=DEFAULT_MODEL,
            **kwargs,
        )

    def _deny_provider(self) -> None:
        self.cp.set_policy(TENANT, _policy(model_policy={"disabled_providers": ["openai"]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))


class ModelCallGateTests(GateFixture):
    def test_purpose_catalogue_is_closed(self) -> None:
        self.assertIn("triage", MODEL_CALL_PURPOSES)
        self.assertIn("language_detect", MODEL_CALL_PURPOSES)
        self.assertEqual(PURPOSE_LANGUAGE_DETECT, "language_detect")

    def test_unknown_purpose_is_rejected(self) -> None:
        gate = self._gate()
        with self.assertRaises(ValueError):
            gate.evaluate(TENANT, "not_a_purpose")

    def test_absent_policy_allows(self) -> None:
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertTrue(decision.allowed)
        self.assertEqual(decision.model_ref, DEFAULT_MODEL)

    def test_tenant_without_policy_row_allows(self) -> None:
        decision = ModelCallGate(None, default_model_ref=DEFAULT_MODEL).evaluate(
            TENANT, PURPOSE_LANGUAGE_DETECT
        )
        self.assertTrue(decision.allowed)

    def test_daily_budget_exhausted_denies(self) -> None:
        self.db.set_tenant_model_policy(TENANT, None, 1)
        self.db.increment_tenant_usage(TENANT, utc_now()[:10])
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertFalse(decision.allowed)
        self.assertIn("budget", decision.reason)

    def test_model_outside_allow_list_denies(self) -> None:
        self.db.set_tenant_model_policy(TENANT, ["gpt-4o"], None)
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertFalse(decision.allowed)
        self.assertIn("allow-list", decision.reason)

    def test_disabled_provider_denies(self) -> None:
        self._deny_provider()
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertFalse(decision.allowed)
        self.assertIn("provider", decision.reason)
        self.assertEqual(decision.purpose, "language_detect")

    def test_disabled_model_denies_auxiliary_call(self) -> None:
        self.cp.set_policy(TENANT, _policy(model_policy={"disabled_models": [DEFAULT_MODEL]}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertFalse(decision.allowed)
        self.assertIn("disabled", decision.reason)

    def test_region_egress_denies(self) -> None:
        self.cp.set_policy(TENANT, _policy(model_policy={"allow_data_egress": False}))
        self.dp.apply_snapshot(self.cp.issue_snapshot(TENANT))
        decision = self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertFalse(decision.allowed)
        self.assertIn("region", decision.reason)

    def test_authorize_raises_typed_error(self) -> None:
        self._deny_provider()
        with self.assertRaises(ModelCallDenied) as ctx:
            self._gate().authorize(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertEqual(ctx.exception.purpose, "language_detect")
        self.assertEqual(ctx.exception.tenant_id, TENANT)

    def test_denial_is_counted_for_visibility(self) -> None:
        from app import telemetry

        def denied_total() -> float:
            counters = telemetry.metrics.snapshot()["counters"]
            return sum(value for key, value in counters.items() if "model.call_denied" in key)

        self._deny_provider()
        before = denied_total()
        self._gate().evaluate(TENANT, PURPOSE_LANGUAGE_DETECT)
        self.assertEqual(denied_total(), before + 1)


class AuxiliaryCallGovernanceTests(GateFixture):
    """Each auxiliary surface must not reach transport under a denying policy."""

    def _conversation(self, name: str) -> str:
        conv = self.db.create_conversation(TENANT, name, None, "web", "admin", 120)
        self.db.add_message(
            TENANT,
            conv["id"],
            "customer",
            name,
            "我的订单到哪里了？",
            {"source_actor": "admin"},
            "turn-1",
        )
        return conv["id"]

    # ------------------------------------------------------ language detection

    def test_detect_uses_model_when_allowed(self) -> None:
        provider = FakeModelProvider(detect="ja")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        language, source = service.detect("Hello there", tenant_id=TENANT)
        self.assertEqual((language, source), ("ja", "model"))
        self.assertEqual(len(provider.calls), 1)

    def test_detect_makes_zero_transports_when_denied(self) -> None:
        self._deny_provider()
        provider = FakeModelProvider(detect="ja")
        service = LanguageService(provider, "zh", model_gate=self._gate())
        language, source = service.detect("Hello there", tenant_id=TENANT)
        self.assertEqual((language, source), ("en", "rule"))
        self.assertEqual(provider.calls, [])

    # ------------------------------------------------------------ translation

    def test_translate_uses_model_when_allowed(self) -> None:
        provider = FakeModelProvider()
        service = LanguageService(provider, "zh", model_gate=self._gate())
        text, translated, source = service.translate("退款已批准。", "en", tenant_id=TENANT)
        self.assertEqual((text, translated, source), ("Translated reply.", True, "model"))
        self.assertEqual(len(provider.calls), 1)

    def test_translate_makes_zero_transports_when_denied(self) -> None:
        self._deny_provider()
        provider = FakeModelProvider()
        service = LanguageService(provider, "zh", model_gate=self._gate())
        text, translated, source = service.translate("退款已批准。", "en", tenant_id=TENANT)
        self.assertEqual((text, translated, source), ("退款已批准。", False, "rule"))
        self.assertEqual(provider.calls, [])

    # --------------------------------------------------------------- summaries

    def test_summary_uses_model_when_allowed(self) -> None:
        conv_id = self._conversation("Summary Allowed")
        provider = FakeModelProvider()
        service = SummaryService(self.db, provider, model_gate=self._gate())
        row = service.generate(TENANT, conv_id, "context")
        self.assertEqual(row["source"], "model")
        self.assertEqual(len(provider.calls), 1)

    def test_summary_makes_zero_transports_when_denied(self) -> None:
        conv_id = self._conversation("Summary Denied")
        self._deny_provider()
        provider = FakeModelProvider()
        service = SummaryService(self.db, provider, model_gate=self._gate())
        row = service.generate(TENANT, conv_id, "context")
        self.assertEqual(row["source"], "rule")
        self.assertEqual(provider.calls, [])

    # ----------------------------------------------------------------- copilot

    def test_copilot_suggest_uses_model_when_allowed(self) -> None:
        conv_id = self._conversation("Copilot Allowed")
        provider = FakeModelProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        suggestions = service.suggest_reply(TENANT, conv_id)
        self.assertEqual(suggestions[0]["source"], "model")
        self.assertEqual(len(provider.calls), 1)

    def test_copilot_suggest_makes_zero_transports_when_denied(self) -> None:
        conv_id = self._conversation("Copilot Denied")
        self._deny_provider()
        provider = FakeModelProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        suggestions = service.suggest_reply(TENANT, conv_id)
        self.assertTrue(all(item["source"] == "rule" for item in suggestions))
        self.assertEqual(provider.calls, [])

    def test_copilot_rewrite_makes_zero_transports_when_denied(self) -> None:
        self._deny_provider()
        provider = FakeModelProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        result = service.rewrite_tone("请稍等。", "friendly", tenant_id=TENANT)
        self.assertEqual(result["source"], "rule")
        self.assertEqual(provider.calls, [])

    def test_copilot_rewrite_uses_model_when_allowed(self) -> None:
        provider = FakeModelProvider()
        service = CopilotService(self.db, provider, model_gate=self._gate())
        result = service.rewrite_tone("请稍等。", "friendly", tenant_id=TENANT)
        self.assertEqual(result["source"], "model")
        self.assertEqual(len(provider.calls), 1)


class TurnIntakeGovernanceTests(GateFixture):
    """The turn path must gate intake detection with the same decision."""

    def _orchestrator(self, provider: FakeModelProvider) -> ConversationOrchestrator:
        orchestrator = ConversationOrchestrator(
            self.db, Settings(database_path=self.db_path, auth_mode="demo"), provider
        )
        orchestrator.data_plane_config = self.dp
        return orchestrator

    def _customer_metadata(self, conversation_id: str) -> dict:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT metadata_json FROM messages WHERE tenant_id = ? "
                "AND conversation_id = ? AND role = 'customer' ORDER BY seq LIMIT 1",
                (TENANT, conversation_id),
            ).fetchone()
        return json.loads(row["metadata_json"]) if row else {}

    def test_intake_detection_skips_transport_when_denied(self) -> None:
        self._deny_provider()
        provider = FakeModelProvider(detect="ja")
        orchestrator = self._orchestrator(provider)
        conv = self.db.create_conversation(TENANT, "Intake Denied", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            TENANT, conv["id"], "配送一般多久能到？", "admin", "idem-intake-1"
        )
        self.assertEqual(provider.calls, [])
        self.assertEqual(self._customer_metadata(conv["id"])["language_source"], "rule")

    def test_intake_detection_uses_model_when_allowed(self) -> None:
        provider = FakeModelProvider(detect="ja")
        orchestrator = self._orchestrator(provider)
        conv = self.db.create_conversation(TENANT, "Intake Allowed", None, "web", "admin", 120)
        orchestrator.handle_customer_message(
            TENANT, conv["id"], "配送一般多久能到？", "admin", "idem-intake-2"
        )
        self.assertTrue(provider.calls)
        self.assertEqual(self._customer_metadata(conv["id"])["language_source"], "model")


class BootstrapWiringTests(unittest.TestCase):
    """Production assembly must actually deliver the gate and the control plane.

    The 43.5 disable surface had only ever been attached in tests
    (``orchestrator.data_plane_config = dp``); bootstrap never set it, so the
    gate fail-opened for every real deployment. Pin the wiring so it cannot
    silently regress.
    """

    def test_control_plane_reaches_orchestrator_and_gate(self) -> None:
        from app.bootstrap import build_application

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                database_path=root / "boot.db",
                auth_mode="demo",
                docs_enabled=False,
                turn_worker_enabled=False,
                control_plane_secret=SECRET,
                eval_worm_dir=str(root / "worm"),
            )
            ctx = build_application(settings)
            try:
                self.assertIsNotNone(ctx.services.data_plane_config)
                self.assertIs(ctx.orchestrator.data_plane_config, ctx.services.data_plane_config)
                gate = ctx.orchestrator.model_gate
                self.assertIsNotNone(gate)
                self.assertIs(gate.database, ctx.database)
                self.assertIs(gate.plane_resolver(), ctx.services.data_plane_config)
                # The auxiliary surfaces the orchestrator owns share that gate.
                self.assertIs(ctx.orchestrator.languages.model_gate, gate)
                self.assertIs(ctx.orchestrator.summaries.model_gate, gate)
                self.assertIs(ctx.services.copilot.model_gate, gate)
            finally:
                ctx.database.close()

    def test_orchestrator_declares_the_plane_it_has_not_been_given_yet(self) -> None:
        """The plane attribute belongs to the orchestrator, not to bootstrap.

        bootstrap builds the plane *after* the orchestrator and then assigns it,
        so the orchestrator has to declare the attribute itself. An undeclared
        attribute type-checks as unknown (``reportAttributeAccessIssue`` -- CI
        rejected exactly that) and makes the fail-open default implicit. Pin the
        declaration at runtime so a local gate set without pyright still sees
        its removal.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                database_path=root / "orchestrator.db",
                auth_mode="demo",
                docs_enabled=False,
                turn_worker_enabled=False,
                eval_worm_dir=str(root / "worm"),
            )
            database = Database(settings.database_path)
            database.initialize()
            database.ensure_tenant(TENANT)
            try:
                orchestrator = ConversationOrchestrator(database, settings)
                # Declared, not injected: reading it before bootstrap runs is
                # legal and yields the "no control plane" default.
                self.assertIsNone(orchestrator.data_plane_config)
                self.assertIsNone(orchestrator.model_gate.plane_resolver())
                self.assertTrue(orchestrator.model_gate.is_allowed(TENANT, PURPOSE_LANGUAGE_DETECT))
            finally:
                database.close()

    def test_short_secret_leaves_the_gate_fail_open(self) -> None:
        from app.bootstrap import build_application

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            settings = Settings(
                database_path=root / "boot2.db",
                auth_mode="demo",
                docs_enabled=False,
                turn_worker_enabled=False,
                control_plane_secret="too-short",
                widget_secret="too-short",
                eval_worm_dir=str(root / "worm"),
            )
            ctx = build_application(settings)
            try:
                self.assertIsNone(ctx.services.data_plane_config)
                self.assertIsNone(ctx.orchestrator.data_plane_config)
                self.assertTrue(
                    ctx.orchestrator.model_gate.is_allowed(TENANT, PURPOSE_LANGUAGE_DETECT)
                )
            finally:
                ctx.database.close()


if __name__ == "__main__":
    unittest.main()
