"""OpenAPI metadata enrichment (Phase 25.2).

Every API operation gets a human-readable ``summary``, ``description``,
OpenAPI ``tags`` group, and an RBAC note before the snapshot is generated.
Rather than editing ~60 route decorators, this module centralizes the
metadata in one table and applies it to the built app's routes, so a new
endpoint that is added without metadata is still documented (it appears
under a default tag) and the snapshot gate still catches shape changes.

The enrichment runs inside ``create_app`` right before the app is returned,
so ``/openapi.json`` always reflects it.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

# route key -> (summary, tags, permission note)
_ENDPOINT_META: dict[str, tuple[str, list[str], str]] = {
    "GET /api/me": ("Current user profile", ["auth"], "any authenticated key"),
    "GET /api/conversations": (
        "List conversations",
        ["conversations"],
        "conversation:read",
    ),
    "POST /api/conversations": (
        "Create a conversation",
        ["conversations"],
        "conversation:write",
    ),
    "GET /api/conversations/{conversation_id}": (
        "Conversation detail with messages, audit events, and summaries",
        ["conversations"],
        "conversation:read",
    ),
    "PATCH /api/conversations/{conversation_id}": (
        "Update conversation priority",
        ["conversations"],
        "operator:act",
    ),
    "PUT /api/conversations/{conversation_id}/labels": (
        "Replace conversation labels",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/bulk-actions": (
        "Bulk priority/label/claim actions",
        ["conversations"],
        "operator:act",
    ),
    "GET /api/conversation-labels": (
        "Label catalog with counts",
        ["conversations"],
        "conversation:read",
    ),
    "GET /api/conversations/{conversation_id}/messages": (
        "List conversation messages",
        ["conversations"],
        "conversation:read",
    ),
    "POST /api/conversations/{conversation_id}/messages": (
        "Send a customer turn (idempotent)",
        ["conversations"],
        "conversation:write",
    ),
    "POST /api/conversations/{conversation_id}/claim": (
        "Claim a conversation",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/release": (
        "Release a claim",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/assign": (
        "Assign to an operator",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/accept": (
        "Accept a conversation into human_active",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/resolve": (
        "Resolve a conversation",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/reopen": (
        "Reopen a resolved conversation",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/operator-messages": (
        "Send an operator reply",
        ["conversations"],
        "operator:act",
    ),
    "POST /api/conversations/{conversation_id}/notes": (
        "Add an internal note (supports @mention colleagues and reply threads)",
        ["conversations"],
        "operator:act",
    ),
    "GET /api/admin/csat-summary": (
        "Aggregate answered CSAT surveys; days only bounds the per-day trend (readouts are all-history)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/collaborators": (
        "List tenant actors for the @-mention autocomplete",
        ["collaboration"],
        "operator:act",
    ),
    "PATCH /api/conversations/{conversation_id}/language": (
        "Set (or clear, with null) the manual language override for a conversation",
        ["conversations"],
        "conversation:write",
    ),
    "POST /api/conversations/{conversation_id}/messages/{message_id}/translate": (
        "Translate one customer message; without a provider the original text is echoed",
        ["conversations"],
        "conversation:write",
    ),
    "GET /api/mentions": (
        "List my mentions (unread first) with the unread count",
        ["collaboration"],
        "conversation:read",
    ),
    "POST /api/mentions/{mention_id}/read": (
        "Mark one of my mentions as read (idempotent)",
        ["collaboration"],
        "conversation:read",
    ),
    "GET /api/conversations/{conversation_id}/threads": (
        "List internal discussion threads for a conversation",
        ["collaboration"],
        "conversation:read",
    ),
    "GET /api/conversations/{conversation_id}/events": (
        "Supervisor live view: SSE revision stream for a conversation",
        ["collaboration"],
        "conversation:read",
    ),
    "POST /api/conversations/{conversation_id}/feedback": (
        "Rate an assistant message",
        ["conversations"],
        "conversation:read",
    ),
    "POST /api/conversations/{conversation_id}/turn-jobs": (
        "Enqueue an async turn",
        ["turn-jobs"],
        "conversation:write",
    ),
    "GET /api/turn-jobs": (
        "List turn jobs",
        ["turn-jobs"],
        "conversation:read",
    ),
    "GET /api/turn-jobs/{job_id}": (
        "Fetch a turn job with result",
        ["turn-jobs"],
        "conversation:read",
    ),
    "GET /api/turn-jobs/{job_id}/events": (
        "SSE stream for a turn job",
        ["turn-jobs"],
        "conversation:read",
    ),
    "POST /api/turn-jobs/{job_id}/retry": (
        "Retry a failed turn job",
        ["turn-jobs"],
        "conversation:write",
    ),
    "GET /api/saved-views": (
        "List saved queue views",
        ["queues"],
        "conversation:read",
    ),
    "POST /api/saved-views": (
        "Create a saved queue view",
        ["queues"],
        "conversation:read",
    ),
    "DELETE /api/saved-views/{view_id}": (
        "Delete a saved queue view",
        ["queues"],
        "conversation:read",
    ),
    "GET /api/dashboard": (
        "Queue and quality dashboard indicators",
        ["dashboard"],
        "conversation:read",
    ),
    "GET /api/canned-responses": (
        "List canned responses",
        ["canned-responses"],
        "conversation:read",
    ),
    "POST /api/canned-responses": (
        "Create a canned response",
        ["canned-responses"],
        "knowledge:write",
    ),
    "PATCH /api/canned-responses/{response_id}": (
        "Update a canned response",
        ["canned-responses"],
        "knowledge:write",
    ),
    "DELETE /api/canned-responses/{response_id}": (
        "Delete a canned response",
        ["canned-responses"],
        "knowledge:write",
    ),
    "GET /api/knowledge": (
        "List published knowledge articles",
        ["knowledge"],
        "conversation:read",
    ),
    "POST /api/knowledge": (
        "Create a published knowledge article",
        ["knowledge"],
        "knowledge:write",
    ),
    "PATCH /api/knowledge/{article_id}": (
        "Update a knowledge article",
        ["knowledge"],
        "knowledge:write",
    ),
    "POST /api/knowledge/drafts": (
        "Create a knowledge draft (invisible until approved)",
        ["knowledge"],
        "knowledge:write",
    ),
    "POST /api/knowledge/{article_id}/review": (
        "Publish or retire a pending knowledge article",
        ["knowledge"],
        "knowledge:write",
    ),
    "POST /api/conversations/{conversation_id}/messages/{message_id}/knowledge-draft": (
        "Create a draft from a negatively-rated message",
        ["knowledge"],
        "knowledge:write",
    ),
    "GET /api/audit-events": (
        "List audit events",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/audit-archives": (
        "List audit archives",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/audit-archives/{archive_id}": (
        "Get audit archive",
        ["audit"],
        "metrics:read",
    ),
    "GET /api/admin/audit/anchors": (
        "List audit frontier anchors",
        ["audit"],
        "admin:manage",
    ),
    "POST /api/admin/audit/anchors/verify": (
        "Re-verify external audit anchors against the local chain",
        ["audit"],
        "admin:manage",
    ),
    "GET /api/admin/audit/gaps": (
        "List observable audit gap counts",
        ["audit"],
        "admin:manage",
    ),
    "GET /api/supervisor/quality": (
        "Quality buckets by day/intent/prompt_version",
        ["quality"],
        "metrics:read",
    ),
    "GET /api/supervisor/knowledge-gaps": (
        "Negative-feedback turns without knowledge citations",
        ["quality"],
        "metrics:read",
    ),
    "GET /api/system/metrics": (
        "Runtime metrics snapshot",
        ["system"],
        "metrics:read",
    ),
    "GET /api/prompts": (
        "List prompt versions",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/prompts": (
        "Register a prompt version",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/prompts/{version_id}/action": (
        "Activate, canary, or rollback a prompt version",
        ["prompts"],
        "admin:manage",
    ),
    "PUT /api/admin/tenants/{tenant_id}/model-policy": (
        "Set tenant model policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/model-policy": (
        "Read tenant model policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/retention/policies": (
        "List retention policies",
        ["admin"],
        "admin:manage",
    ),
    "PUT /api/retention/policies/{data_type}": (
        "Set a retention policy",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/retention/enforce": (
        "Run retention enforcement",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/data-subject-requests": (
        "List data subject requests",
        ["privacy"],
        "privacy:request",
    ),
    "POST /api/data-subject-requests": (
        "Create a data subject request",
        ["privacy"],
        "privacy:request",
    ),
    "POST /api/data-subject-requests/{request_id}/approve": (
        "Approve a data subject request",
        ["privacy"],
        "privacy:approve",
    ),
    "POST /api/data-subject-requests/{request_id}/execute": (
        "Execute an approved data subject request",
        ["privacy"],
        "privacy:execute",
    ),
    "GET /api/data-subject-requests/{request_id}/export": (
        "Download a data subject export",
        ["privacy"],
        "privacy:execute",
    ),
    "GET /api/privacy/board": (
        "Privacy approval board (redacted refs, SLA posture, failure details)",
        ["privacy"],
        "privacy:manage",
    ),
    "POST /api/privacy/sla/scan": (
        "Re-scan open DSRs and flag SLA breaches",
        ["privacy"],
        "privacy:manage",
    ),
    "POST /api/privacy/requests/{request_id}/retry": (
        "Return a failed DSR to approved for re-execution",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/privacy/deletion-proof": (
        "Deletion attestation keyed by the DSR execution secret",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/privacy/tombstones": (
        "List recorded customer tombstones",
        ["privacy"],
        "privacy:manage",
    ),
    "GET /api/webhooks": (
        "List webhook endpoints",
        ["webhooks"],
        "admin:manage",
    ),
    "POST /api/webhooks": (
        "Register a webhook endpoint",
        ["webhooks"],
        "admin:manage",
    ),
    "DELETE /api/webhooks/{endpoint_id}": (
        "Delete a webhook endpoint",
        ["webhooks"],
        "admin:manage",
    ),
    "GET /api/webhooks/deliveries": (
        "List webhook deliveries",
        ["webhooks"],
        "admin:manage",
    ),
    "POST /api/webhooks/{endpoint_id}/deliveries/{delivery_id}/retry": (
        "Retry a webhook delivery",
        ["webhooks"],
        "admin:manage",
    ),
    "GET /api/events/queue": (
        "SSE queue updates",
        ["queues"],
        "conversation:read",
    ),
    "GET /api/prompts/export": (
        "Export prompt registry",
        ["prompts"],
        "admin:manage",
    ),
    "POST /api/admin/tenants": (
        "Provision a tenant (idempotent)",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/quota": (
        "Read tenant quota",
        ["admin"],
        "tenant:manage",
    ),
    "PUT /api/admin/tenants/{tenant_id}/quota": (
        "Update tenant quota",
        ["admin"],
        "tenant:manage",
    ),
    "POST /api/admin/tenants/{tenant_id}/members": (
        "Invite a tenant member",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/tenants/{tenant_id}/members": (
        "List tenant members",
        ["admin"],
        "tenant:manage",
    ),
    "PATCH /api/admin/tenants/{tenant_id}/members/{actor_id}": (
        "Change a member's role",
        ["admin"],
        "tenant:manage",
    ),
    "POST /api/admin/tenants/{tenant_id}/members/{actor_id}/deactivate": (
        "Deactivate a tenant member",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/usage": (
        "Export raw daily tenant usage",
        ["admin"],
        "tenant:manage",
    ),
    "GET /api/admin/diagnostics": (
        "Support diagnostics bundle (version/config/queue/audit head)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/agent-groups": (
        "List agent groups (skills + capacity)",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/agent-groups": (
        "Create an agent group",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/agent-groups/{group_id}": (
        "Delete an agent group",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/agent-groups/{group_id}/agents": (
        "Add an agent to a group",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/agent-groups/{group_id}/agents/{actor_id}": (
        "Remove an agent from a group",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/sla-policies": (
        "List SLA policies (tenant/priority/channel limits)",
        ["admin"],
        "admin:manage",
    ),
    "PUT /api/admin/sla-policies": (
        "Configure an SLA policy",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/routing-rules": (
        "List routing rules",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/routing-rules": (
        "Create a routing rule (intent/label/channel -> group)",
        ["admin"],
        "admin:manage",
    ),
    "DELETE /api/admin/routing-rules/{rule_id}": (
        "Delete a routing rule",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/keys/{credential_id}/revoke": (
        "Revoke an API key by credential id",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/admin/keys": (
        "Issue a fresh runtime API key (secret returned once)",
        ["admin"],
        "admin:manage",
    ),
    "GET /api/admin/keys": (
        "List API-key credentials (secrets are never returned)",
        ["admin"],
        "admin:manage",
    ),
    "POST /api/widget/sessions": (
        "Open a widget chat session (signed token)",
        ["widget"],
        "X-Widget-Token (signed, no API key)",
    ),
    "POST /api/channels/{account_id}/webhook": (
        "Receive a signed formal-channel customer message",
        ["channels"],
        "X-Helix-Timestamp + raw-body HMAC signature (no API key)",
    ),
    "POST /api/widget/sessions/{conversation_id}/messages": (
        "Send a widget message (channel-id idempotent)",
        ["widget"],
        "X-Widget-Token (signed, no API key)",
    ),
    "GET /api/widget/sessions/{conversation_id}/messages": (
        "List widget conversation messages",
        ["widget"],
        "X-Widget-Token (signed, no API key)",
    ),
    "GET /api/widget/sessions/{conversation_id}/stream": (
        "SSE stream for the widget conversation's latest turn",
        ["widget"],
        "X-Widget-Token (signed, no API key)",
    ),
    "POST /api/csat/{token}": (
        "Submit a one-time CSAT satisfaction rating",
        ["csat"],
        "one-time survey token (no API key)",
    ),
    "POST /api/copilot/suggest": (
        "Suggest 1-3 customer-facing reply drafts for a conversation",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/copilot/knowledge": (
        "Recommend knowledge articles for the latest customer message",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/copilot/rewrite": (
        "Rewrite an operator draft in a requested tone (best-effort)",
        ["copilot"],
        "operator:act",
    ),
    "POST /api/tickets": (
        "Convert a conversation into a long-cycle ticket (idempotent)",
        ["tickets"],
        "operator:act",
    ),
    "GET /api/tickets": (
        "List tickets (filter by status or customer reference)",
        ["tickets"],
        "conversation:read",
    ),
    "GET /api/tickets/{ticket_id}": (
        "Ticket detail with linked conversations",
        ["tickets"],
        "conversation:read",
    ),
    "PATCH /api/tickets/{ticket_id}": (
        "Update ticket subject/description/priority/assignee",
        ["tickets"],
        "operator:act",
    ),
    "POST /api/tickets/{ticket_id}/transition": (
        "Move a ticket through its state machine (open/in_progress/closed)",
        ["tickets"],
        "operator:act",
    ),
    "POST /api/tickets/{ticket_id}/link": (
        "Link another conversation to the ticket (cross-conversation tracking)",
        ["tickets"],
        "operator:act",
    ),
    "POST /api/admin/report-subscriptions": (
        "Create a scheduled report subscription (webhook delivery)",
        ["reports"],
        "admin:manage",
    ),
    "GET /api/admin/report-subscriptions": (
        "List report subscriptions",
        ["reports"],
        "admin:manage",
    ),
    "PATCH /api/admin/report-subscriptions/{subscription_id}": (
        "Update a report subscription (active/schedule/window)",
        ["reports"],
        "admin:manage",
    ),
    "DELETE /api/admin/report-subscriptions/{subscription_id}": (
        "Delete a report subscription",
        ["reports"],
        "admin:manage",
    ),
    "POST /api/admin/reports/generate": (
        "Generate a quality/usage report on demand (optionally deliver)",
        ["reports"],
        "admin:manage",
    ),
    "GET /api/admin/reports/{report_type}/export": (
        "Export a quality/usage report as CSV",
        ["reports"],
        "admin:manage",
    ),
    "POST /api/attachments": (
        "Upload an attachment to a conversation (validated + scanned)",
        ["attachments"],
        "operator:act",
    ),
    "GET /api/attachments": (
        "List a conversation's stored attachments",
        ["attachments"],
        "conversation:read",
    ),
    "GET /api/attachments/{attachment_id}": (
        "Attachment metadata",
        ["attachments"],
        "conversation:read",
    ),
    "GET /api/attachments/{attachment_id}/download": (
        "Force-download an attachment (Content-Disposition: attachment)",
        ["attachments"],
        "conversation:read",
    ),
    "DELETE /api/attachments/{attachment_id}": (
        "Delete an attachment (removes the file and frees quota)",
        ["attachments"],
        "operator:act",
    ),
    "GET /health": ("Liveness probe", ["system"], "none (unauthenticated)"),
    "GET /health/live": ("Liveness probe", ["system"], "none (unauthenticated)"),
    "GET /health/ready": (
        "Readiness probe (database reachable)",
        ["system"],
        "none (unauthenticated)",
    ),
    "GET /health/startup": (
        "Startup probe (app serving, before dependencies ready)",
        ["system"],
        "none (unauthenticated)",
    ),
    "GET /": ("Operator workspace", ["ui"], "none (unauthenticated)"),
    "POST /api/canned-responses/{response_id}/use": (
        "Record canned-response usage",
        ["canned-responses"],
        "operator:act",
    ),
    "GET /auth/login": ("OIDC login redirect", ["auth"], "none (unauthenticated)"),
    "GET /auth/callback": ("OIDC callback", ["auth"], "none (unauthenticated)"),
    "POST /auth/logout": ("End the session", ["auth"], "any authenticated session"),
    "GET /auth/session": ("Current session", ["auth"], "any authenticated session"),
    "POST /auth/refresh": (
        "Refresh the session cookie",
        ["auth"],
        "any authenticated session",
    ),
}


def _operation_key(route: Any) -> str | None:
    methods = getattr(route, "methods", None)
    if not methods:
        return None
    method = next(iter(methods), "").upper()
    if method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return None
    return f"{method} {route.path}"


def _iter_routes(app: Any) -> Any:
    """Yield every APIRoute, including those inside included routers.

    FastAPI 0.139 wraps ``include_router`` targets in a lazy ``_IncludedRouter``
    that is materialized at request time; its inner ``APIRouter.routes`` are
    the real route objects and must be enriched too.
    """
    for route in app.routes:
        if type(route).__name__ == "_IncludedRouter":
            inner = getattr(route, "original_router", None)
            if inner is not None:
                yield from inner.routes
            continue
        yield route


def enrich_openapi(app: Any) -> None:
    """Apply summaries/tags/permission notes to every registered operation.

    FastAPI stores summary/description/tags on each ``APIRoute`` at
    construction time (from the decorator args or the endpoint docstring).
    Mutating those attributes before the OpenAPI spec is generated is the
    supported hook; the builder reads them via ``get_openapi``.
    """
    for route in _iter_routes(app):
        key = _operation_key(route)
        if key is None:
            continue
        meta = _ENDPOINT_META.get(key)
        if meta is None:
            continue
        summary, tags, permission = meta
        if not getattr(route, "summary", None):
            route.summary = summary
        if not getattr(route, "description", None) or route.description == route.summary:
            route.description = f"{summary}. Requires: {permission}."
        if not getattr(route, "tags", None):
            route.tags = tags
        # 43.3: deprecated operations are flagged on the route itself so the
        # generated spec (and clients reading it) see ``deprecated: true``.
        from app.deprecation import deprecation_for

        entry = deprecation_for(key)
        if entry is not None:
            route.deprecated = True


def apply_openapi_metadata(app: Any) -> Callable[[], dict[str, Any]]:
    """Return an openapi() implementation that enriches the spec first.

    FastAPI 0.139 defers OpenAPI generation until first access; we hook the
    app's ``openapi`` method so summary/description/tags are injected onto
    routes before the spec is built, then call the original builder.
    """
    original_openapi = app.openapi

    def enriched_openapi() -> dict[str, Any]:
        enrich_openapi(app)
        spec = original_openapi()
        # 43.3: deprecated operations also carry the migration note in the
        # document itself (description + successor pointer).
        from app.deprecation import enrich_openapi_with_deprecations

        enrich_openapi_with_deprecations(spec.get("paths", {}))
        return spec

    return enriched_openapi
