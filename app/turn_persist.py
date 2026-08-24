"""Turn finalization/persistence stage (Phase 41.6 / ARC-001).

Extracted from ``app/orchestrator.py``: everything after the specialist
produced a result -- routing-state transition (with SLA deadline), auto-assign,
assistant-message persist (with translation), quality aggregate, telemetry,
webhook dispatch, and the final response dict.  The orchestrator composes this
stage with a typed context and a typed result; it never reaches into this
module's internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Callable
from uuid import uuid4

from app.agents import AgentResult
from app.database import utc_now
from app.domain import (
    ConversationStatus,
    QualityAssessment,
    RiskAssessment,
    TriageDecision,
)
from app.prompts import PromptVersion
from app.quality import estimate_tokens
from app.telemetry import metrics as telemetry_metrics
from app.turn_services import TurnServices
from app.webhooks import EVENT_CONVERSATION_ESCALATED

logger = logging.getLogger("helix")


@dataclass(frozen=True)
class TurnPersistInputs:
    """Everything the persist stage needs for one resolved specialist result."""

    tenant_id: str
    conversation: dict[str, Any]
    customer_message: dict[str, Any]
    decision: TriageDecision
    result: AgentResult
    quality: QualityAssessment
    prompt_version: PromptVersion | None
    prompt_channel: str | None
    content_risk: RiskAssessment | None
    policy_categories: list[str]
    language: str | None
    detected_language: str | None
    customer_content: str
    assistant_content: str
    turn_id: str
    turn_started_monotonic: float
    budget_exceeded: bool


@dataclass(frozen=True)
class TurnPersistOutcome:
    """Output of the persist stage.

    ``assistant_message`` is None only when the routing state changed under
    us (automation suppressed mid-turn).
    """

    state_updated: bool
    next_status: ConversationStatus
    priority: str
    sla_minutes: int
    metadata: dict[str, Any]
    assistant_content: str
    assistant_message: dict[str, Any] | None
    elapsed: float


class TurnPersistStage:
    def __init__(self, services: TurnServices) -> None:
        """Bind the live service provider; services are read at call time."""
        self.services = services

    def finalize(
        self, inputs: TurnPersistInputs, chunk_sink: Callable[[str], None] | None
    ) -> TurnPersistOutcome:
        """Persist the turn outcome; returns the final response parts."""
        started = monotonic()
        tenant_id = inputs.tenant_id
        conversation_id = inputs.conversation["id"]
        result = inputs.result
        decision = inputs.decision
        prompt_version = inputs.prompt_version
        turn_started = inputs.turn_started_monotonic

        next_status = (
            ConversationStatus.WAITING_HUMAN if result.requires_human else ConversationStatus.OPEN
        )
        priority = "high" if result.requires_human or decision.urgency == "high" else "normal"
        sla_minutes = self.services.database.resolve_sla_policy(
            tenant_id,
            priority,
            inputs.conversation.get("channel") or "web",
            self.services.settings.high_sla_minutes
            if next_status == ConversationStatus.WAITING_HUMAN
            else self.services.settings.normal_sla_minutes,
        )
        state_updated = self.services.database.set_routing(
            tenant_id,
            conversation_id,
            next_status,
            decision.intent,
            str(result.agent),
            priority,
            result.handoff_reason,
            result.confidence,
            sla_minutes,
        )
        if state_updated and next_status == ConversationStatus.OPEN:
            self._maybe_auto_assign(
                tenant_id,
                conversation_id,
                inputs.conversation,
                intent=decision.intent,
                channel=inputs.conversation.get("channel"),
            )
        if not state_updated:
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "automation.suppressed",
                {"reason": "conversation_state_changed_during_agent_run"},
            )
            return TurnPersistOutcome(
                state_updated=False,
                next_status=next_status,
                priority=priority,
                sla_minutes=sla_minutes,
                metadata={},
                assistant_content="",
                assistant_message=None,
                elapsed=max(0.0, (monotonic() - started) * 1000),
            )

        if next_status == ConversationStatus.WAITING_HUMAN:
            self._emit_webhook(
                tenant_id,
                EVENT_CONVERSATION_ESCALATED,
                {
                    "conversation_id": conversation_id,
                    "customer_name": inputs.conversation["customer_name"],
                    "reason": result.handoff_reason,
                    "agent": str(result.agent),
                    "intent": decision.intent,
                    "priority": priority,
                },
            )

        metadata = {
            "agent": result.agent,
            "confidence": result.confidence,
            "intent": decision.intent,
            "route_mode": decision.mode,
            "risk_categories": [
                *inputs.policy_categories,
                *(inputs.content_risk.categories if inputs.content_risk else []),
            ],
            "content_inspection_escalated": inputs.content_risk is not None,
            "citations": result.citations,
            "tool_calls": result.tool_calls,
            "handoff_reason": result.handoff_reason,
            "quality_approved": inputs.quality.approved,
            "quality_issues": inputs.quality.issues,
            "prompt_version_id": prompt_version.id if prompt_version else None,
            "prompt_name": prompt_version.name if prompt_version else None,
            "prompt_version": prompt_version.version if prompt_version else None,
            "prompt_channel": inputs.prompt_channel,
            "budget_exceeded": inputs.budget_exceeded,
        }
        assistant_content = result.content
        if inputs.language:
            metadata["language"] = inputs.language
            metadata["detected_language"] = inputs.detected_language
            if inputs.language != self.services.languages.service_language:
                translated, did_translate, translation_source = self.services.languages.translate(
                    result.content, inputs.language
                )
                if did_translate:
                    assistant_content = translated
                    metadata["original_content"] = result.content
                metadata["translated"] = did_translate
                metadata["translation_source"] = translation_source
                self.services.database.audit(
                    tenant_id,
                    conversation_id,
                    result.agent,
                    "reply.translated",
                    {
                        "to_language": inputs.language,
                        "translated": did_translate,
                        "source": translation_source,
                    },
                )
            else:
                metadata["translated"] = False
                metadata["translation_source"] = "none"
        telemetry_metrics.increment(
            "turn.processed",
            tenant_id=tenant_id,
            prompt_channel=metadata.get("prompt_channel"),
            prompt_version=prompt_version.version if prompt_version else "default",
            budget_exceeded=str(inputs.budget_exceeded),
        )
        telemetry_metrics.observe(
            "turn.latency_ms",
            max(0, int((monotonic() - turn_started) * 1000)),
            route="customer_message",
        )
        queue_stats = self.services.database.turn_job_stats(tenant_id)
        telemetry_metrics.observe(
            "turn.queue_staleness_seconds",
            float(queue_stats.get("oldest_queued_age_seconds", 0)),
            tenant_id=tenant_id,
        )
        self.services.database.increment_tenant_usage(tenant_id, utc_now()[:10])
        assistant_message = self.services.database.add_message(
            tenant_id,
            conversation_id,
            "assistant",
            str(result.agent),
            assistant_content,
            metadata,
            inputs.turn_id,
        )
        self.services.database.audit(
            tenant_id,
            conversation_id,
            result.agent,
            "agent.responded",
            {
                "message_id": assistant_message["id"],
                "confidence": result.confidence,
                "requires_human": result.requires_human,
            },
        )
        if chunk_sink is not None and assistant_content:
            try:
                chunk_sink(assistant_content)
            except Exception:
                logger.exception("stream.chunk_sink_failed")
        try:
            self._record_quality_turn(
                tenant_id=tenant_id,
                customer_message=inputs.customer_message,
                decision=decision,
                result=result,
                prompt_version=prompt_version,
                customer_content=inputs.customer_content,
                assistant_content=result.content,
                turn_started_monotonic=turn_started,
            )
        except Exception:
            logger.exception("failed to record quality turn")
        elapsed = max(0.0, (monotonic() - started) * 1000)
        return TurnPersistOutcome(
            state_updated=True,
            next_status=next_status,
            priority=priority,
            sla_minutes=sla_minutes,
            metadata=metadata,
            assistant_content=assistant_content,
            assistant_message=assistant_message,
            elapsed=elapsed,
        )

    def _maybe_auto_assign(
        self,
        tenant_id: str,
        conversation_id: str,
        conversation: dict[str, Any],
        *,
        intent: str | None,
        channel: str | None,
    ) -> None:
        if conversation.get("assigned_agent"):
            return
        label = None
        labels = conversation.get("labels") or []
        if isinstance(labels, list) and labels:
            label = str(labels[0])
        try:
            group_id = self.services.database.find_routing_group(
                tenant_id, intent=intent, label=label, channel=channel
            )
        except Exception:
            logger.exception("auto-routing lookup failed")
            return
        if group_id is None:
            return
        agent = None
        try:
            agent = self.services.database.pick_agent_for_group(tenant_id, group_id)
        except Exception:
            logger.exception("auto-routing agent pick failed")
        if agent is None:
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "routing.group_full",
                {"group_id": group_id, "intent": intent, "channel": channel},
            )
            return
        try:
            self.services.database.transition_conversation(
                tenant_id,
                conversation_id,
                [ConversationStatus.OPEN, ConversationStatus.WAITING_HUMAN],
                ConversationStatus.OPEN,
                assigned_agent=agent,
            )
        except Exception:
            logger.exception("auto-routing assign failed")
            return
        self.services.database.audit(
            tenant_id,
            conversation_id,
            "orchestrator",
            "routing.assigned",
            {"group_id": group_id, "assigned_agent": agent, "intent": intent},
        )

    def _record_quality_turn(
        self,
        *,
        tenant_id: str,
        customer_message: dict[str, Any] | None,
        decision: TriageDecision,
        result: AgentResult,
        prompt_version: PromptVersion | None,
        customer_content: str,
        assistant_content: str,
        turn_started_monotonic: float,
    ) -> None:
        """Persist one turn into the daily quality aggregate (Phase 21.1)."""
        latency_ms = max(0, int((monotonic() - turn_started_monotonic) * 1000))
        first_response_seconds: float | None = None
        customer_created_at = customer_message.get("created_at") if customer_message else None
        if customer_created_at:
            try:
                received_at = datetime.fromisoformat(customer_created_at)
                first_response_seconds = max(0.0, (datetime.now(UTC) - received_at).total_seconds())
            except (ValueError, TypeError):
                first_response_seconds = None
        prompt_version_label = prompt_version.version if prompt_version else "default"
        tokens = estimate_tokens(customer_content) + estimate_tokens(assistant_content)
        self.services.quality_service.record_turn(
            tenant_id,
            intent=decision.intent,
            prompt_version=prompt_version_label,
            escalated=result.requires_human,
            latency_ms=latency_ms,
            first_response_seconds=first_response_seconds,
            estimated_tokens=tokens,
        )

    def _emit_webhook(self, tenant_id: str, event: str, payload: dict[str, Any]) -> None:
        """Enqueue an outbound webhook event, never breaking the conversation
        path: subscription failures are logged and ignored."""
        if self.services.webhook_service is None:
            return
        try:
            self.services.webhook_service.emit_event(tenant_id, event, payload, str(uuid4()))
        except Exception:
            logger.exception(
                "webhook.emit_failed",
                extra={"event_type": event, "tenant_id": tenant_id},
            )


__all__ = ["TurnPersistInputs", "TurnPersistOutcome", "TurnPersistStage"]
