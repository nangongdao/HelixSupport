"""Conversation lifecycle transitions (Phase 27.2 home).

Extracted from ``app/orchestrator.py`` when the controller outgrew its
post-Phase-27 size: every operator-facing state transition — handoff,
claim/release, assign, replies, notes, labels/priority, bulk actions,
resolve/reopen, plus the CSAT survey link and webhook emit helpers — is
one domain, composed into :class:`app.orchestrator.ConversationOrchestrator`
as a mixin. Methods are verbatim; they read the orchestrator's collaborators
(``database``/``settings``/``summaries``/``webhook_service``) through
``self`` at call time, so the legacy swap-after-construction test contract
is honoured here too. The lifecycle exceptions live in :mod:`app.domain`
alongside :class:`ConversationStatus`; ``app.orchestrator`` re-exports them
so every importer is unchanged.
"""

# Mixin composition: the attributes below are the orchestrator's own
# collaborators (database/settings/summaries/webhook_service) — the same
# file-level escape the app/db mixins use for cross-mixin attribute access.
# pyright: reportAttributeAccessIssue=false

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from app.domain import ConversationStatus, InvalidTransitionError
from app.webhooks import EVENT_CONVERSATION_RESOLVED

logger = logging.getLogger("helix")


class ConversationLifecycleMixin:
    """Operator-facing conversation state transitions (see module docstring)."""

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
