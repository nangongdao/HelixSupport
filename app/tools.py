"""Least-privilege tool gateway with re-authorization (Phase 41.5, AI-001).

The checkpoint before every tool call re-verifies the caller's tenant matches
the resource-owner tenant the agent is acting for, so a model that fabricates a
foreign ``tenant_id`` inside a tool argument gets refused by the gateway, not
silently routed to another tenant's data.  Resource ids in parameters (order
id, customer reference) are canonicalized here instead of trusting a model-built
concatenation, extending the existing ``safe_arguments`` mode.

Write-capable tools are collected in a registry.  With
``require_write_confirmation`` (default true) every write tool needs a prior
human confirmation ``id``; unconfirmed writes are refused and audited.  There
are no write tools in the current system, so the registry is empty and the
confirmation path is forward-reserved (ADR-014 decision 3) — the enforcement
is testable without any real mutating tool.

ROADMAP 43.5 governance layer: every registered tool declares a
:class:`~app.tool_governance.ToolPolicy` (side-effect class + parameter
schema + wall-clock budget).  A call may carry a **capability token**; when it
does the gateway verifies signature/expiry/subject binding and validates the
actual arguments against the declared schema *before* any connector runs —
fail closed.  A ``high_risk`` tool additionally requires a human approval
record in the AI governance registry (:meth:`AiGovernanceService.
require_approved`); without one the call is refused regardless of tokens or
confirmations.  Every refusal is audited as ``tool.denied`` so drift signals
can count denials per tool (ROADMAP 43.5 item 4).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any

from app.connectors import (
    CRMConnector,
    KnowledgeConnector,
    KnowledgeHit,
    OrderConnector,
    SandboxCRMConnector,
    SandboxKnowledgeConnector,
    SandboxOrderConnector,
)
from app.database import Database
from app.tool_governance import (
    CapabilityError,
    ToolPolicy,
    validate_arguments,
    verify_capability_token,
)

logger = logging.getLogger(__name__)

_OBJECT_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

# Tools that mutate state must be registered here (name -> doer) before the
# gateway will allow a call through the confirmation path.  Kept empty until a
# real write tool lands; registry membership is asserted in the WORM-gated
# evaluation suite.
WRITE_TOOLS: dict[str, Any] = {}

# ROADMAP 43.5: declarative policy for every gateway-mediated tool. The
# sandbox surface today is read-only lookups; a new tool registers its side
# effect and argument schema here at the same time as its connector wiring.
DEFAULT_TOOL_POLICIES: dict[str, ToolPolicy] = {
    "orders.lookup": ToolPolicy(
        name="orders.lookup",
        side_effect="readonly",
        parameter_schema={
            "type": "object",
            "required": ["order_id"],
            "properties": {"order_id": {"type": "string"}, "customer_ref": {"type": "string"}},
        },
    ),
    "customers.resolve": ToolPolicy(
        name="customers.resolve",
        side_effect="readonly",
        parameter_schema={
            "type": "object",
            "required": ["customer_ref"],
            "properties": {"customer_ref": {"type": "string"}},
        },
    ),
}


@dataclass(frozen=True)
class ToolExecution:
    tool: str
    success: bool
    code: str
    duration_ms: int
    output: dict[str, Any] = field(default_factory=dict)
    arguments: dict[str, str] = field(default_factory=dict)

    def public_record(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "success": self.success,
            "code": self.code,
            "duration_ms": self.duration_ms,
            "arguments": self.arguments,
        }


@dataclass(frozen=True)
class ToolReauthorization:
    """Context needed to re-check a resource before the gateway executes it."""

    tenant_id: str
    owner_tenant_id: str


class ToolGovernanceDenied(PermissionError):
    """A tool call was refused by the 43.5 governance layer.

    Carries a machine-readable ``reason`` (``token_*`` / ``schema`` /
    ``approval_required`` / ``budget_exceeded``) so callers and drift monitors
    can count denials per tool without parsing messages.
    """

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _safe_resource_id(value: str) -> str:
    """Canonicalize a resource id so no caller-controlled character survives.

    A model that builds ``ORD-1\n...`` or a path fragment inside an id gets a
    scrubbed value back; the connector then behaves as if the id simply did
    not match — nothing foreign is disclosed and nothing is executed.  A
    non-string value (a model hallucinating a numeric id) is refused by the
    schema check downstream, but must not crash canonicalization here.
    """
    if not isinstance(value, str):
        return ""
    scrubbed = "".join(ch for ch in value if ch in _OBJECT_ID_CHARS)
    return scrubbed.strip("-") or ""


class ToolGateway:
    """Re-authorizing entry point for business tools used by agents.

    Tools delegate to :mod:`app.connectors` contracts, so a real connector can
    replace the sandbox without changing how agents call tools.
    """

    def __init__(
        self,
        database: Database,
        *,
        order_connector: OrderConnector | None = None,
        knowledge_connector: KnowledgeConnector | None = None,
        crm_connector: CRMConnector | None = None,
        require_write_confirmation: bool = True,
        tool_policies: dict[str, ToolPolicy] | None = None,
        capability_secret: bytes | str = "",
        governance_service: Any | None = None,
    ) -> None:
        self.database = database
        self._order_connector = order_connector or SandboxOrderConnector(database)
        self._knowledge_connector = knowledge_connector or SandboxKnowledgeConnector(database)
        self._crm_connector = crm_connector or SandboxCRMConnector(database)
        self.require_write_confirmation = require_write_confirmation
        # ROADMAP 43.5: per-tool governance policies (side effect, schema,
        # budget). Defaults cover the sandbox surface; deployments may extend.
        self.tool_policies: dict[str, ToolPolicy] = dict(DEFAULT_TOOL_POLICIES)
        if tool_policies:
            self.tool_policies.update(tool_policies)
        # HMAC secret for capability tokens; empty disables token enforcement
        # (calls without a ``capability`` argument still pass — the token is
        # required only when the caller presents one or when policy demands it).
        self._capability_secret = (
            capability_secret.encode("utf-8")
            if isinstance(capability_secret, str)
            else capability_secret
        )
        # AiGovernanceService for high-risk approvals; None means no high-risk
        # tool is approvable and such calls fail closed.
        self.governance_service = governance_service

    def _authorize(self, authz: ToolReauthorization, resource_id: str | None) -> None:
        if authz.owner_tenant_id != authz.tenant_id:
            raise ValueError(
                "tool call refers to a resource in another tenant (re-authorization refusal)"
            )
        if resource_id is None:
            return
        safe = _safe_resource_id(resource_id)
        if safe != resource_id:
            raise ValueError(
                "tool call carries a non-canonical resource id (re-authorization refusal)"
            )

    def enforce_governance(
        self,
        tool: str,
        tenant_id: str,
        arguments: dict[str, Any],
        *,
        capability: dict[str, Any] | None = None,
    ) -> ToolPolicy:
        """Run the 43.5 governance checks for one call; returns its policy.

        Order matters and is fail closed:

        1. The tool must declare a policy — an unregistered tool has no
           declared side effect, so it cannot be classified and is refused.
        2. A presented capability token must verify against this gateway's
           secret, cover this tool/tenant, and pin the *current* schema digest;
           the actual arguments must validate against the declared schema.
        3. A ``high_risk`` tool requires an approved human approval record
           from the AI governance registry (maker-checker), regardless of any
           token — approval is orthogonal to authentication.
        """
        policy = self.tool_policies.get(tool)
        if policy is None:
            raise ToolGovernanceDenied(
                "policy_missing", f"tool {tool!r} has no registered governance policy"
            )
        if capability is not None:
            if not self._capability_secret:
                raise ToolGovernanceDenied(
                    "token_unsupported", "gateway has no capability secret configured"
                )
            try:
                verify_capability_token(
                    self._capability_secret,
                    capability,
                    tool=tool,
                    tenant_id=tenant_id,
                    schema_digest=policy.schema_digest(),
                )
            except CapabilityError as exc:
                raise ToolGovernanceDenied("token_invalid", str(exc)) from exc
        problems = validate_arguments(policy.parameter_schema, arguments)
        if problems:
            raise ToolGovernanceDenied(
                "schema", f"arguments violate {tool!r} schema: {'; '.join(problems)}"
            )
        if policy.side_effect == "high_risk":
            if self.governance_service is None:
                raise ToolGovernanceDenied(
                    "approval_required",
                    f"high-risk tool {tool!r} requires human approval "
                    "(no governance registry attached)",
                )
            try:
                self.governance_service.require_approved(
                    tenant_id=tenant_id, subject_kind="tool_enablement", subject_id=tool
                )
            except Exception as exc:  # ApprovalRequiredError / lookup failure both deny.
                raise ToolGovernanceDenied(
                    "approval_required",
                    f"high-risk tool {tool!r} lacks an approved record: {exc}",
                ) from exc
        return policy

    def require_confirmation(self, tool: str, confirmation_id: str | None) -> None:
        """Gate a write tool on prior explicit human confirmation.

        With ``require_write_confirmation`` on, a write tool with no
        ``confirmation_id`` is refused before any effect; the audit trail is
        recorded by the caller (``audited_confirmation_granted``).
        """
        if not self.require_write_confirmation:
            return
        if tool not in WRITE_TOOLS:
            raise ValueError(f"tool {tool!r} is not a registered write tool")
        if not confirmation_id:
            raise PermissionError(f"write tool {tool!r} requires human confirmation")

    def search_knowledge(self, tenant_id: str, query: str, *, limit: int = 3) -> list[KnowledgeHit]:
        """Delegate knowledge retrieval to the injected connector (Phase 20.2)."""
        return self._knowledge_connector.search(tenant_id, query, limit=limit)

    def resolve_customer(
        self,
        tenant_id: str,
        customer_ref: str,
        *,
        owner_tenant_id: str | None = None,
        capability: dict[str, Any] | None = None,
    ) -> ToolExecution:
        """Resolve a profile only inside the caller's own tenant.

        ``owner_tenant_id`` must equal ``tenant_id`` (or be omitted, which
        defaults to the same tenant) — a synthesized foreign reference is
        refused here instead of touching the CRM connector.  When a capability
        token is presented it is verified (signature/expiry/schema digest)
        and the arguments validated against the tool's declared schema before
        the connector runs; a failure refuses the call without disclosure.
        """
        started = perf_counter()
        self._authorize(ToolReauthorization(tenant_id, owner_tenant_id or tenant_id), customer_ref)

        def _denied(code: str, reason: str, message: str) -> ToolExecution:
            logger.info("tool.denied tool=%s reason=%s", "customers.resolve", reason)
            return ToolExecution(
                tool="customers.resolve",
                success=False,
                code=code,
                duration_ms=int((perf_counter() - started) * 1000),
                arguments={"customer_ref": customer_ref},
            )

        try:
            self.enforce_governance(
                "customers.resolve",
                tenant_id,
                {"customer_ref": customer_ref},
                capability=capability,
            )
        except ToolGovernanceDenied as exc:
            return _denied("policy_denied", exc.reason, str(exc))

        result = self._crm_connector.resolve_customer(tenant_id, customer_ref)
        output: dict[str, Any] = {}
        if result.ok and result.profile is not None:
            output = {
                "customer_ref": result.profile.customer_ref,
                "name": result.profile.name,
            }
        return ToolExecution(
            tool="customers.resolve",
            success=result.ok,
            code=result.code,
            duration_ms=int((perf_counter() - started) * 1000),
            output=output,
            arguments={"customer_ref": customer_ref},
        )

    def lookup_order(
        self,
        tenant_id: str,
        customer_ref: str | None,
        order_id: str,
        *,
        owner_tenant_id: str | None = None,
        capability: dict[str, Any] | None = None,
    ) -> ToolExecution:
        """Look up an order only inside the caller's own tenant.

        The resource id is canonicalized by the gateway (not trusted from the
        model) before the connector sees it. A non-canonical id (newline,
        statement separator, SQL fragment glued to the id) is refused and
        surfaces as ``not_found`` -- the connector never runs and nothing is
        disclosed (Phase 41.5 / AI-001, tool_parameter_injection).  A failed
        capability-token or schema check (ROADMAP 43.5) likewise refuses the
        call, surfacing as ``policy_denied``.
        """
        started = perf_counter()
        try:
            self._authorize(ToolReauthorization(tenant_id, owner_tenant_id or tenant_id), order_id)
        except ValueError as exc:
            logger.info("order lookup refused: %s", exc)
            return ToolExecution(
                tool="orders.lookup",
                success=False,
                code="not_found",
                duration_ms=int((perf_counter() - started) * 1000),
                arguments={"order_id": order_id},
            )

        def _denied(code: str, reason: str, message: str) -> ToolExecution:
            logger.info("tool.denied tool=%s reason=%s", "orders.lookup", reason)
            return ToolExecution(
                tool="orders.lookup",
                success=False,
                code=code,
                duration_ms=int((perf_counter() - started) * 1000),
                arguments={"order_id": order_id},
            )

        try:
            self.enforce_governance(
                "orders.lookup",
                tenant_id,
                {"order_id": order_id, "customer_ref": customer_ref},
                capability=capability,
            )
        except ToolGovernanceDenied as exc:
            return _denied("policy_denied", exc.reason, str(exc))

        result = self._order_connector.lookup_order(tenant_id, customer_ref, order_id)
        output: dict[str, Any] = {}
        if result.ok and result.order is not None:
            output = {
                "id": result.order.id,
                "status": result.order.status,
                "eta": result.order.eta,
                "tracking_code": result.order.tracking_code,
            }
        return ToolExecution(
            tool="orders.lookup",
            success=result.ok,
            code=result.code,
            duration_ms=int((perf_counter() - started) * 1000),
            output=output,
            arguments={"order_id": order_id},
        )


__all__ = [
    "DEFAULT_TOOL_POLICIES",
    "WRITE_TOOLS",
    "ToolExecution",
    "ToolGateway",
    "ToolGovernanceDenied",
    "ToolReauthorization",
]
