"""Capability tokens and side-effect classification for tool calls (ROADMAP 43.5).

Every gateway-mediated tool call carries a **capability token**: a signed
(HMAC-SHA256), short-lived, single-purpose grant naming the tool, the tenant,
and an arguments-schema digest.  The gateway verifies the signature, the
expiry, the subject match, and that the actual arguments validate against the
tool's declared parameter schema *before* any connector runs — a model cannot
summon a capability it was never granted.

Each tool also declares a **side-effect class**:

- ``readonly``   — no state change; no approval needed (orders.lookup …).
- ``mutating``   — changes business state; needs a registered write tool and
  prior human confirmation (the 41.5 confirmation path).
- ``high-risk``  — mutating *and* irreversible/externally visible (refund,
  cancel-order); additionally requires a human approval record in the AI
  governance registry before execution.

Budgets are enforced per tool call: a wall-clock budget and an optional
per-call cost estimate cap, so a runaway loop of expensive calls is bounded.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

TOKEN_SCHEMA = 1
DEFAULT_TOKEN_TTL_SECONDS = 300
SIDEEFFECT_CLASSES = ("readonly", "mutating", "high_risk")


class CapabilityError(PermissionError):
    """A capability token failed verification or does not cover the call."""


def _canonical(document: dict[str, Any]) -> bytes:
    return json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


@dataclass(frozen=True)
class ToolPolicy:
    """Declarative governance policy for one tool."""

    name: str
    side_effect: str = "readonly"
    parameter_schema: dict[str, Any] = field(default_factory=dict)
    max_duration_ms: int = 5000

    def __post_init__(self) -> None:
        if self.side_effect not in SIDEEFFECT_CLASSES:
            raise ValueError(
                f"unknown side-effect class {self.side_effect!r}; allowed: {list(SIDEEFFECT_CLASSES)}"
            )

    def schema_digest(self) -> str:
        return hashlib.sha256(_canonical(self.parameter_schema)).hexdigest()[:16]


def issue_capability_token(
    *,
    secret: bytes,
    tool: str,
    tenant_id: str,
    ttl_seconds: int = DEFAULT_TOKEN_TTL_SECONDS,
    schema_digest: str = "",
    issued_by: str = "gateway",
    now: float | None = None,
) -> dict[str, Any]:
    """Sign a short-lived single-tool capability token."""
    import time

    moment = time.time() if now is None else now
    body = {
        "schema": TOKEN_SCHEMA,
        "tool": tool,
        "tenant_id": tenant_id,
        "issued_at": moment,
        "expires_at": moment + ttl_seconds,
        "schema_digest": schema_digest,
        "issued_by": issued_by,
    }
    signature = hmac.new(secret, _canonical(body), hashlib.sha256).hexdigest()
    return {"token": body, "signature": signature}


def verify_capability_token(
    secret: bytes,
    grant: dict[str, Any],
    *,
    tool: str,
    tenant_id: str,
    schema_digest: str = "",
    now: float | None = None,
) -> dict[str, Any]:
    """Verify signature, expiry, and subject binding; returns the token body.

    When ``schema_digest`` is given the token must pin exactly that digest of
    the tool's *current* parameter schema — a policy edit invalidates every
    outstanding token.  Any mismatch raises :class:`CapabilityError` —
    verification fails closed.
    """
    import time

    body = grant.get("token")
    signature = grant.get("signature") or ""
    if not isinstance(body, dict):
        raise CapabilityError("capability grant carries no token body")
    expected = hmac.new(secret, _canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature)):
        raise CapabilityError("capability token signature mismatch")
    if int(body.get("schema", 0)) != TOKEN_SCHEMA:
        raise CapabilityError("unsupported capability token schema")
    if str(body.get("tool")) != tool or str(body.get("tenant_id")) != tenant_id:
        raise CapabilityError("capability token does not cover this tool/tenant")
    moment = time.time() if now is None else now
    if float(body.get("expires_at", 0)) <= moment:
        raise CapabilityError("capability token expired")
    if schema_digest and str(body.get("schema_digest") or "") != schema_digest:
        raise CapabilityError("capability token pins a different parameter schema")
    return body


# ---------------------------------------------------------------------------
# Minimal JSON-schema validation (no external dependency)
# ---------------------------------------------------------------------------


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> list[str]:
    """Validate ``arguments`` against a tiny subset of JSON Schema.

    Supports: ``type`` (object/string/integer/number/boolean/array),
    required keys, and per-property type checks. Returns a list of problems;
    empty means valid. Deliberately shallow — deep validation belongs to the
    connector contract — but enough to stop fabricated argument shapes.
    """
    problems: list[str] = []
    if not schema:
        return problems
    expected_type = schema.get("type")
    if expected_type == "object" or (expected_type is None and "properties" in schema):
        for key in schema.get("required", []) or []:
            if key not in arguments:
                problems.append(f"missing required argument {key!r}")
        properties = schema.get("properties") or {}
        type_map = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "array": list,
            "object": dict,
        }
        for name, spec in properties.items():
            if name not in arguments:
                continue
            value = arguments[name]
            if value is None:
                # ``None`` means "not provided" for optional parameters — the
                # gateway passes explicit Nones so callers stay keyword-clean.
                continue
            wanted = type_map.get(str(spec.get("type")))
            if (
                wanted is not None
                and not isinstance(value, bool | bytes)
                and not isinstance(value, wanted)
            ):
                problems.append(f"argument {name!r} must be {spec.get('type')}")
    return problems


__all__ = [
    "TOKEN_SCHEMA",
    "CapabilityError",
    "DEFAULT_TOKEN_TTL_SECONDS",
    "SIDEEFFECT_CLASSES",
    "ToolPolicy",
    "issue_capability_token",
    "validate_arguments",
    "verify_capability_token",
]
