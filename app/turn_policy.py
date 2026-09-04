"""Turn policy/triage stage of the conversation pipeline (Phase 41.6 / ARC-001).

Extracted from ``app/orchestrator.py``: the intake and decision half of a
turn -- language detection, customer-message persist, policy inspection,
budget/model allow-list guards, and the triage decision.  The public surface
is one typed step (``ingest``) returning a typed result, so the orchestrator
composes the stage without reaching into this module's internals.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from time import monotonic
from typing import Any

from app.database import utc_now
from app.domain import AgentName, ConversationStatus, RiskAssessment, TriageDecision
from app.model_provider import ModelPolicyDecision, check_model_policy
from app.prompts import PromptVersion
from app.turn_services import TurnServices

logger = logging.getLogger("helix")


@dataclass(frozen=True)
class TurnPolicyContext:
    """Immutable inputs the policy stage needs (ARC-001 typed context)."""

    tenant_id: str
    conversation: dict[str, Any]
    content: str
    actor_id: str
    turn_id: str


@dataclass(frozen=True)
class TurnPolicyResult:
    """Outcome of the intake + policy + triage stage for one turn.

    ``suppressed`` is True when automation must stand down (the conversation
    is in a human state); ``decision``/``risk`` are then None and the caller
    returns without routing. ``allow_model`` is False when the tenant budget
    is exceeded or the prompt version's model is outside the allow-list.
    ``segment_elapsed`` feeds the ``turn.segment_ms`` histogram with the
    intake/policy/triage durations measured here.
    """

    suppressed: bool
    customer_message: dict[str, Any]
    decision: TriageDecision | None
    risk: RiskAssessment | None
    language: str | None
    detected_language: str | None
    prompt_version: PromptVersion | None
    prompt_channel: str
    allow_model: bool
    budget_exceeded: bool
    budget_used: int
    budget_limit: int | None
    segment_elapsed: dict[str, float]


class TurnPolicyStage:
    def __init__(self, services: TurnServices) -> None:
        """Bind the live service provider; services are read at call time."""
        self.services = services

    def ingest(
        self, context: TurnPolicyContext, channel_message_id: str | None = None
    ) -> TurnPolicyResult:
        """Intake one customer message and produce the routing decision.

        Mirrors the orchestrator's intake-policy-triage passage: the message
        is durable and audited before any decision, so a suppressed
        automation or a later failure never loses the customer's words.
        """
        started = monotonic()
        tenant_id = context.tenant_id
        conversation_id = context.conversation["id"]
        conversation = context.conversation
        # Backlog (多语言客服): detect the customer message language up front --
        # best-effort and deterministic, so it never blocks or breaks intake.
        detected_language, language_source = self.services.languages.detect(
            context.content, tenant_id=tenant_id
        )
        # The operator's manual override (PATCH /language) wins over
        # auto-detection: it pins both the conversation badge AND the reply
        # translation target.
        language = conversation.get("language") or detected_language
        customer_message = self.services.database.add_message(
            tenant_id,
            conversation_id,
            "customer",
            conversation["customer_name"],
            context.content,
            {
                "source_actor": context.actor_id,
                "language": language,
                "language_source": language_source,
            },
            context.turn_id,
            channel_message_id=channel_message_id,
        )
        if detected_language and conversation.get("language") is None:
            # Backlog (多语言客服): the manual override is sticky -- an
            # auto-detected code is only written while the stored language is
            # null, so the pinned value survives the next customer message
            # until the operator clears it.
            self.services.database.set_conversation_language(
                tenant_id, conversation_id, detected_language
            )
        self.services.database.audit(
            tenant_id,
            conversation_id,
            context.actor_id,
            "customer.message_received",
            {
                "message_id": customer_message["id"],
                "language": language,
                "detected_language": detected_language,
            },
        )
        # ROADMAP 18.2a: intake ends once the customer message is durable and
        # audited (intake = language detect + message insert + audit).
        intake_elapsed = max(0.0, (monotonic() - started) * 1000)
        # H1 (41.6 review): read a fresh snapshot for the suppression check.
        # The context's conversation was fetched at queue/claim time (possibly
        # seconds earlier); an operator handoff in between must suppress this
        # turn instead of routing a specialist that then wastes model/tool
        # calls and writes audit rows the persist stage would contradict.
        current = self.services.database.get_conversation(tenant_id, conversation_id)
        if current is not None:
            conversation = current
        if conversation["status"] in {
            ConversationStatus.WAITING_HUMAN,
            ConversationStatus.HUMAN_ACTIVE,
        }:
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "automation.suppressed",
                {"reason": f"conversation_status:{conversation['status']}"},
            )
            return TurnPolicyResult(
                suppressed=True,
                customer_message=customer_message,
                decision=None,
                risk=None,
                language=language,
                detected_language=detected_language,
                prompt_version=None,
                prompt_channel="default",
                allow_model=False,
                budget_exceeded=False,
                budget_used=0,
                budget_limit=None,
                segment_elapsed={"intake": intake_elapsed},
            )

        prompt_version, prompt_channel = self.services.prompt_registry.resolve_prompt(
            tenant_id,
            "triage_prompt",
            conversation_id,
            self.services.settings.prompt_canary_ratio,
        )
        if prompt_version is not None:
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "prompt_version.resolved",
                {
                    "prompt_version_id": prompt_version.id,
                    "prompt_name": prompt_version.name,
                    "prompt_version": prompt_version.version,
                    "channel": prompt_channel,
                    "model_ref": prompt_version.model_ref,
                },
            )
        budget_exceeded, budget_used, budget_limit = self._check_budget(tenant_id)
        allow_model = not budget_exceeded
        if budget_exceeded:
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "turn.budget_exceeded",
                {"used": budget_used, "limit": budget_limit},
            )
        # Phase 19.4: a prompt version whose model_ref is outside the tenant's
        # allowed-models allow-list must not route through the model path.
        model_ref = prompt_version.model_ref if prompt_version else None
        if allow_model and not self._model_allowed(tenant_id, model_ref):
            allow_model = False
            self.services.database.audit(
                tenant_id,
                conversation_id,
                "orchestrator",
                "turn.model_denied",
                {"model_ref": model_ref},
            )
        # ROADMAP 43.5: the control-plane disable surface may refuse a provider
        # or model outright (or block data egress to its region) even when the
        # 19.4 allow-list passed; the refusal reason is audited for drift.
        if allow_model:
            decision = self._governance_decision(tenant_id, model_ref)
            if not decision.allowed:
                allow_model = False
                self.services.database.audit(
                    tenant_id,
                    conversation_id,
                    "orchestrator",
                    "turn.model_denied",
                    {"model_ref": model_ref, "reason": decision.reason},
                )
        # ROADMAP 18.2a: policy assessment segment starts here.
        policy_started = monotonic()
        risk = self.services.policy.inspect(context.content)
        self.services.database.audit(
            tenant_id,
            conversation_id,
            AgentName.POLICY,
            "policy.assessed",
            {
                "categories": risk.categories,
                "requires_human": risk.requires_human,
                "redacted_excerpt": risk.redacted_excerpt,
            },
        )
        # ROADMAP 18.2a: policy segment done (inspect + audit + policy-route
        # decision). Triage segment starts now.
        policy_elapsed = max(0.0, (monotonic() - policy_started) * 1000)
        triage_started = monotonic()
        if risk.requires_human:
            decision = TriageDecision(
                route=AgentName.ESCALATION,
                intent="policy_risk",
                confidence=1.0,
                urgency="high",
                reasons=[risk.handoff_reason or "Policy risk"],
                mode="policy",
            )
        else:
            decision = self.services.triage.decide(
                context.content,
                prompt=prompt_version,
                allow_model=allow_model,
                tenant_id=tenant_id,
            )
        self.services.database.audit(
            tenant_id,
            conversation_id,
            AgentName.TRIAGE,
            "agent.routed",
            {
                "route": decision.route,
                "intent": decision.intent,
                "confidence": decision.confidence,
                "urgency": decision.urgency,
                "reasons": decision.reasons,
                "mode": decision.mode,
                "prompt_version_id": prompt_version.id if prompt_version else None,
                "prompt_channel": prompt_channel,
            },
        )
        # ROADMAP 18.2a: triage done (decide + agent.routed audit).
        triage_elapsed = max(0.0, (monotonic() - triage_started) * 1000)
        return TurnPolicyResult(
            suppressed=False,
            customer_message=customer_message,
            decision=decision,
            risk=risk,
            language=language,
            detected_language=detected_language,
            prompt_version=prompt_version,
            prompt_channel=prompt_channel,
            allow_model=allow_model,
            budget_exceeded=budget_exceeded,
            budget_used=budget_used,
            budget_limit=budget_limit,
            segment_elapsed={
                "intake": intake_elapsed,
                "policy": policy_elapsed,
                "triage": triage_elapsed,
            },
        )

    def _check_budget(self, tenant_id: str) -> tuple[bool, int, int | None]:
        """Check the tenant's daily turn budget (Phase 19.4).

        Returns ``(exceeded, used, limit)``. ``limit`` is None when the tenant
        has no budget configured (unlimited), in which case ``exceeded`` is
        always False -- the backward-compatible default.
        """
        try:
            policy = self.services.database.get_tenant_model_policy(tenant_id)
        except LookupError:
            return False, 0, None
        limit = policy["daily_turn_budget"]
        if limit is None:
            return False, 0, None
        used = self.services.database.get_tenant_daily_usage(tenant_id, utc_now()[:10])
        return used >= limit, used, limit

    def _model_allowed(self, tenant_id: str, model_ref: str | None) -> bool:
        """Enforce the tenant's allowed-models allow-list (Phase 19.4).

        ``allowed_models=None`` means unrestricted. A prompt version with a
        ``model_ref`` outside the allow-list is not permitted for this tenant;
        the turn then falls back to the deterministic routing path.
        """
        try:
            policy = self.services.database.get_tenant_model_policy(tenant_id)
        except LookupError:
            return True
        allowed = policy["allowed_models"]
        if not allowed or not model_ref:
            return True
        return model_ref in allowed

    def _governance_decision(self, tenant_id: str, model_ref: str | None) -> ModelPolicyDecision:
        """Evaluate the 43.5 control-plane disable surface for one model ref.

        Reads ``model_policy`` (disabled providers/models, data-egress flag)
        from the signed control-plane policy when one is attached; without a
        control plane, an unreachable plane, or no snapshot the check passes —
        the pre-43.5 fail-open default is preserved so existing deployments
        are unaffected.
        """
        plane = getattr(self.services, "data_plane_config", None)
        if plane is None:
            return ModelPolicyDecision(True, "")
        try:
            policy = plane.effective_policy(tenant_id).model_policy or {}
        except Exception:
            # PolicyUnavailableError / lookup failure: keep serving with the
            # 19.4 surface only rather than blocking every turn.
            logger.info("model governance policy unavailable; skipping 43.5 gate")
            return ModelPolicyDecision(True, "")
        tenant_region = "local"
        try:
            quota = self.services.database.get_tenant_quota(tenant_id)
            tenant_region = str(quota.get("region") or "local")
        except LookupError:
            pass
        return check_model_policy(policy, model_ref, tenant_region=tenant_region)


__all__ = ["TurnPolicyContext", "TurnPolicyResult", "TurnPolicyStage"]
