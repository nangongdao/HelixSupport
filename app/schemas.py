from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.labels import normalize_conversation_labels
from app.language import KNOWN_LANGUAGES


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateConversationRequest(StrictModel):
    customer_name: str = Field(min_length=1, max_length=80)
    customer_ref: str | None = Field(default=None, min_length=2, max_length=80)
    channel: str = Field(default="web", min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")


class DsrCreateRequest(StrictModel):
    """Create a data-subject request (M0 SEC-002).

    ``idempotency_key`` is optional; when supplied, replaying the same key
    returns the original request instead of creating a duplicate.
    """

    customer_ref: str = Field(min_length=2, max_length=80)
    request_type: Literal["deletion", "export"]
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=128)


class WidgetSessionCreateRequest(StrictModel):
    """Create an embeddable Web Chat session (Phase 23.1).

    The tenant is bound by the signed ``X-Widget-Token`` header, not by the
    body. ``customer_name`` is optional for anonymous visitors; when absent a
    generated display name is used.
    """

    customer_name: str | None = Field(default=None, min_length=1, max_length=80)
    channel: str = Field(
        default="web_chat", min_length=1, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$"
    )


class WidgetMessageSendRequest(StrictModel):
    """Send a customer message from the widget (Phase 23.2).

    ``channel_message_id`` is the channel's own message id: replaying the
    same id returns the original turn instead of creating a duplicate.
    """

    content: str = Field(min_length=1, max_length=4000)
    channel_message_id: str | None = Field(default=None, min_length=1, max_length=120)


class ChannelWebhookMessageRequest(StrictModel):
    """Provider-neutral inbound customer message (ROADMAP Phase 23.3)."""

    event: Literal["message.created"] = "message.created"
    message_id: str = Field(min_length=1, max_length=120)
    thread_id: str = Field(min_length=1, max_length=120)
    customer_id: str = Field(min_length=1, max_length=80)
    customer_name: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=4000)


class ChannelWebhookAccepted(StrictModel):
    conversation_id: str
    job_id: str | None = None
    status: Literal["queued", "processing", "completed", "failed"]
    idempotent_replay: bool
    conversation_created: bool


class WidgetSessionOut(StrictModel):
    """A created widget session: the conversation plus a fresh token.

    The token is re-issued per session so the client never sees the shared
    signing secret; it binds the session's conversation and expires.
    """

    conversation: ConversationOut
    widget_token: str


class MessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=4000)


class OperatorMessageRequest(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    # Backlog (语音/富媒体消息): optional ids of stored attachments in the
    # same conversation to attach to this reply.
    attachment_ids: list[str] = Field(default_factory=list, max_length=10)


class InternalNoteRequest(StrictModel):
    content: str = Field(min_length=1, max_length=4000)
    # Backlog (坐席协作): optional id of another internal note in the same
    # conversation that this note replies to (discussion threads).
    reply_to: str | None = Field(default=None, min_length=5, max_length=80)


class ConversationPriorityRequest(StrictModel):
    priority: Literal["normal", "high"]


class ConversationLabelsRequest(StrictModel):
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("labels")
    @classmethod
    def validate_labels(cls, labels: list[str]) -> list[str]:
        return normalize_conversation_labels(labels)


class BulkConversationActionRequest(StrictModel):
    conversation_ids: list[str] = Field(min_length=1, max_length=100)
    action: Literal["set_priority", "add_labels", "remove_labels", "claim", "release"]
    priority: Literal["normal", "high"] | None = None
    labels: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("conversation_ids")
    @classmethod
    def validate_conversation_ids(cls, ids: list[str]) -> list[str]:
        normalized = [conversation_id.strip() for conversation_id in ids]
        if any(not 5 <= len(conversation_id) <= 160 for conversation_id in normalized):
            raise ValueError("conversation_ids must contain values between 5 and 160 characters")
        return list(dict.fromkeys(normalized))

    @field_validator("labels")
    @classmethod
    def validate_bulk_labels(cls, labels: list[str]) -> list[str]:
        return normalize_conversation_labels(labels)

    @model_validator(mode="after")
    def validate_action_payload(self) -> "BulkConversationActionRequest":
        if self.action == "set_priority" and (self.priority is None or self.labels):
            raise ValueError("priority is required and labels must be omitted for set_priority")
        if self.action in {"add_labels", "remove_labels"} and (
            self.priority is not None or not self.labels
        ):
            raise ValueError("labels are required and priority must be omitted for label actions")
        if self.action in {"claim", "release"} and (self.priority is not None or self.labels):
            raise ValueError("priority and labels must be omitted for claim/release actions")
        return self


class AssignConversationRequest(StrictModel):
    assignee_id: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._:@-]+$")


