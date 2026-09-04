from __future__ import annotations

import logging
import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import uuid4

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
from app.connectors_runtime import (
    CircuitBreakerRegistry,
    ResilientCRMConnector,
    ResilientKnowledgeConnector,
    ResilientOrderConnector,
)
from app.database import Database
from app.domain import (
    ConversationStatus,
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
    EVENT_CONVERSATION_RESOLVED,
    WebhookService,
)

logger = logging.getLogger("helix")


class TurnInProgressError(ValueError):
    pass


class InvalidTransitionError(ValueError):
    pass


class IdempotencyConflictError(ValueError):
    pass


class ConversationOrchestrator:
    def __init__(
        self,
        database: Database,
        settings: Settings,
        model_provider: ModelProvider | None = None,
        queue: TaskQueue | None = None,
        webhook_service: WebhookService | None = None,
        cost_attribution: Any | None = None,
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
        self.tools = ToolGateway(
            database,
            order_connector=order_connector,
            knowledge_connector=knowledge_connector,
            crm_connector=crm_connector,
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

    def handoff(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        can_override: bool = False,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.HUMAN_ACTIVE:
            if conversation["assigned_agent"] == actor_id or can_override:
                return conversation
            raise InvalidTransitionError("Conversation is assigned to another operator")
        if (
            conversation.get("claim_active")
            and conversation.get("claimed_by") not in {None, actor_id}
            and not can_override
        ):
            raise InvalidTransitionError("Conversation is claimed by another operator")
        updated = self.database.transition_conversation(
            tenant_id,
            conversation_id,
            [ConversationStatus.OPEN, ConversationStatus.WAITING_HUMAN],
            ConversationStatus.HUMAN_ACTIVE,
            assigned_agent=actor_id,
            clear_claim=True,
        )
        if not updated:
            raise InvalidTransitionError("Conversation cannot be accepted from its current state")
        self.database.audit(tenant_id, conversation_id, actor_id, "conversation.human_accepted", {})
        # Backlog: generate the handover brief ("前情摘要") so the incoming
        # operator sees what happened before they picked it up. Best-effort:
        # the acceptance never waits on or breaks from the model.
        try:
            self.summaries.generate(tenant_id, conversation_id, "context")
        except Exception:
            logger.exception(
                "summary.context_failed",
                extra={"tenant_id": tenant_id, "conversation_id": conversation_id},
            )
        return updated

    def claim(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.RESOLVED:
            raise InvalidTransitionError("Resolved conversations cannot be claimed")
        if (
            conversation.get("claim_active")
            and conversation.get("claimed_by") not in {None, actor_id}
            and not force
        ):
            raise InvalidTransitionError("Conversation is claimed by another operator")
        updated = self.database.claim_conversation(
            tenant_id,
            conversation_id,
            actor_id,
            self.settings.claim_ttl_seconds,
            force=force,
        )
        if not updated:
            raise InvalidTransitionError("Conversation cannot be claimed from its current state")
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "conversation.claimed",
            {
                "claim_expires_at": updated.get("claim_expires_at"),
                "forced": force,
            },
        )
        # Backlog: like handoff, claiming hands the conversation to an operator
        # who benefits from the "前情摘要" brief. Best-effort; a claim never
        # blocks on the model.
        try:
            self.summaries.generate(tenant_id, conversation_id, "context")
        except Exception:
            logger.exception(
                "summary.context_failed",
                extra={"tenant_id": tenant_id, "conversation_id": conversation_id},
            )
        return updated

    def release_claim(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if (
            conversation.get("claim_active")
            and conversation.get("claimed_by") not in {None, actor_id}
            and not force
        ):
            raise InvalidTransitionError("Conversation is claimed by another operator")
        updated = self.database.release_conversation_claim(
            tenant_id,
            conversation_id,
            actor_id,
            force=force,
        )
        if not updated:
            raise InvalidTransitionError("Conversation claim cannot be released")
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "conversation.claim_released",
            {"forced": force},
        )
        return updated

    def assign(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        assignee_id: str,
        *,
        force: bool = False,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.RESOLVED:
            raise InvalidTransitionError("Resolved conversations cannot be assigned")
        if (
            conversation.get("claim_active")
            and conversation.get("claimed_by") not in {None, actor_id, assignee_id}
            and not force
        ):
            raise InvalidTransitionError("Conversation is claimed by another operator")
        updated = self.database.assign_conversation(
            tenant_id,
            conversation_id,
            assignee_id,
            self.settings.claim_ttl_seconds,
            force=force,
            actor_id=actor_id,
        )
        if not updated:
            raise InvalidTransitionError("Conversation cannot be assigned from its current state")
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "conversation.assigned",
            {"assignee": assignee_id, "forced": force},
        )
        return updated

    def operator_reply(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        content: str,
        can_override: bool = False,
        attachment_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Message content cannot be blank")
        conversation = self.handoff(tenant_id, conversation_id, actor_id, can_override=can_override)
        if conversation.get("assigned_agent") != actor_id and not can_override:
            raise InvalidTransitionError("Conversation is assigned to another operator")
        metadata: dict[str, Any] = {"source": "operator_workspace"}
        attachment_ids = attachment_ids or []
        if attachment_ids:
            # Backlog (语音/富媒体消息): ids are validated against this
            # tenant+conversation by the router; the backfill onto the
            # attachment rows happens there too, keeping the orchestrator
            # decoupled from the attachment service.
            metadata["attachment_ids"] = attachment_ids
        message = self.database.add_message(
            tenant_id,
            conversation_id,
            "operator",
            actor_id,
            content,
            metadata,
        )
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "operator.replied",
            {"message_id": message["id"], "attachment_count": len(attachment_ids)},
        )
        return message

    def add_internal_note(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        content: str,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        content = content.strip()
        if not content:
            raise ValueError("Note content cannot be blank")
        if not self.database.get_conversation(tenant_id, conversation_id):
            raise LookupError("Conversation not found")
        if reply_to:
            target = self.database.get_message(tenant_id, conversation_id, reply_to)
            if not target:
                raise LookupError("Reply target not found")
            if target["role"] not in ("internal_note", "note"):
                raise InvalidTransitionError("Reply target must be an internal note")
        message = self.database.add_message(
            tenant_id,
            conversation_id,
            "internal_note",
            actor_id,
            content,
            {"visibility": "internal", "source": "operator_workspace"},
            reply_to=reply_to,
        )
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "conversation.note_added",
            {"message_id": message["id"], "reply_to": reply_to},
        )
        mentioned = self._mention_actors(content, actor_id)
        if mentioned:
            count = self.database.record_mentions(
                tenant_id,
                conversation_id,
                message["id"],
                actor_id,
                mentioned,
            )
            if count:
                self.database.audit(
                    tenant_id,
                    conversation_id,
                    actor_id,
                    "conversation.mentions_recorded",
                    {"message_id": message["id"], "mentions": count, "actors": mentioned},
                )
        return message

    @staticmethod
    def _mention_actors(content: str, author: str) -> list[str]:
        """Extract ``@actor`` tokens (excluding the author) for mention rows.

        A mention is a leading ``@`` followed by an actor id matching the
        assignee pattern. Tokens that do not look like an actor id (e.g. an
        email local part without tenant context) are skipped so junk rows never
        land in any colleague's inbox; self-mentions are skipped too.
        """
        seen: list[str] = []
        for match in re.finditer(r"@([A-Za-z0-9._:@/-]+)", content):
            token = match.group(1)
            if not re.fullmatch(r"[A-Za-z0-9._:@/-]{2,64}", token):
                continue
            if token == author or token in seen:
                continue
            seen.append(token)
        return seen

    def update_priority(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        priority: str,
    ) -> dict[str, Any]:
        current = self.database.get_conversation(tenant_id, conversation_id)
        if not current:
            raise LookupError("Conversation not found")
        if current["priority"] == priority:
            return current
        row = self.database.update_priority(
            tenant_id,
            conversation_id,
            priority,
            self.database.resolve_sla_policy(
                tenant_id,
                priority,
                current.get("channel") or "web",
                self.settings.high_sla_minutes
                if priority == "high"
                else self.settings.normal_sla_minutes,
            ),
        )
        if not row:
            raise LookupError("Conversation not found")
        self.database.audit(
            tenant_id,
            conversation_id,
            actor_id,
            "conversation.priority_changed",
            {"from": current["priority"], "to": priority},
        )
        return row

    def replace_labels(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        labels: list[str],
    ) -> dict[str, Any]:
        current = self.database.get_conversation(tenant_id, conversation_id)
        if not current:
            raise LookupError("Conversation not found")
        previous = list(current.get("labels") or [])
        row, changed = self.database.replace_conversation_labels(
            tenant_id,
            conversation_id,
            labels,
            actor_id,
        )
        if not row:
            raise LookupError("Conversation not found")
        if changed:
            self.database.audit(
                tenant_id,
                conversation_id,
                actor_id,
                "conversation.labels_changed",
                {"from": previous, "to": labels},
            )
        return row

    def bulk_action(
        self,
        tenant_id: str,
        actor_id: str,
        conversation_ids: list[str],
        action: str,
        *,
        priority: str | None = None,
        labels: list[str] | None = None,
        force: bool = False,
    ) -> dict[str, int]:
        if action == "set_priority":
            if priority is None:
                raise ValueError("priority is required")
            changed, matched = self.database.bulk_update_priority(
                tenant_id,
                conversation_ids,
                priority,
                self.settings.high_sla_minutes if priority == "high" else None,
            )
            events = [
                (
                    item["id"],
                    "conversation.priority_changed",
                    {"from": item["from"], "to": priority, "bulk": True},
                )
                for item in changed
            ]
            updated = len(changed)
        elif action in {"add_labels", "remove_labels"}:
            if not labels:
                raise ValueError("labels are required")
            add = action == "add_labels"
            changed_ids, matched = self.database.bulk_modify_labels(
                tenant_id,
                conversation_ids,
                labels,
                actor_id,
                add=add,
            )
            events = [
                (
                    conversation_id,
                    "conversation.labels_changed",
                    {
                        "operation": "add" if add else "remove",
                        "labels": labels,
                        "bulk": True,
                    },
                )
                for conversation_id in changed_ids
            ]
            updated = len(changed_ids)
        elif action == "claim":
            changed_ids, matched = self.database.bulk_claim_conversations(
                tenant_id,
                conversation_ids,
                actor_id,
                self.settings.claim_ttl_seconds,
                force=force,
            )
            events = [
                (
                    conversation_id,
                    "conversation.claimed",
                    {"bulk": True, "forced": force},
                )
                for conversation_id in changed_ids
            ]
            updated = len(changed_ids)
        elif action == "release":
            changed_ids, matched = self.database.bulk_release_claims(
                tenant_id,
                conversation_ids,
                actor_id,
                force=force,
            )
            events = [
                (
                    conversation_id,
                    "conversation.claim_released",
                    {"bulk": True, "forced": force},
                )
                for conversation_id in changed_ids
            ]
            updated = len(changed_ids)
        else:
            raise ValueError(f"Unsupported bulk action: {action}")
        self.database.audit_many(tenant_id, actor_id, events)
        return {
            "requested": len(conversation_ids),
            "matched": matched,
            "updated": updated,
            "unchanged": matched - updated,
        }

    def resolve(
        self,
        tenant_id: str,
        conversation_id: str,
        actor_id: str,
        can_override: bool = False,
    ) -> dict[str, Any]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if not conversation:
            raise LookupError("Conversation not found")
        if conversation["status"] == ConversationStatus.RESOLVED:
            return conversation
        if (
            conversation["status"] == ConversationStatus.HUMAN_ACTIVE
            and conversation.get("assigned_agent") not in {None, actor_id}
            and not can_override
        ):
            raise InvalidTransitionError("Conversation is assigned to another operator")
        updated = self.database.transition_conversation(
            tenant_id,
            conversation_id,
            [
                ConversationStatus.OPEN,
                ConversationStatus.WAITING_HUMAN,
                ConversationStatus.HUMAN_ACTIVE,
            ],
            ConversationStatus.RESOLVED,
            assigned_agent=actor_id,
        )
        if not updated:
            raise InvalidTransitionError("Conversation cannot be resolved from its current state")
        self.database.audit(tenant_id, conversation_id, actor_id, "conversation.resolved", {})
        # Backlog: issue a one-time CSAT survey link on resolution so the
        # customer can rate the experience; ratings reflow into feedback. The
        # token is echoed on the response and the resolved webhook so tenants
        # can deliver the link through their own channel.
        survey_url: str | None = None
        try:
            expires = (datetime.now(UTC) + timedelta(days=7)).isoformat(timespec="microseconds")
            token = self.database.create_csat_survey(tenant_id, conversation_id, expires)
            survey_url = self._csat_survey_url(token)
            updated["survey_url"] = survey_url
        except Exception:
            logger.exception("failed to create CSAT survey")
        # Backlog: draft the disposition record ("处置记录草稿") for the
        # resolved conversation; generated once so a later re-open/re-resolve
        # cannot clobber it. Best-effort like the survey link.
        try:
            self.summaries.generate(tenant_id, conversation_id, "disposition")
        except Exception:
            logger.exception(
                "summary.disposition_failed",
                extra={"tenant_id": tenant_id, "conversation_id": conversation_id},
            )
        self._emit_webhook(
            tenant_id,
            EVENT_CONVERSATION_RESOLVED,
            {
                "conversation_id": conversation_id,
                "customer_name": conversation["customer_name"],
                "resolved_by": actor_id,
                "survey_url": survey_url,
            },
        )
        return updated

    def reopen(self, tenant_id: str, conversation_id: str, actor_id: str) -> dict[str, Any]:
        current = self.database.get_conversation(tenant_id, conversation_id)
        if not current:
            raise LookupError("Conversation not found")
        updated = self.database.transition_conversation(
            tenant_id,
            conversation_id,
            [ConversationStatus.RESOLVED],
            ConversationStatus.OPEN,
            clear_handoff=True,
            clear_assignment=True,
            sla_minutes=self.database.resolve_sla_policy(
                tenant_id,
                current.get("priority") or "normal",
                current.get("channel") or "web",
                self.settings.normal_sla_minutes,
            ),
        )
        if not updated:
            raise InvalidTransitionError("Only resolved conversations can be reopened")
        self.database.audit(tenant_id, conversation_id, actor_id, "conversation.reopened", {})
        return updated

    def _csat_survey_url(self, token: str) -> str:
        """Build the customer survey URL (absolute when configured fully)."""
        base = self.settings.csat_base_url or ""
        return f"{base}/api/csat/{token}"

    def _emit_webhook(
        self,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
        event_id: str | None = None,
    ) -> None:
        """Enqueue an outbound webhook event (Phase 20.5), never breaking the
        conversation path: subscription failures are logged and ignored."""
        if self.webhook_service is None:
            return
        try:
            self.webhook_service.emit_event(
                tenant_id, event_type, payload, event_id or str(uuid4())
            )
        except Exception:
            logger.exception(
                "webhook.emit_failed",
                extra={"event_type": event_type, "tenant_id": tenant_id},
            )
