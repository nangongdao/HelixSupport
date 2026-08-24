"""Specialist-execution stage of the conversation pipeline (Phase 41.6 / ARC-001).

Extracted from ``app/orchestrator.py``: given a triage decision, run the
routed specialist (escalation / order-with-CRM-identity / knowledge),
re-check
retrieved knowledge content against policy (indirect-injection defense,
ADR-014), run the quality gate, and downgrade to an escalation reply when the
gate fails.  The public surface is one typed step (``execute``) returning the
specialist result plus the content risk it surfaced, so the orchestrator
composes the stage without reaching into this module's internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

from app.agents import (
    AgentResult,
)
from app.domain import AgentName, QualityAssessment, RiskAssessment, TriageDecision
from app.turn_services import TurnServices

logger = logging.getLogger("helix")


@dataclass(frozen=True)
class TurnExecutionContext:
    """Immutable inputs the specialist stage needs (ARC-001 typed context)."""

    tenant_id: str
    conversation: dict[str, Any]
    content: str
    decision: TriageDecision
    policy_reason: str | None
    language: str | None


@dataclass(frozen=True)
class TurnExecutionResult:
    """Outcome of the specialist + quality-gate stage for one turn."""

    result: AgentResult
    content_risk: RiskAssessment | None
    quality: QualityAssessment
    segment_elapsed: float


class TurnExecutionStage:
    def __init__(self, services: TurnServices) -> None:
        """Bind the live service provider; services are read at call time."""
        self.services = services

    def execute(self, context: TurnExecutionContext) -> TurnExecutionResult:
        """Run the routed specialist, then the quality gate.

        Mirrors the orchestrator's specialist→quality passage.  When the
        quality gate fails the result is replaced by an escalation reply that
        keeps the original tool calls (the audit trail of what ran).
        """
        started = monotonic()
        result, content_risk = self._run_specialist(context)
        quality = self.services.quality.review(
            result, self.services.settings.auto_escalate_threshold
        )
        self.services.database.audit(
            context.tenant_id,
            context.conversation["id"],
            AgentName.QUALITY,
            "quality.reviewed",
            {
                "approved": quality.approved,
                "issues": quality.issues,
                "reviewed_agent": result.agent,
            },
        )
        if not quality.approved:
            original_tools = result.tool_calls
            result = replace(
                self.services.escalation.respond(
                    "Response quality gate failed: " + ", ".join(quality.issues)
                ),
                tool_calls=original_tools,
            )
        for tool_call in result.tool_calls:
            self.services.database.audit(
                context.tenant_id,
                context.conversation["id"],
                result.agent,
                "tool.executed",
                tool_call,
            )
        return TurnExecutionResult(
            result=result,
            content_risk=content_risk,
            quality=quality,
            segment_elapsed=max(0.0, (monotonic() - started) * 1000),
        )

    def _run_specialist(
        self, context: TurnExecutionContext
    ) -> tuple[AgentResult, RiskAssessment | None]:
        """Run the routed specialist; returns ``(result, content_risk)``.

        ``content_risk`` is non-None when the specialist escalated because
        *retrieved* material failed policy inspection (indirect prompt
        injection / planted secrets): the caller merges its categories into
        the turn metadata so the escalation is traceable (ADR-014).
        """
        decision = context.decision
        tenant_id = context.tenant_id
        conversation = context.conversation
        if decision.route == AgentName.ESCALATION:
            return (
                self.services.escalation.respond(
                    context.policy_reason or "Sensitive request requires human authorization"
                ),
                None,
            )
        if decision.route == AgentName.ORDER:
            customer_ref = conversation.get("customer_ref")
            crm_record: dict[str, Any] | None = None
            if customer_ref:
                crm = self.services.tools.resolve_customer(tenant_id, str(customer_ref))
                crm_record = crm.public_record()
                if crm.code == "unavailable":
                    return (
                        AgentResult(
                            agent=AgentName.ORDER,
                            content=(
                                "客户资料服务暂时不可用。为避免给出过期信息，我已转交人工客服继续核实。"
                            ),
                            confidence=1.0,
                            tool_calls=[crm_record],
                            requires_human=True,
                            handoff_reason="CRM connector unavailable",
                        ),
                        None,
                    )
                if crm.code == "not_found":
                    return (
                        AgentResult(
                            agent=AgentName.ORDER,
                            content=(
                                "当前会话的客户身份无法在当前租户验证。为保护订单信息，"
                                "我已转交人工客服核验。"
                            ),
                            confidence=1.0,
                            tool_calls=[crm_record],
                            requires_human=True,
                            handoff_reason=("Customer reference not resolvable in this tenant"),
                        ),
                        None,
                    )
                if crm.code == "ok" and crm.output.get("customer_ref"):
                    customer_ref = crm.output["customer_ref"]
            result = self.services.order.respond(tenant_id, customer_ref, context.content)
            if crm_record is None:
                return result, None
            return replace(result, tool_calls=[crm_record, *result.tool_calls]), None

        result = self.services.knowledge.respond(
            tenant_id, context.content, language=context.language
        )
        # Phase 41.5 (AI-001): indirect prompt injection.  Re-run the full
        # policy over whatever the knowledge agent produced, so a planted
        # article that slipped past the agent's own scan is caught here and
        # its risk merged into the turn metadata (traceability).
        if result.agent == AgentName.KNOWLEDGE:
            retrieved_risk = self.services.policy.inspect(result.content)
            if retrieved_risk.requires_human:
                if result.requires_human:
                    return result, retrieved_risk
                return (
                    replace(
                        self.services.escalation.respond(
                            "Retrieved knowledge content failed policy inspection: "
                            + (retrieved_risk.handoff_reason or "unsafe content")
                        ),
                        tool_calls=result.tool_calls,
                    ),
                    retrieved_risk,
                )
            if result.requires_human and result.handoff_reason:
                inferred = self._infer_retrieval_risk(result.handoff_reason)
                if inferred is not None:
                    return result, inferred
        return result, None

    @staticmethod
    def _infer_retrieval_risk(handoff_reason: str) -> RiskAssessment | None:
        """Map a knowledge-agent refusal reason back to its risk class.

        The agent refuses dangerous retrieved text without echoing it, so the
        orchestrator cannot inspect the article itself; the refusal reason is
        the authoritative record of which class of risk was found.
        """
        if "instruction-override" in handoff_reason:
            return RiskAssessment(
                categories=["prompt_injection"],
                requires_human=True,
                handoff_reason=handoff_reason,
            )
        if "secret" in handoff_reason:
            return RiskAssessment(
                categories=["credential_topic"],
                requires_human=True,
                handoff_reason=handoff_reason,
            )
        return None


__all__ = ["TurnExecutionContext", "TurnExecutionResult", "TurnExecutionStage"]