class ConversationLabelOut(StrictModel):
    label: str
    conversation_count: int


class BulkConversationActionOut(StrictModel):
    requested: int
    matched: int
    updated: int
    unchanged: int


class FeedbackRequest(StrictModel):
    message_id: str = Field(min_length=5, max_length=80)
    rating: Literal[-1, 1]
    reason: str | None = Field(default=None, max_length=500)


class KnowledgeCreateRequest(StrictModel):
    title: str = Field(min_length=2, max_length=160)
    content: str = Field(min_length=10, max_length=12000)
    tags: list[str] = Field(min_length=1, max_length=20)
    category: str = Field(default="general", min_length=2, max_length=60)
    source_url: str = Field(min_length=1, max_length=500)
    # Backlog (多语言客服): the article's language; null means language-agnostic
    # (matches any customer language in retrieval).
    language: str | None = None

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, tags: list[str]) -> list[str]:
        normalized = [tag.strip() for tag in tags if tag.strip()]
        if not normalized or any(len(tag) > 40 for tag in normalized):
            raise ValueError("tags must contain non-empty values up to 40 characters")
        return list(dict.fromkeys(normalized))

    @field_validator("language")
    @classmethod
    def validate_language(cls, language: str | None) -> str | None:
        if language is None:
            return None
        code = language.strip().lower()
        if code not in KNOWN_LANGUAGES:
            raise ValueError(f"language must be one of {sorted(KNOWN_LANGUAGES)}")
        return code


class KnowledgeUpdateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=2, max_length=160)
    content: str | None = Field(default=None, min_length=10, max_length=12000)
    tags: list[str] | None = Field(default=None, min_length=1, max_length=20)
    category: str | None = Field(default=None, min_length=2, max_length=60)
    source_url: str | None = Field(default=None, min_length=1, max_length=500)
    active: bool | None = None
    language: str | None = None

    @field_validator("tags")
    @classmethod
    def validate_optional_tags(cls, tags: list[str] | None) -> list[str] | None:
        if tags is None:
            return None
        return KnowledgeCreateRequest.validate_tags(tags)

    @field_validator("language")
    @classmethod
    def validate_optional_language(cls, language: str | None) -> str | None:
        if language is None:
            return None
        return KnowledgeCreateRequest.validate_language(language)


class ConversationOut(StrictModel):
    id: str
    tenant_id: str
    customer_name: str
    customer_ref: str | None = None
    channel: str
    status: str
    intent: str | None = None
    assigned_agent: str | None = None
    priority: str
    handoff_reason: str | None = None
    sla_due_at: str | None = None
    last_confidence: float | None = None
    version: int = 1
    created_at: str
    updated_at: str
    resolved_at: str | None = None
    preview: str | None = None
    message_count: int = 0
    last_message_at: str | None = None
    labels: list[str] = Field(default_factory=list)
    claimed_by: str | None = None
    claimed_at: str | None = None
    claim_expires_at: str | None = None
    claim_active: bool = False
    sla_breached: bool = False
    needs_response: bool = False
    waiting_since: str | None = None
    first_response_at: str | None = None
    # Backlog (CSAT): populated on the resolve response with a one-time
    # customer survey link; everything else leaves it null.
    survey_url: str | None = None
    # Backlog (多语言客服): detected language of the customer's latest
    # message; null until a customer message has been detected.
    language: str | None = None
    # Backlog (工单化): the ticket this conversation belongs to, when it has
    # been converted or linked; null otherwise.
    ticket_id: str | None = None


