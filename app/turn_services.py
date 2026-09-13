"""Structural service contract for the turn stages (Phase 41.6 / ARC-001).

``ConversationOrchestrator`` is the composition root and *is* the provider:
each stage receives it as a ``TurnServices`` and reads services at call time,
so a caller may swap ``orchestrator.languages`` / ``orchestrator.tools`` /
``orchestrator.order`` after construction (the legacy test contract) and the
next turn observes the new instance.  The protocol lives in its own module so
the stages never import the orchestrator -- the ARC-001 import graph stays
acyclic.
"""

from __future__ import annotations

from typing import Protocol

from app.agents import (
    EscalationAgent,
    KnowledgeAgent,
    OrderAgent,
    PolicyAgent,
    QualityAgent,
    TriageAgent,
)
from app.config import Settings
from app.database import Database
from app.language import LanguageService
from app.model_gateway import ModelCallGate
from app.prompts import PromptRegistry
from app.quality import QualityService
from app.tools import ToolGateway
from app.webhooks import WebhookService


class TurnServices(Protocol):
    """The subset of orchestrator services the three turn stages consume."""

    database: Database
    settings: Settings
    policy: PolicyAgent
    triage: TriageAgent
    languages: LanguageService
    prompt_registry: PromptRegistry
    tools: ToolGateway
    knowledge: KnowledgeAgent
    order: OrderAgent
    escalation: EscalationAgent
    quality: QualityAgent
    quality_service: QualityService
    webhook_service: WebhookService | None
    # H04/T02: the single governed entry for every model transport. The policy
    # stage reads its budget/allow-list/disable-surface checks from here so the
    # main chain and the auxiliary calls share one implementation.
    model_gate: ModelCallGate


__all__ = ["TurnServices"]
