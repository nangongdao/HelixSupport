from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ConversationStatus(StrEnum):
    OPEN = "open"
    WAITING_HUMAN = "waiting_human"
    HUMAN_ACTIVE = "human_active"
    RESOLVED = "resolved"


class AgentName(StrEnum):
    POLICY = "policy"
    TRIAGE = "triage"
    KNOWLEDGE = "knowledge"
    ORDER = "order"
    ESCALATION = "escalation"
    QUALITY = "quality"


@dataclass(frozen=True)
class RiskAssessment:
    categories: list[str] = field(default_factory=list)
    requires_human: bool = False
    handoff_reason: str | None = None
    redacted_excerpt: str = ""


@dataclass(frozen=True)
class TriageDecision:
    route: AgentName
    intent: str
    confidence: float
    urgency: str = "normal"
    reasons: list[str] = field(default_factory=list)
    mode: str = "rules"


@dataclass(frozen=True)
class AgentResult:
    agent: AgentName
    content: str
    confidence: float
    citations: list[dict[str, str]] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    requires_human: bool = False
    handoff_reason: str | None = None


@dataclass(frozen=True)
class QualityAssessment:
    approved: bool
    issues: list[str] = field(default_factory=list)