class MessageOut(StrictModel):
    id: str
    role: str
    author: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str
    # Backlog (坐席协作): internal-note threading — the note a message replies
    # to (an internal note id in the same conversation), or null. Always
    # present so ``list_messages``/``message_out`` round-trip cleanly.
    reply_to: str | None = None


class MentionOut(StrictModel):
    id: str
    conversation_id: str
    conversation_customer: str
    channel: str
    mentioned_by: str
    note_id: str
    note_preview: str
    created_at: str
    read_at: str | None = None
    unread: bool = False


class MentionsOut(StrictModel):
    mentions: list[MentionOut] = Field(default_factory=list)
    unread_count: int = 0


class ThreadOut(StrictModel):
    root: MessageOut
    replies: list[MessageOut] = Field(default_factory=list)


class ConversationThreadsOut(StrictModel):
    threads: list[ThreadOut] = Field(default_factory=list)


class AuditEventOut(StrictModel):
    id: str
    request_id: str | None = None
    actor: str
    event_type: str
    payload: dict[str, Any]
    created_at: str


class AuditArchiveOut(StrictModel):
    """Metadata for one durable audit-retention export batch."""

    id: str
    cutoff: str
    event_count: int
    first_seq: int
    last_seq: int
    first_event_hash: str
    last_event_hash: str
    content_sha256: str
    created_at: str


class AuditArchiveDetailOut(AuditArchiveOut):
    """A durable audit export with its original hash-chain rows."""

    events: list[dict[str, Any]]


class ConversationDetail(StrictModel):
    conversation: ConversationOut
    messages: list[MessageOut]
    audit_events: list[AuditEventOut]
    # Backlog (session intelligent summary): operator-facing context brief and
    # disposition draft, when generated. Add-only, always present (nullable).
    summaries: list["ConversationSummaryOut"] = Field(default_factory=list)


class ConversationSummaryOut(StrictModel):
    kind: str
    content: str
    source: str
    updated_at: str


class TurnResponse(StrictModel):
    customer_message: MessageOut
    assistant_message: MessageOut | None
    conversation: ConversationOut
    idempotent_replay: bool


class TurnJobOut(StrictModel):
    id: str
    tenant_id: str
    conversation_id: str
    status: Literal["queued", "processing", "completed", "failed"]
    attempts: int
    max_attempts: int
    available_at: str
    locked_at: str | None = None
    error_code: str | None = None
    created_at: str
    updated_at: str
    completed_at: str | None = None
    idempotent_replay: bool = False
    result: TurnResponse | None = None


class DashboardOut(StrictModel):
    open: int
    waiting_human: int
    human_active: int
    resolved: int
    total: int
    sla_breached: int
    claimed_active: int = 0
    high_priority: int = 0
    needs_response: int = 0
    average_first_response_seconds: float = 0.0
    automated_responses: int
    average_confidence: float
    grounded_rate: float
    positive_feedback_rate: float


