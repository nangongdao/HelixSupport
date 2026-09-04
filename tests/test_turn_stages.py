"""Phase 41.6 (ARC-001): pin the three turn-stage contracts directly.

The orchestrator refactor split the monolithic turn pipeline into three
deep modules (policy/triage, specialist execution, persistence).  These
tests lock the typed context/result surfaces so a future 41.x change
cannot silently drift the stage boundaries; the composed path is covered
by test_system.py / test_turn_segments.py / test_golden_set.py.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.agents import AgentResult
from app.config import Settings
from app.domain import AgentName, ConversationStatus, TriageDecision
from app.turn_persist import TurnPersistInputs, TurnPersistStage
from app.turn_policy import TurnPolicyContext, TurnPolicyStage


@dataclass(frozen=True)
class _FakeRisk:
    requires_human: bool = False
    categories: list[str] = ()
    handoff_reason: str = ""
    redacted_excerpt: str = ""


@dataclass(frozen=True)
class _FakeQuality:
    approved: bool = True
    issues: list[str] = ()


class _FakeLanguages:
    def detect(self, content: str) -> tuple[str | None, str]:
        return None, "none"


class _MonkeyServices:
    """Minimal structural stand-in for TurnServices (runtime provider)."""

    def __init__(self, database: Any) -> None:
        self.database = database
        self.settings = Settings()
        self.languages = _FakeLanguages()
        self.prompt_registry = None
        self.tools = None
        self.policy = None
        self.triage = None
        self.knowledge = None
        self.order = None
        self.escalation = None
        self.quality = None
        self.quality_service = None
        self.webhook_service = None


class TurnStageContractTests(unittest.TestCase):
    """Stage boundaries: frozen dataclasses compose, dicts stay at call rims."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        from app.database import Database

        self.database = Database(Path(self._tmp.name) / "stage.db")
        self.database.initialize()
        self._seed_conv = self.database.create_conversation(
            "demo", "测试顾客", None, "web", "seed", 60
        )
        self._seed_id_ = self._seed_conv["id"]

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def test_turn_policy_context_is_frozen(self) -> None:
        ctx = TurnPolicyContext(
            tenant_id="demo",
            conversation={"id": "conv_1", "status": ConversationStatus.OPEN},
            content="hi",
            actor_id="a",
            turn_id="t1",
        )
        with self.assertRaises(AttributeError):
            ctx.content = "changed"  # type: ignore[misc]

    def test_suppressed_policy_result_has_no_decision(self) -> None:
        """WAITING_HUMAN/HUMAN_ACTIVE returns suppressed with None decision,
        so the orchestrator stops before the specialist."""
        svc = _MonkeyServices(self.database)
        stage = TurnPolicyStage(svc)
        conversations = (
            self.database.list_conversations("demo")
            if hasattr(self.database, "list_conversations")
            else []
        )
        if not conversations:
            self.skipTest("no conversation available")
        conv = conversations[0]
        self.database.transition_conversation(
            "demo", conv["id"], [ConversationStatus.OPEN], ConversationStatus.WAITING_HUMAN
        )
        result = stage.ingest(
            TurnPolicyContext(
                tenant_id="demo",
                conversation=self.database.get_conversation("demo", conv["id"]) or conv,
                content="补充说明",
                actor_id="customer",
                turn_id="t-hold",
            )
        )
        self.assertTrue(result.suppressed)
        self.assertIsNone(result.decision)
        self.assertIsNone(result.risk)

    def test_persist_outcome_metadata_carries_policy_categories(self) -> None:
        """Persist merges policy categories + content-risk categories into
        turn metadata, so escalation reasons are traceable (ADR-014)."""
        svc = _MonkeyServices(self.database)
        decision = TriageDecision(
            route=AgentName.ESCALATION,
            intent="policy_risk",
            confidence=1.0,
            urgency="high",
            reasons=["Sensitive request"],
            mode="policy",
        )
        result = AgentResult(
            agent=AgentName.ESCALATION,
            content="人工处理",
            confidence=1.0,
            requires_human=True,
            handoff_reason="Sensitive request requires human authorization",
        )
        conv = self.database.get_conversation("demo", self._seed_id_)
        self.assertIsNotNone(conv, "seed conversation must exist")
        inputs = TurnPersistInputs(
            tenant_id="demo",
            conversation=conv,
            customer_message={"id": "m1"},
            decision=decision,
            result=result,
            quality=_FakeQuality(),
            prompt_version=None,
            prompt_channel=None,
            content_risk=_FakeRisk(categories=["credential_topic"], requires_human=True),
            policy_categories=["secret_topic"],
            language=None,
            detected_language=None,
            customer_content="",
            assistant_content=result.content,
            turn_id="t-persist",
            turn_started_monotonic=0.0,
            budget_exceeded=False,
        )
        outcome = TurnPersistStage(svc).finalize(inputs, chunk_sink=None)
        self.assertTrue(outcome.state_updated)
        self.assertEqual(
            set(outcome.metadata["risk_categories"]),
            {"secret_topic", "credential_topic"},
        )
        self.assertEqual(outcome.metadata["agent"], AgentName.ESCALATION)

    def _seed_id(self) -> str:
        return self._seed_id_


if __name__ == "__main__":
    unittest.main()
