"""AI-assisted operator copilot (backlog: AI 辅助坐席).

Keeps the product useful after a conversation is handed to a human operator:

- ``suggest_reply`` drafts 1-3 candidate replies for the composer input;
- ``recommend_knowledge`` surfaces knowledge articles relevant to the latest
  customer message;
- ``rewrite_tone`` rewrites the operator's draft in a chosen tone.

Generation is model-first: when a ``ModelProvider`` is configured its output
is preferred, and any failure (provider error, malformed JSON, empty result)
falls back to a deterministic path — canned responses for suggestions, the
language-aware knowledge search for recommendations, the original text for
rewrites — so the operator workflow never waits on or breaks from the model.
Internal notes never enter the suggestion/recommendation context.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.cost_attribution import InferenceContext, record_model_response
from app.model_provider import ModelProvider

logger = logging.getLogger("helix")

SUGGEST_SYSTEM_PROMPT = (
    "You are a customer support coach. Based on the conversation transcript, "
    'return ONLY a JSON object with one key "suggestions" whose value is an '
    "array of 1-3 concise customer-facing reply drafts (3-6 sentences each) "
    "in the same language as the customer's messages. Keep names, order "
    "numbers, and figures accurate; do not invent facts not in the "
    "transcript. Never include internal notes."
)

REWRITE_SYSTEM_PROMPT = (
    "You are a customer support editor. Return ONLY a JSON object with one "
    'key "rewritten" whose value is the operator draft rewritten in the '
    "requested tone. Keep every fact, name, and figure exactly as written."
)

_SUGGEST_USER_PROMPT = (
    "Conversation metadata:\n{metadata}\n\nTranscript (newest last):\n{transcript}"
)

_REWRITE_USER_PROMPT = "Tone: {tone}\n\nDraft:\n{text}"

TONES = ("friendly", "concise", "professional")

# Maximum transcript characters sent to the model (keeps the prompt bounded).
_MAX_TRANSCRIPT_CHARS = 6000

_TONE_LABELS = {"friendly": "亲切", "concise": "简洁", "professional": "专业"}


class CopilotService:
    """Operator assistant: suggestions, knowledge recommendations, rewrites."""

    def __init__(
        self,
        database: Any,
        model_provider: ModelProvider | None = None,
        language_service: Any | None = None,
        cost_attribution: Any = None,
    ) -> None:
        self.database = database
        self.model_provider = model_provider
        # The orchestrator's LanguageService (detection + translation); when
        # absent, suggestions are drafted in the default language.
        self.language_service = language_service
        self.cost_attribution = cost_attribution

    # ------------------------------------------------------------- suggestions

    def suggest_reply(
        self,
        tenant_id: str,
        conversation_id: str,
        draft: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return candidate reply drafts (model-first, canned fallback)."""
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        limit = max(1, min(limit, 3))
        messages = self.database.list_messages(tenant_id, conversation_id, limit=100)
        language = conversation.get("language")
        if self.model_provider is not None:
            try:
                suggestions = self._model_suggestions(
                    conversation, messages, draft, language, tenant_id
                )
                if suggestions:
                    return [{"content": s, "source": "model"} for s in suggestions[:limit]]
            except Exception:
                logger.debug(
                    "copilot.suggest_failed",
                    extra={"tenant_id": tenant_id, "conversation_id": conversation_id},
                )
        # Deterministic fallback: the tenant's most-used canned responses,
        # filtered by the current draft when one exists.
        canned = self.database.list_canned_responses(tenant_id, search=draft, limit=limit)
        if not canned and not draft:
            canned = self.database.list_canned_responses(tenant_id, limit=limit)
        return [{"content": item["body"], "source": "rule"} for item in canned[:limit]]

    def _model_suggestions(
        self,
        conversation: dict[str, Any],
        messages: list[dict[str, Any]],
        draft: str | None,
        language: str | None,
        tenant_id: str,
    ) -> list[str]:
        assert self.model_provider is not None
        transcript = _truncate(self._render_transcript(messages), _MAX_TRANSCRIPT_CHARS)
        user_prompt = _SUGGEST_USER_PROMPT.format(
            metadata=json.dumps(self._metadata(conversation), ensure_ascii=False),
            transcript=transcript,
        )
        if draft and draft.strip():
            user_prompt += f"\n\nOperator draft to improve or extend:\n{draft.strip()}"
        if language and language != "zh":
            user_prompt += f"\n\nReply in the customer's language: {language}."
        response = self.model_provider.complete(SUGGEST_SYSTEM_PROMPT, user_prompt)
        record_model_response(
            self.cost_attribution,
            tenant_id,
            response,
            InferenceContext(agent="copilot_suggest"),
        )
        payload = json.loads(response.content)
        raw = payload["suggestions"]
        suggestions = [s for s in raw if isinstance(s, str) and s.strip()]
        if not suggestions:
            raise ValueError("Model suggestions must be non-empty strings")
        return suggestions

    # --------------------------------------------------------- recommendations

    def recommend_knowledge(
        self,
        tenant_id: str,
        conversation_id: str,
        query: str | None = None,
        limit: int = 3,
    ) -> list[dict[str, Any]]:
        """Return knowledge articles relevant to the conversation."""
        conversation = self.database.get_conversation(tenant_id, conversation_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        limit = max(1, min(limit, 3))
        language = conversation.get("language")
        search_query = query or self._latest_customer_message(tenant_id, conversation_id) or ""
        if not search_query.strip():
            return []
        articles = self.database.search_knowledge(
            tenant_id, search_query, limit=limit, language=language
        )
        return [
            {
                "id": item["id"],
                "title": item["title"],
                "category": item.get("category"),
                "content": item["content"],
                "language": item.get("language"),
                "retrieval_score": item.get("retrieval_score"),
                "matched_terms": item.get("matched_terms", []),
                "source": "rule",
            }
            for item in articles
        ]

    def _latest_customer_message(self, tenant_id: str, conversation_id: str) -> str | None:
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT content FROM messages
                WHERE tenant_id = ? AND conversation_id = ? AND role = 'customer'
                ORDER BY seq DESC, created_at DESC LIMIT 1""",
                (tenant_id, conversation_id),
            ).fetchone()
        return str(row["content"]) if row else None

    # ------------------------------------------------------------------ rewrite

    def rewrite_tone(self, text: str, tone: str, tenant_id: str | None = None) -> dict[str, Any]:
        """Rewrite ``text`` in ``tone``; any failure returns the original."""
        text = text.strip()
        if not text:
            return {"rewritten": text, "source": "rule", "tone": tone}
        if self.model_provider is not None:
            try:
                response = self.model_provider.complete(
                    REWRITE_SYSTEM_PROMPT,
                    _REWRITE_USER_PROMPT.format(tone=tone, text=text[:6000]),
                )
                record_model_response(
                    self.cost_attribution,
                    tenant_id,
                    response,
                    InferenceContext(agent="copilot_rewrite"),
                )
                payload = json.loads(response.content)
                rewritten = payload["rewritten"]
                if isinstance(rewritten, str) and rewritten.strip():
                    if rewritten.strip() != text:
                        return {"rewritten": rewritten.strip(), "source": "model", "tone": tone}
                    return {"rewritten": text, "source": "rule", "tone": tone}
            except Exception:
                logger.debug("copilot.rewrite_failed", extra={"tone": tone})
        return {"rewritten": text, "source": "rule", "tone": tone}

    # ------------------------------------------------------------- transcript

    def _render_transcript(self, messages: list[dict[str, Any]]) -> str:
        lines: list[str] = []
        for message in messages[-100:]:
            role = str(message.get("role", ""))
            if role in {"note", "internal_note"}:
                continue  # internal notes never reach the model
            content = str(message.get("content", ""))
            lines.append(f"{role}: {content}")
        return "\n".join(lines)

    def _metadata(self, conversation: dict[str, Any]) -> dict[str, Any]:
        return {
            "customer_name": conversation.get("customer_name"),
            "channel": conversation.get("channel"),
            "priority": conversation.get("priority"),
            "intent": conversation.get("intent"),
            "status": conversation.get("status"),
            "language": conversation.get("language"),
        }


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "…"


def tone_label(tone: str) -> str:
    return _TONE_LABELS.get(tone, tone)
