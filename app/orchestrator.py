from __future__ import annotations

import logging
from collections.abc import Callable
from time import monotonic
from typing import Any

from app.agents import (
    EscalationAgent,
    KnowledgeAgent,
    OrderAgent,
    PolicyAgent,
    QualityAgent,
    TriageAgent,
)
from app.config import Settings
from app.connectors import (
    SandboxCRMConnector,
    SandboxKnowledgeConnector,
    SandboxOrderConnector,
)
from app.conversation_lifecycle import ConversationLifecycleMixin
from app.connectors_runtime import (
    CircuitBreakerRegistry,
    ResilientCRMConnector,
    ResilientKnowledgeConnector,
    ResilientOrderConnector,
)
from app.database import Database

# The lifecycle exceptions are domain errors; they live here next to
# ConversationStatus and are re-exported for every importer (middleware,
# routers, tests) whose import surface is unchanged.
from app.domain import (
    ConversationStatus,
    IdempotencyConflictError,  # noqa: F401  (re-export)
    InvalidTransitionError,  # noqa: F401  (re-export)
    TurnInProgressError,  # noqa: F401  (re-export)
)
from app.language import LanguageService
from app.model_provider import ModelProvider
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.queue import SQLiteTaskQueue, TaskQueue
from app.summaries import SummaryService
from app.telemetry import metrics as telemetry_metrics
from app.tools import ToolGateway
from app.turn_execution import TurnExecutionContext, TurnExecutionStage
from app.turn_persist import TurnPersistInputs, TurnPersistStage
from app.turn_policy import TurnPolicyContext, TurnPolicyStage
from app.webhooks import (
    WebhookService,
)

logger = logging.getLogger("helix")