class SavedQueueViewFilters(StrictModel):
    status: Literal["open", "waiting_human", "human_active", "resolved"] | None = None
    search: str | None = Field(default=None, max_length=120)
    label: str | None = Field(default=None, max_length=32)
    priority: Literal["normal", "high"] | None = None
    channel: str | None = Field(default=None, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    sort: Literal["priority", "waiting", "sla", "updated"] | None = None
    ownership: (
        Literal[
            "mine", "unassigned", "unclaimed", "claimed_by_me", "sla_breached", "needs_response"
        ]
        | None
    ) = None

    @field_validator("label")
    @classmethod
    def validate_saved_view_label(cls, label: str | None) -> str | None:
        if label is None:
            return None
        return normalize_conversation_labels([label])[0]


class SavedQueueViewCreateRequest(StrictModel):
    name: str = Field(min_length=1, max_length=60)
    filters: SavedQueueViewFilters


class SavedQueueViewOut(StrictModel):
    id: str
    tenant_id: str
    actor_id: str
    name: str
    filters: SavedQueueViewFilters
    created_at: str
    updated_at: str


class CannedResponseCreateRequest(StrictModel):
    title: str = Field(min_length=1, max_length=120)
    body: str = Field(min_length=1, max_length=4000)
    shortcut: str | None = Field(default=None, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    tags: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("tags")
    @classmethod
    def validate_macro_tags(cls, tags: list[str]) -> list[str]:
        normalized = [tag.strip() for tag in tags if tag.strip()]
        if any(len(tag) > 40 for tag in normalized):
            raise ValueError("tags must contain values up to 40 characters")
        return list(dict.fromkeys(normalized))


class CannedResponseUpdateRequest(StrictModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    shortcut: str | None = Field(default=None, max_length=40, pattern=r"^[a-zA-Z0-9_-]+$")
    tags: list[str] | None = Field(default=None, max_length=20)
    active: bool | None = None

    @field_validator("tags")
    @classmethod
    def validate_optional_macro_tags(cls, tags: list[str] | None) -> list[str] | None:
        if tags is None:
            return None
        return CannedResponseCreateRequest.validate_macro_tags(tags)


class CannedResponseOut(StrictModel):
    id: str
    tenant_id: str
    title: str
    body: str
    shortcut: str | None = None
    tags: list[str] = Field(default_factory=list)
    active: bool = True
    usage_count: int = 0
    created_by: str
    updated_by: str
    created_at: str
    updated_at: str


class FeedbackOut(StrictModel):
    id: str
    tenant_id: str
    conversation_id: str
    message_id: str
    actor: str
    rating: int
    reason: str | None = None
    created_at: str
    updated_at: str


class KnowledgeArticleOut(StrictModel):
    id: str
    tenant_id: str
    title: str
    content: str
    tags: list[str]
    category: str
    source_url: str
    active: bool
    status: str = "published"
    version: int
    updated_at: str
    reviewed_by: str | None = None
    reviewed_at: str | None = None
    # Backlog (多语言客服): article language; null = language-agnostic.
    language: str | None = None


class MeOut(StrictModel):
    tenant_id: str
    actor_id: str
    role: str
    permissions: list[str]
    local_drafts_enabled: bool = True
    local_draft_ttl_minutes: int = 720
    # Phase 28.2: the credential id (sha256[:12]) used to revoke this key.
    credential_id: str = ""


class PromptVersionCreateRequest(StrictModel):
    name: str = Field(min_length=2, max_length=80, pattern=r"^[a-z][a-z0-9_]*$")
    version: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=10, max_length=8000)
    model_ref: str = Field(min_length=2, max_length=120)


class PromptVersionOut(StrictModel):
    id: str
    tenant_id: str | None
    name: str
    version: str
    body: str
    model_ref: str
    status: str
    created_by: str
    created_at: str
    updated_at: str
    activated_at: str | None


class PromptVersionActionRequest(StrictModel):
    action: Literal["activate", "canary", "rollback"]


class TenantModelPolicyRequest(StrictModel):
    allowed_models: list[str] | None = None
    daily_turn_budget: int | None = Field(default=None, ge=1, le=1_000_000)


class TenantModelPolicyOut(StrictModel):
    tenant_id: str
    allowed_models: list[str] | None = None
    daily_turn_budget: int | None = None
    daily_turn_count: int = 0


class TenantProvisionRequest(StrictModel):
    """Provision a new tenant (Phase 22.1)."""

    tenant_id: str = Field(min_length=2, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$")
    name: str = Field(min_length=1, max_length=120)
    allowed_models: list[str] | None = None
    daily_turn_budget: int | None = Field(default=None, ge=1, le=1_000_000)
    conversation_quota: int | None = Field(default=None, ge=1, le=10_000_000)
    storage_quota_bytes: int | None = Field(default=None, ge=1, le=10**15)
    # ROADMAP 43.4: data residency fixed at creation time. Constrained to
    # identifier-style names so inventory lookups cannot be defeated by
    # whitespace/case variants; deployments register their regions in
    # ``app.residency.REGION_INVENTORY`` and unknown names are flagged in
    # manifests/evidence rather than silently trusted.
    region: str | None = Field(
        default=None, min_length=1, max_length=40, pattern=r"^[a-z0-9][a-z0-9_-]*$"
    )


class TenantQuotaOut(StrictModel):
    """Tenant quota + policy readout (Phase 22.4)."""

    tenant_id: str
    name: str
    allowed_models: list[str] | None = None
    daily_turn_budget: int | None = None
    conversation_quota: int | None = None
    storage_quota_bytes: int | None = None
    created_at: str
    region: str = "local"


class TenantQuotaUpdateRequest(StrictModel):
    """Partial quota update (Phase 32.2).

    Mirrors the existing ``dict[str, Any]`` payload but with strict validation
    matching ``TenantProvisionRequest``: negative or out-of-range values are
    rejected at the schema boundary instead of reaching the database.  All
    fields are optional so a caller can update either or both limits.
    """

    conversation_quota: int | None = Field(default=None, ge=1, le=10_000_000)
    storage_quota_bytes: int | None = Field(default=None, ge=1, le=10**15)


class MemberInviteRequest(StrictModel):
    """Invite a member to a tenant (Phase 22.2)."""

    actor_id: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._:@-]+$")
    role: Literal["admin", "supervisor", "operator", "channel", "viewer", "auditor"]


class MemberRoleUpdateRequest(StrictModel):
    role: Literal["admin", "supervisor", "operator", "channel", "viewer", "auditor"]


class TenantMemberOut(StrictModel):
    id: str
    tenant_id: str
    actor_id: str
    role: str
    status: str
    invited_by: str | None = None
    invited_at: str | None = None
    updated_at: str
    last_login_at: str | None = None


class CollaboratorOut(StrictModel):
    """A tenant actor offered by the @-mention autocomplete (backlog M18)."""

    actor_id: str = Field(min_length=2, max_length=80, pattern=r"^[A-Za-z0-9._:@-]+$")
    role: Literal["admin", "supervisor", "operator", "channel", "viewer", "auditor"]


class CsatDayOut(StrictModel):
    """One day of the CSAT summary trend (backlog)."""

    date: str
    count: int
    avg_rating: float


class CsatSummaryOut(StrictModel):
    """Admin「CSAT 评分汇总」card payload (backlog)."""

    total: int
    avg_rating: float
    positive_rate: float
    per_day: list[CsatDayOut]


class TenantUsageRowOut(StrictModel):
    """One day of raw billing usage for a tenant (Phase 22.4)."""

    tenant_id: str
    date: str
    turn_count: int
    conversation_count: int
    message_count: int


class SetConversationLanguageRequest(StrictModel):
    """Manual language override for one conversation (backlog M19).

    ``None`` clears the override so the writer path re-detects automatically.
    """

    language: str | None = Field(default=None, min_length=2, max_length=16)

    @field_validator("language")
    @classmethod
    def validate_language(cls, language: str | None) -> str | None:
        if language is None:
            return None
        code = language.strip().lower()
        if code not in KNOWN_LANGUAGES:
            raise ValueError(f"language must be one of {sorted(KNOWN_LANGUAGES)}")
        return code


class TranslateMessageRequest(StrictModel):
    """Ask for a translated rendering of one customer message (backlog M19)."""

    target_language: str = Field(min_length=2, max_length=16)

    @field_validator("target_language")
    @classmethod
    def validate_target_language(cls, target_language: str) -> str:
        code = target_language.strip().lower()
        if code not in KNOWN_LANGUAGES:
            raise ValueError(f"target_language must be one of {sorted(KNOWN_LANGUAGES)}")
        return code


class TranslateOut(StrictModel):
    """Translation response; never a hard failure without a model available."""

    translated: str
    was_translated: bool
    source: str


class WebhookCreateRequest(StrictModel):
    url: str = Field(min_length=8, max_length=500)
    events: list[str] = Field(min_length=1, max_length=20)
    secret: str = Field(min_length=8, max_length=200)


class WebhookOut(StrictModel):
    id: str
    tenant_id: str
    url: str
    events: list[str]
    status: str
    created_at: str
    updated_at: str


class WebhookDeliveryOut(StrictModel):
    id: str
    tenant_id: str
    endpoint_id: str
    event_type: str
    event_id: str
    status: str
    attempts: int
    max_attempts: int
    next_attempt_at: str
    last_response_code: int | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class QualityBucketOut(StrictModel):
    """One row of the daily quality aggregate (Phase 21.1)."""

    date: str
    intent: str
    prompt_version: str
    turn_count: int
    escalation_count: int
    negative_feedback_count: int
    escalation_rate: float
    negative_feedback_rate: float
    avg_first_response_seconds: float
    avg_latency_ms: float
    estimated_tokens: int


class KnowledgeReviewRequest(StrictModel):
    """Approve or reject a pending knowledge article (Phase 21.3)."""

    action: Literal["publish", "retire"]
    notes: str | None = Field(default=None, max_length=400)


class AgentGroupCreateRequest(StrictModel):
    """Create an agent group with skill tags and a capacity cap (backlog)."""

    name: str = Field(min_length=1, max_length=80)
    skills: list[str] = Field(default_factory=list, max_length=20)
    capacity: int = Field(default=1, ge=1, le=100)


class AgentGroupOut(StrictModel):
    id: str
    tenant_id: str
    name: str
    skills: list[str] = Field(default_factory=list)
    capacity: int
    created_at: str


class AgentGroupMemberOut(StrictModel):
    group_id: str
    tenant_id: str
    actor_id: str
    added_at: str


class RoutingRuleCreateRequest(StrictModel):
    """A routing rule: match intent/label/channel -> group (backlog)."""

    intent: str | None = Field(default=None, max_length=80)
    label: str | None = Field(default=None, max_length=80)
    channel: str | None = Field(default=None, max_length=40)
    group_id: str = Field(min_length=5, max_length=80)
    priority: int = Field(default=0, ge=0, le=1000)


class RoutingRuleOut(StrictModel):
    id: str
    tenant_id: str
    intent: str | None = None
    label: str | None = None
    channel: str | None = None
    group_id: str
    priority: int
    created_at: str


class SlaPolicyRequest(StrictModel):
    """Configure an SLA policy (backlog): tenant/priority/channel limits.

    ``tenant_id``/``priority``/``channel`` may be null to express defaults;
    resolution falls back exact -> tenant -> global default.
    """

    tenant_id: str | None = Field(default=None, min_length=2, max_length=40)
    priority: Literal["normal", "high"] | None = None
    channel: str | None = Field(default=None, max_length=40)
    first_response_minutes: int = Field(ge=1, le=10080)
    resolve_minutes: int = Field(ge=1, le=10080)


class SlaPolicyOut(StrictModel):
    id: str
    tenant_id: str | None = None
    priority: str | None = None
    channel: str | None = None
    first_response_minutes: int
    resolve_minutes: int
    created_at: str
    updated_at: str


# ---------------------------------------------------------------------------
# AI-assisted operator copilot (backlog: AI 辅助坐席)
# ---------------------------------------------------------------------------


class CopilotSuggestRequest(StrictModel):
    conversation_id: str = Field(min_length=5, max_length=80)
    draft: str | None = Field(default=None, max_length=4000)
    limit: int = Field(default=3, ge=1, le=3)


class CopilotSuggestionOut(StrictModel):
    content: str
    source: str


class CopilotSuggestOut(StrictModel):
    suggestions: list[CopilotSuggestionOut] = Field(default_factory=list)


class CopilotKnowledgeRequest(StrictModel):
    conversation_id: str = Field(min_length=5, max_length=80)
    query: str | None = Field(default=None, max_length=500)
    limit: int = Field(default=3, ge=1, le=3)


class CopilotKnowledgeArticleOut(StrictModel):
    id: str
    title: str
    category: str | None = None
    content: str
    language: str | None = None
    retrieval_score: int | None = None
    matched_terms: list[str] = Field(default_factory=list)
    source: str


class CopilotKnowledgeOut(StrictModel):
    articles: list[CopilotKnowledgeArticleOut] = Field(default_factory=list)


class CopilotRewriteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=4000)
    tone: Literal["friendly", "concise", "professional"] = "professional"


class CopilotRewriteOut(StrictModel):
    rewritten: str
    source: str
    tone: str


# ---------------------------------------------------------------------------
# Ticketing (backlog: 工单化)
# ---------------------------------------------------------------------------


class TicketCreateRequest(StrictModel):
    conversation_id: str = Field(min_length=5, max_length=80)
    subject: str = Field(min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=12000)
    priority: Literal["normal", "high"] = "normal"


class TicketTransitionRequest(StrictModel):
    status: Literal["open", "in_progress", "closed"]
    reason: str | None = Field(default=None, max_length=500)


class TicketUpdateRequest(StrictModel):
    subject: str | None = Field(default=None, min_length=2, max_length=160)
    description: str | None = Field(default=None, max_length=12000)
    priority: Literal["normal", "high"] | None = None
    assigned_agent: str | None = Field(default=None, min_length=2, max_length=80)


class TicketLinkRequest(StrictModel):
    conversation_id: str = Field(min_length=5, max_length=80)


class TicketConversationOut(StrictModel):
    id: str
    customer_name: str
    status: str
    updated_at: str


class TicketOut(StrictModel):
    id: str
    tenant_id: str
    subject: str
    description: str | None = None
    status: str
    priority: str
    assigned_agent: str | None = None
    customer_name: str
    customer_ref: str | None = None
    source_conversation_id: str | None = None
    created_at: str
    updated_at: str
    closed_at: str | None = None


class TicketDetail(TicketOut):
    conversations: list[TicketConversationOut] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Report export & subscription (backlog: 报表导出与订阅)
# ---------------------------------------------------------------------------


class ReportSubscriptionCreateRequest(StrictModel):
    report_type: Literal["quality", "usage"]
    schedule: Literal["daily", "weekly"] = "daily"
    window_days: int = Field(default=7, ge=1, le=30)
    webhook_endpoint_id: str = Field(min_length=5, max_length=80)


class ReportSubscriptionUpdateRequest(StrictModel):
    active: bool | None = None
    schedule: Literal["daily", "weekly"] | None = None
    window_days: int | None = Field(default=None, ge=1, le=30)


class ReportSubscriptionOut(StrictModel):
    id: str
    tenant_id: str
    report_type: str
    schedule: str
    window_days: int
    webhook_endpoint_id: str
    active: bool
    created_by: str
    created_at: str
    updated_at: str
    last_run_at: str | None = None


class ReportGenerateRequest(StrictModel):
    report_type: Literal["quality", "usage"]
    window_days: int = Field(default=7, ge=1, le=30)
    webhook_endpoint_id: str | None = Field(default=None, min_length=5, max_length=80)


class ReportGenerateOut(StrictModel):
    report_type: str
    from_date: str
    to_date: str
    window_days: int
    rows: list[dict[str, Any]] = Field(default_factory=list)
    generated_at: str
    deliveries: int = 0


# ---------------------------------------------------------------------------
# Rich-media attachments (backlog: 语音/富媒体消息)
# ---------------------------------------------------------------------------


class AttachmentOut(StrictModel):
    id: str
    tenant_id: str
    conversation_id: str
    message_id: str | None = None
    filename: str
    content_type: str
    size_bytes: int
    uploader: str
    status: str
    scanned: bool
    verdict: str
    created_at: str
    # 42.4 SEC-006: content digest pinned at upload; clients can verify the
    # downloaded bytes against it. NULL for legacy rows.
    sha256: str | None = None


class ScanVerdictIn(StrictModel):
    """External AV/CDR verdict for a quarantined attachment (42.4 SEC-006)."""

    clean: bool
    verdict: str = Field(min_length=1, max_length=200)
