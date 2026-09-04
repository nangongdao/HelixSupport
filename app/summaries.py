"""Session intelligent summaries (backlog item).

At operator handoff the orchestrator generates a *context* summary ("前情摘
要") so the incoming operator can pick up the conversation quickly; at resolve
it drafts a *disposition* record ("处置记录草稿"). Generation is model-first:
when a ``ModelProvider`` is configured its output is preferred, and any failure
(provider error, malformed JSON, empty result) falls back to the deterministic
summary projection built from the conversation row and its recent messages, so
the lifecycle path never waits on an external model.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.cost_attribution import InferenceContext, record_model_response
from app.model_provider import ModelProvider

logger = logging.getLogger("helix")

CONTEXT_SYSTEM_PROMPT = (
    "You are a customer support supervisor writing a brief handover brief. "
    'Return ONLY a JSON object with one key "summary" whose value is a '
    "concise Chinese summary (3-6 sentences) of the conversation so far for "
    "an incoming operator: who the customer is, what they asked, what has "
    "been done, and what still needs attention. Do not invent facts that are "
    "not in the transcript."
)

DISPOSITION_SYSTEM_PROMPT = (
    "You are a customer support supervisor drafting a closing record. "
    'Return ONLY a JSON object with one key "summary" whose value is a '
    "concise Chinese handling record (3-5 sentences) for a resolved "
    "conversation: the issue, the resolution reached, and any follow-up the "
    "customer may still need. Do not invent facts that are not in the "
    "transcript."
)

_DISPOSITION_PROMPT = (
    "Conversation metadata:\n{metadata}\n\nTranscript (newest last):\n{transcript}"
)

# Maximum transcript characters sent to the model (keeps the prompt bounded).
_MAX_TRANSCRIPT_CHARS = 6000


def _message_label(role: str, author: str) -> str:
    names = {"customer": "客户", "assistant": "客服助手", "operator": "坐席"}
    if role in names:
        return names[role]
    if author and author != role:
        return f"{role}:{author}"
    return role


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


class SummaryService:
    """Generates and persists per-conversation summary kinds (model-first)."""

    def __init__(
        self,
        database: Any,
        model_provider: ModelProvider | None = None,
        cost_attribution: Any = None,
    ) -> None:
        self.database = database
        self.model_provider = model_provider
        self.cost_attribution = cost_attribution

    # ------------------------------------------------------------- generation

    def generate(
        self,
        tenant_id: str,
        conversation_id: str,
        kind: str,
        actor_id: str | None = None,
    ) -> dict[str, Any]:
        """Build (if missing) and store one summary kind; returns the stored row.

        ``kind`` is ``"context"`` or ``"disposition"``. Existing rows are
        preserved (each kind is only generated at its own lifecycle point), so
        re-accepting or recomputing after a reopen never clobbers an operator
        edit or an older disposition.
        """
        existing = self.database.get_conversation_summary(tenant_id, conversation_id, kind)
        if existing:
            return existing
        content, source = self._summarize(tenant_id, conversation_id, kind)
        return self.database.upsert_conversation_summary(
            tenant_id, conversation_id, kind, content, source
        )

    def _summarize(self, tenant_id: str, conversation_id: str, kind: str) -> tuple[str, str]:
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        messages = self.database.list_messages(tenant_id, conversation_id, limit=100)
        if self.model_provider is not None:
            try:
                content, response = self._model_summary(kind, conversation, messages)
                record_model_response(
                    self.cost_attribution,
                    tenant_id,
                    response,
                    InferenceContext(
                        conversation_id=conversation_id,
                        agent="summary",
                    ),
                )
                if content.strip():
                    return content, "model"
            except Exception:
                # Any provider failure (HTTP, malformed JSON, missing key,
                # unexpected type) falls back to the deterministic projection.
                logger.debug(
                    "summary.model_failed",
                    extra={
                        "tenant_id": tenant_id,
                        "conversation_id": conversation_id,
                        "kind": kind,
                    },
                )
        return self._rule_summary(conversation, messages), "rule"

    def _model_summary(
        self, kind: str, conversation: dict[str, Any], messages: list[dict[str, Any]]
    ) -> tuple[str, Any]:
        assert self.model_provider is not None
        system_prompt = CONTEXT_SYSTEM_PROMPT if kind == "context" else DISPOSITION_SYSTEM_PROMPT
        transcript = _truncate(self._render_transcript(messages), _MAX_TRANSCRIPT_CHARS)
        user_prompt = _DISPOSITION_PROMPT.format(
            metadata=json.dumps(self._metadata(conversation), ensure_ascii=False),
            transcript=transcript,
        )
        response = self.model_provider.complete(system_prompt, user_prompt)
        payload = json.loads(response.content)
        content = payload["summary"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Model summary must be a non-empty string")
        return content.strip(), response

    # ----------------------------------------------------------- deterministic

    def _render_transcript(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages[-100:]:
            role = str(message.get("role", ""))
            if role in {"note", "internal_note"}:
                continue  # internal notes never appear in summaries
            content = str(message.get("content", ""))
            lines.append(f"{_message_label(role, str(message.get('author', '')))}: {content}")
        return "\n".join(lines)

    def _metadata(self, conversation: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_name": conversation.get("customer_name"),
            "channel": conversation.get("channel"),
            "priority": conversation.get("priority"),
            "intent": conversation.get("intent"),
            "status": conversation.get("status"),
            "handoff_reason": conversation.get("handoff_reason"),
            "message_count": conversation.get("message_count"),
            "first_response_at": conversation.get("first_response_at"),
            "created_at": conversation.get("created_at"),
        }

    def _rule_summary(self, conversation: dict[str, Any], messages: list[dict[str, Any]]) -> str:
        """Deterministic fallback: the existing summary projection, expanded
        with the conversation metadata and the latest visible exchanges."""
        msg_count = conversation.get("message_count", len(messages))
        header = (
            f"客户 {conversation.get('customer_name') or '(匿名)'}"
            f" · 渠道 {conversation.get('channel') or '未知'}"
            f" · 优先级 {conversation.get('priority') or 'normal'}"
            f" · 意图 {conversation.get('intent') or '未知'}"
            f" · {msg_count} 条消息"
        )
        if conversation.get("handoff_reason"):
            header += f" · 转人工原因: {conversation['handoff_reason']}"
        transcript = self._render_transcript(
            [m for m in messages if m.get("role") not in ("note", "internal_note")]
        )
        if not transcript:
            return header
        return f"{header}\n最近对话:\n{_truncate(transcript, 2000)}"
