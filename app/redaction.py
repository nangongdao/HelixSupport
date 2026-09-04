"""Structured field-level redaction for logs, traces, exports (Phase 41.4 DATA).

Unified redaction for secret/token/restricted-PII material so that every
sink (logs, trace attributes, diagnostics, audit payloads, exports) scrubs
the same way and a canary check can prove no leak.

Two channels, both applied during recursion:

- **key channel** — a dict key whose name carries a sensitive term (``secret``,
  ``token``, ``password``, ``api_key``, ``authorization``, ...) or whose field
  classification is ``restricted``/``confidential`` is replaced wholesale.
- **value channel** — a scalar value whose shape matches a known secret
  pattern (JWT, Bearer token, ``sk-``, ``ghp_``, long hex/base64, PEM blocks)
  is replaced even when its key name looks innocent.

Field classifications (``public``/``internal``/``confidential``/``restricted``)
are declared statically here and mirrored in the ``data_field_registry`` table
(migration 32) so the machine-readable inventory and the runtime scrubber can
never disagree.

``canary`` support seeds a unique marker into a value so a leakage test can
scan arbitrary output for the marker after a redaction path; finding it means
the redaction failed.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Public data classifications, from most to least sensitive.
PUBLIC = "public"
INTERNAL = "internal"
CONFIDENTIAL = "confidential"
RESTRICTED = "restricted"

CLASSIFICATIONS = (PUBLIC, INTERNAL, CONFIDENTIAL, RESTRICTED)
_RANK = {PUBLIC: 0, INTERNAL: 1, CONFIDENTIAL: 2, RESTRICTED: 3}

REDACTED = "[REDACTED]"
DEFAULT_KEY_TERMS = frozenset(
    {
        "secret",
        "token",
        "password",
        "api_key",
        "apikey",
        "passwd",
        "authorization",
        "credential",
        "key_ref",
        "encrypted_blob",
        "download_token",
        "session_key",
        "access_token",
        "refresh_token",
        "private_key",
    }
)
# Fields redacted when at or above the redaction threshold (confidential/restricted).
RESTRICTED_FIELDS = frozenset(
    {
        "customer_name",
        "customer_ref",
        "api_key",
        "secret",
        "token",
        "password",
        "key_ref",
        "encrypted_blob",
        "authorization",
        "download_token",
        "session_key",
        "encrypted_session",
        "private_key",
        "access_token",
        "refresh_token",
    }
)
# Fields considered PII (confidential even where the key name is generic).
CONFIDENTIAL_FIELDS = frozenset(
    {
        "customer_name",
        "customer_ref",
        "author",
        "claimed_by",
        "assigned_agent",
    }
)

# Key-less secret shapes. Accept some over-redaction in logs/traces: erring on
# the side of hiding a long opaque blob is cheaper than leaking one.
SECRET_VALUE_PATTERNS = (
    re.compile(r"^eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{6,}$"),  # JWT
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{16,}\b"),  # Bearer/OAuth
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),  # OpenAI-style
    re.compile(r"(?i)\bghp_[A-Za-z0-9]{30,}\b"),  # GitHub PAT
    re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),  # Slack bot/user token
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # AWS access key
    re.compile(r"\bBEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY\b"),  # PEM private key
    re.compile(r"\b[0-9a-fA-F]{40,}\b"),  # sha1/sha256-style raw digest / long hex
    re.compile(r"\b[a-zA-Z0-9+/]{44,}={0,2}\b"),  # base64 blob (>= 32 bytes)
)

# Hooks for diagnostics/observability to decide `redact_sensitive` with one call.
MIN_REDACT_FOR_LOGS = CONFIDENTIAL  # logs/traces drop confidential + restricted


@dataclass(frozen=True)
class FieldInfo:
    """Field-level registry entry (mirrors ``data_field_registry``)."""

    field: str
    classification: str
    collection_purpose: str = ""
    retention_days: int | None = None
    region: str = "global"
    downstream: frozenset[str] = frozenset()


# Static seed for the registry; migration 32's inventory may add site-specific
# rows on top of this baseline. Fields absent from both default to ``internal``.
FIELD_REGISTRY: dict[str, FieldInfo] = {
    "customer_name": FieldInfo("customer_name", CONFIDENTIAL, "identify requester", 365, "global"),
    "customer_ref": FieldInfo(
        "customer_ref", CONFIDENTIAL, "link data-subject records", 730, "global"
    ),
    "content": FieldInfo("content", CONFIDENTIAL, "conversation body", 365, "global"),
    "author": FieldInfo("author", CONFIDENTIAL, "message attribution", 365, "global"),
    "actor": FieldInfo("actor", INTERNAL, "operator identity", 2555, "global"),
    "claimed_by": FieldInfo("claimed_by", CONFIDENTIAL, "queue ownership", 365, "global"),
    "assigned_agent": FieldInfo("assigned_agent", CONFIDENTIAL, "assignment", 365, "global"),
    "request_id": FieldInfo("request_id", INTERNAL, "request correlation", 30, "global"),
    "conversation_id": FieldInfo("conversation_id", INTERNAL, "thread identity", 730, "global"),
    "tenant_id": FieldInfo("tenant_id", INTERNAL, "tenancy", 730, "global"),
    "channel": FieldInfo("channel", PUBLIC, "source channel", 730, "global"),
    "api_key": FieldInfo("api_key", RESTRICTED, "authentication", None, "global"),
    "key_ref": FieldInfo("key_ref", RESTRICTED, "credential fingerprint", 2555, "global"),
    "encrypted_blob": FieldInfo("encrypted_blob", RESTRICTED, "encryption at rest", 1, "global"),
    "authorization": FieldInfo("authorization", RESTRICTED, "request auth", 0, "global"),
    # Knowledge-article search/index fields (43.2 bullet 3): every column the
    # knowledge FTS index makes searchable must carry an explicit
    # classification so a future field cannot silently downgrade the
    # encryption/redaction posture by becoming indexable.
    "title": FieldInfo("title", CONFIDENTIAL, "knowledge article title", None, "global"),
    "tags": FieldInfo("tags", CONFIDENTIAL, "knowledge article tags", None, "global"),
    "category": FieldInfo("category", PUBLIC, "knowledge article category", None, "global"),
    "search_terms": FieldInfo(
        "search_terms", CONFIDENTIAL, "derived knowledge search terms", None, "global"
    ),
}


def classify_field(name: str) -> str:
    """Return the classification of a field name, defaulting to ``internal``."""
    entry = FIELD_REGISTRY.get(name)
    if entry is not None:
        return entry.classification
    lowered = name.lower()
    if any(term in lowered for term in DEFAULT_KEY_TERMS):
        return RESTRICTED
    return INTERNAL


def _matches_secret_shape(value: str) -> bool:
    for pattern in SECRET_VALUE_PATTERNS:
        if pattern.search(value):
            return True
    return False


def redact_sensitive(
    value: Any,
    *,
    key_terms: frozenset[str] = DEFAULT_KEY_TERMS,
    placeholder: str = REDACTED,
    redact_value_shapes: bool = True,
) -> Any:
    """Return a deep copy with sensitive fields and secret-shaped values stripped."""
    if isinstance(value, str):
        if redact_value_shapes and _matches_secret_shape(value):
            return placeholder
        return value
    if isinstance(value, list):
        return [
            redact_sensitive(item, key_terms=key_terms, placeholder=placeholder) for item in value
        ]
    if not isinstance(value, dict):
        return value
    result: dict[str, Any] = {}
    for key, item in value.items():
        if _sensitive_key(str(key), key_terms):
            result[key] = placeholder
            continue
        result[key] = redact_sensitive(
            item,
            key_terms=key_terms,
            placeholder=placeholder,
            redact_value_shapes=redact_value_shapes,
        )
    return result


def _sensitive_key(key: str, key_terms: frozenset[str]) -> bool:
    lowered = key.lower()
    if any(term in lowered for term in key_terms):
        return True
    return classify_field(key) in (CONFIDENTIAL, RESTRICTED)


def redact_for_logs(value: Any) -> Any:
    """Redaction used before log/trace/diagnostic output.

    Drops everything the log redaction threshold implies (confidential and
    restricted fields plus secret-shaped values) while keeping operational
    fields (internal/public) readable.
    """
    return redact_sensitive(value, redact_value_shapes=True)


def redaction_snapshot() -> dict[str, Any]:
    """Inventory the effective scrub rules (used by the data-classification gate)."""
    return {
        "classifications": list(CLASSIFICATIONS),
        "rank": dict(_RANK),
        "key_terms": sorted(DEFAULT_KEY_TERMS),
        "fields": {name: info.classification for name, info in FIELD_REGISTRY.items()},
    }


# --------------------------------------------------------------------------
# Canary harness: seed a unique marker, then prove a sink never emitted it.
# --------------------------------------------------------------------------

CANARY_PREFIX = "canary"


def make_canary() -> str:
    """Build a unique marker to plant inside sensitive material during a test."""
    return f"{CANARY_PREFIX}-{uuid.uuid4().hex}"


def scan_for_canary(text: str) -> list[str]:
    """Return every canary marker present in ``text`` (empty means clean)."""
    return re.findall(rf"{CANARY_PREFIX}-[0-9a-f]{{32}}", text)


class CanaryLeakError(RuntimeError):
    """Raised when a canary marker survives a redaction path (a leak)."""


def assert_no_canary_leaks(*values: Any) -> None:
    """Fail loudly if any canary marker leaks out of the given outputs."""
    leaked: list[str] = []
    for value in values:
        if isinstance(value, str):
            leaked.extend(scan_for_canary(value))
        elif isinstance(value, (dict, list)):
            import json

            leaked.extend(scan_for_canary(json.dumps(value, ensure_ascii=False, default=str)))
    if leaked:
        raise CanaryLeakError(f"canary marker(s) leaked: {sorted(set(leaked))}")


__all__ = [
    "CLASSIFICATIONS",
    "CONFIDENTIAL",
    "FIELD_REGISTRY",
    "INTERNAL",
    "PUBLIC",
    "REDACTED",
    "RESTRICTED",
    "CanaryLeakError",
    "FieldInfo",
    "assert_no_canary_leaks",
    "classify_field",
    "make_canary",
    "redact_for_logs",
    "redact_sensitive",
    "redaction_snapshot",
    "scan_for_canary",
]