class ConversationOrchestrator(ConversationLifecycleMixin):
    def __init__(
        self,
        database: Database,
        settings: Settings,
        model_provider: ModelProvider | None = None,
        queue: TaskQueue | None = None,
        webhook_service: WebhookService | None = None,
        cost_attribution: Any | None = None,
        capability_secret: str = "",
        governance_service: Any | None = None,
    ) -> None:
        self.database = database
        self.settings = settings
        self.webhook_service = webhook_service
        self.cost_attribution = cost_attribution
        # Phase 20.1/20.2: wrap the sandbox connectors in the resilient guard
        # (circuit breaker + retry, per-tenant isolation). The sandbox never
        # raises transient errors, so this is behaviour-preserving; a real
        # connector that does will engage the guard and degrade cleanly.
        self.breaker_registry = CircuitBreakerRegistry()
        order_connector = ResilientOrderConnector(
            SandboxOrderConnector(database), self.breaker_registry
        )
        knowledge_connector = ResilientKnowledgeConnector(
            SandboxKnowledgeConnector(database), self.breaker_registry
        )
        crm_connector = ResilientCRMConnector(SandboxCRMConnector(database), self.breaker_registry)
        # ROADMAP 2.5.0: the governance plane reaches the gateway here —
        # capability tokens verify against CAPABILITY_SECRET and high-risk
        # tools consult the AI governance registry through governance_service.
        self.tools = ToolGateway(
            database,
            order_connector=order_connector,
            knowledge_connector=knowledge_connector,
            crm_connector=crm_connector,
            capability_secret=capability_secret,
            governance_service=governance_service,
        )
        self.policy = PolicyAgent()
        self.triage = TriageAgent(model_provider, cost_attribution=cost_attribution)
        self.knowledge = KnowledgeAgent(database, knowledge_connector=knowledge_connector)
        self.order = OrderAgent(self.tools)
        self.escalation = EscalationAgent()
        self.quality = QualityAgent()
        self.queue = queue or SQLiteTaskQueue(database)
        self.prompt_registry = PromptRegistry(database)
        self.quality_service = QualityService(database)
        # Backlog: session intelligent summaries — model-first, deterministic
        # projection fallback so the lifecycle path never depends on the model.
        self.summaries = SummaryService(database, model_provider, cost_attribution=cost_attribution)
        # Backlog: multi-language customer service — script-based detection
        # with a model-first detector, and best-effort reply translation.
        self.languages = LanguageService(
            model_provider, settings.service_language, cost_attribution=cost_attribution
        )
        # Phase 41.6 (ARC-001): the turn pipeline is composed of three deep
        # stages (policy/triage, specialist execution, persistence), each with
        # a typed context/result surface so the orchestrator stays a thin
        # coordinator between durable state and the public API. The stages
        # read services through this orchestrator at call time, so swapping
        # ``languages``/``tools``/``order`` after construction (the legacy
        # test contract) is honoured by the next turn.
        self.turn_policy = TurnPolicyStage(self)
        self.turn_execution = TurnExecutionStage(self)
        self.turn_persist = TurnPersistStage(self)

    def queue_customer_message(
        self,
        tenant_id: str,
        conversation_id: str,
        content: str,
        actor_id: str,
        idempotency_key: str,
        max_attempts: int,
        channel_message_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be blank")
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.RESOLVED:
            raise InvalidTransitionError("Resolved conversations must be reopened first")
        if channel_message_id is None:
            job, replayed = self.queue.enqueue(
                tenant_id,
                conversation_id,
                idempotency_key,
                actor_id,
                content,
                max_attempts,
            )
        else:
            job, replayed = self.queue.enqueue(
                tenant_id,
                conversation_id,
                idempotency_key,
                actor_id,
                content,
                max_attempts,
                channel_message_id,
            )
        if replayed and job["content"] != content:
            raise IdempotencyConflictError(
                "Idempotency-Key was already used with a different message"
            )
        if (
            replayed
            and channel_message_id is not None
            and job.get("channel_message_id") != channel_message_id
        ):
            raise IdempotencyConflictError(
                "Idempotency-Key was already used with a different channel message"
            )
        if not replayed:
            self.database.audit(
                tenant_id,
                conversation_id,
                actor_id,
                "turn_job.queued",
                {"job_id": job["id"], "max_attempts": max_attempts},
            )
        return job, replayed

    def retry_turn_job(
        self,
        tenant_id: str,
        job_id: str,
        actor_id: str,
    ) -> dict[str, Any]:
        current = self.database.get_turn_job(tenant_id, job_id)
        if not current:
            raise LookupError("Turn job not found")
        if current["status"] != "failed":
            raise InvalidTransitionError("Only failed turn jobs can be retried")
        job = self.queue.retry(tenant_id, job_id)
        if not job:
            raise InvalidTransitionError("Turn job state changed before retry")
        self.database.audit(
            tenant_id,
            job["conversation_id"],
            actor_id,
            "turn_job.retried",
            {"job_id": job_id},
        )
        return job

    def handle_customer_message(
        self,
        tenant_id: str,
        conversation_id: str,
        content: str,
        actor_id: str,
        idempotency_key: str,
        channel_message_id: str | None = None,
        chunk_sink: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be blank")
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.RESOLVED:
            raise InvalidTransitionError("Resolved conversations must be reopened first")

        # Phase 23.2: channel-level idempotency. A channel message id already
        # stored for this conversation is a replay of the same channel message;
        # return the recorded turn instead of processing a duplicate.
        if channel_message_id:
            existing = self.database.get_message_by_channel_id(
                tenant_id, conversation_id, channel_message_id
            )
            if existing is not None:
                cached = self.database.get_turn_by_message_id(
                    tenant_id, conversation_id, existing["id"]
                )
                if cached is not None:
                    cached["idempotent_replay"] = True
                    return cached

        claim, cached = self.database.claim_turn(
            tenant_id,
            conversation_id,
            idempotency_key,
            self.settings.idempotency_processing_timeout_seconds,
        )
        if claim == "completed" and cached is not None:
            cached["idempotent_replay"] = True
            return cached
        if claim == "processing":
            raise TurnInProgressError("A request with this idempotency key is still processing")

        try:
            response = self._process_customer_message(
                tenant_id,
                conversation,
                content,
                actor_id,
                idempotency_key,
                channel_message_id=channel_message_id,
                chunk_sink=chunk_sink,
            )
            response["idempotent_replay"] = False
            self.database.complete_turn(tenant_id, conversation_id, idempotency_key, response)
            return response
        except Exception as exc:
            self.database.fail_turn(tenant_id, conversation_id, idempotency_key, type(exc).__name__)
            raise

    def _process_customer_message(
        self,
        tenant_id: str,
        conversation: dict[str, Any],
        content: str,
        actor_id: str,
        turn_id: str,
        channel_message_id: str | None = None,
        chunk_sink: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        turn_started_monotonic = monotonic()
        conversation_id = conversation["id"]
        # Phase 41.6 (ARC-001): the turn is composed as three deep stages.
        # 1) Policy/triage: language detect + message persist + audit, policy
        #    inspection, budget/model guards, triage decision.
        policy = self.turn_policy.ingest(
            TurnPolicyContext(
                tenant_id=tenant_id,
                conversation=conversation,
                content=content,
                actor_id=actor_id,
                turn_id=turn_id,
            ),
            channel_message_id,
        )
        self._observe_turn_segment("intake", turn_started_monotonic)
        if policy.suppressed:
            current = self.database.get_conversation(tenant_id, conversation_id) or conversation
            return {
                "customer_message": policy.customer_message,
                "assistant_message": None,
                "conversation": current,
            }
        # Segment timing accumulated by the policy stage feeds the same
        # histogram the monolithic method used (claim→intake→policy→triage).
        for segment, elapsed_ms in policy.segment_elapsed.items():
            self._observe_turn_segment_ms(segment, elapsed_ms)
        assert policy.decision is not None and policy.risk is not None
        # 2) Specialist execution routed by the decision, with the quality
        #    gate; content_risk rechecks retrieved text (indirect injection).
        execution = self.turn_execution.execute(
            TurnExecutionContext(
                tenant_id=tenant_id,
                conversation=conversation,
                content=content,
                decision=policy.decision,
                policy_reason=policy.risk.handoff_reason,
                language=policy.language,
            )
        )
        self._observe_turn_segment_ms("specialist", execution.segment_elapsed)
        # 3) Persistence/finalization: routing state (with SLA), assistant
        #    message (with translation), quality aggregate, telemetry, webhook.
        persist = self.turn_persist.finalize(
            TurnPersistInputs(
                tenant_id=tenant_id,
                conversation=conversation,
                customer_message=policy.customer_message,
                decision=policy.decision,
                result=execution.result,
                quality=execution.quality,
                prompt_version=policy.prompt_version,
                prompt_channel=policy.prompt_channel,
                content_risk=execution.content_risk,
                policy_categories=policy.risk.categories,
                language=policy.language,
                detected_language=policy.detected_language,
                customer_content=content,
                assistant_content=execution.result.content,
                turn_id=turn_id,
                turn_started_monotonic=turn_started_monotonic,
                budget_exceeded=policy.budget_exceeded,
            ),
            chunk_sink,
        )
        self._observe_turn_segment_ms("persist", persist.elapsed)
        if not persist.state_updated:
            current = self.database.get_conversation(tenant_id, conversation_id) or conversation
            return {
                "customer_message": policy.customer_message,
                "assistant_message": None,
                "conversation": current,
            }
        return {
            "customer_message": policy.customer_message,
            "assistant_message": persist.assistant_message,
            "conversation": self.database.get_conversation(tenant_id, conversation_id),
        }

    def _observe_turn_segment(self, segment: str, started: float) -> None:
        """Record one stage of the turn-processing chain (ROADMAP 18.2a).

        Segments: claim → intake → policy → triage → specialist
        (retrieval+generate) → persist. Each is observed into the
        ``turn.segment_ms`` histogram with its segment tag so `/api/system/
        metrics` exposes P50/P95 per stage and pinpoints the real bottleneck.
        """
        telemetry_metrics.observe(
            "turn.segment_ms",
            max(0, int((monotonic() - started) * 1000)),
            segment=segment,
        )

    def _observe_turn_segment_ms(self, segment: str, elapsed_ms: float) -> None:
        """Record a stage duration already measured by a deep-module stage."""
        telemetry_metrics.observe(
            "turn.segment_ms",
            max(0, int(elapsed_ms)),
            segment=segment,
        )
