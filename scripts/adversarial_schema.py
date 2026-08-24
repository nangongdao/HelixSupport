"""Schema validator for the adversarial evaluation set (Phase 41.5, AI-001).

The golden-set validator (``scripts/evaluate.load_golden_set``) deliberately
rejects unknown ``expect`` keys, so the adversarial set cannot reuse it: ADR-014
decision 1 extends the expectation contract with ``requires_human`` (exact),
``citation`` (exact id/title/url/version match), ``redaction`` (sensitive values
that must never appear in the assistant reply) and ``canary`` (sentinel values
that must never leak into the reply).  Every case also pins its threat category,
tenant, prompt/model/version refs and the tool set it is allowed to call, so a
pass or fail is traceable to a fixed configuration.

Failures exit non-zero (``_fail``) with a precise diagnostic, mirroring the
golden validator's strictness.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, NoReturn

# Top-level contract.
_SCHEMA = {
    "id": str,
    "description": str,
    "category": str,
    "conversation": dict,
    "expect": dict,
}

_THREAT_CATEGORIES = frozenset(
    {
        "direct_prompt_injection",
        "indirect_prompt_injection",
        "system_prompt_probing",
        "cross_tenant_retrieval",
        "tool_parameter_injection",
        "pii_secret_exfiltration",
        "malicious_attachment_text",
        "multilingual_variant",
        "policy_suppression",
    }
)

# Per-case pinned metadata required by ADR-014 decision 1.  ``prompt_version``
# is the registry version string the harness registers via
# ``_register_prompt_version``; ``model_ref`` is the pinned provider model;
# ``tenant_id`` defaults to the demo tenant when absent; ``allowed_tools`` is
# the closed set the case may invoke.
_CASE_META_KEYS = frozenset({"prompt_version", "model_ref", "allowed_tools", "tenant_id"})

# Case inputs the harness consumes — single-turn ``message`` or multi-turn
# ``messages`` — plus seeding directives (ADR-014 decision 1 channels): a
# knowledge-article write for indirect injection, an attachment upload for
# malicious-attachment cases, and a canary sentinel injected into the
# conversation text.
_SEED_KEYS = frozenset(
    {
        "knowledge_seed",
        "attachment_seed",
        "canary_sentinel",
        "messages",
        "message",
    }
)

_CONVERSATION_KEYS = frozenset({"customer_ref", "channel"})

# Expect keys shared with the golden set plus the ADR-014 extension.
_EXPECT_KEYS = frozenset(
    {
        "agent",
        "status",
        "priority",
        "citations",
        "quality_approved",
        "tool_code",
        "in_content",
        "not_in_content",
        "risk_categories",
        # ADR-014 decision 1 extension.
        "requires_human",
        "citation",  # exact citation: {id, title, url, version}
        "redaction",  # list of sensitive values that must NOT appear anywhere
        "canary",  # sentinel values (e.g. make_canary()) that must NOT leak
        "tool_calls",  # exact set of tool names that must have been called
        "canary_assert",  # sentinel-leak assertions {not_in_reply, not_in_metadata}
    }
)

_REQUIRED_EXPECT_KEYS = frozenset({"status"})


def _fail(message: str) -> NoReturn:
    sys.exit(f"error: {message}")


def load_adversarial_set(path: Path) -> list[dict[str, Any]]:
    """Load and validate the adversarial set, returning the case list."""
    if not path.exists():
        _fail(f"adversarial set not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _fail(f"adversarial set is not valid JSON: {exc}")
    if not isinstance(data, dict) or data.get("version") != 1:
        _fail("adversarial set must have version: 1")
    cases = data.get("cases")
    if not isinstance(cases, list) or not cases:
        _fail("adversarial set must contain a non-empty cases list")

    seen_ids: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            _fail("each adversarial case must be an object")
        for key, expected_type in _SCHEMA.items():
            if key not in case:
                _fail(f"adversarial case is missing '{key}': {case.get('id', '<no id>')}")
            if not isinstance(case[key], expected_type):
                _fail(f"adversarial case '{case.get('id')}' field '{key}' has wrong type")
        case_id = case["id"]
        if case_id in seen_ids:
            _fail(f"duplicate adversarial case id: {case_id}")
        seen_ids.add(case_id)

        category = case["category"]
        if category not in _THREAT_CATEGORIES:
            _fail(
                f"adversarial case '{case_id}' has unknown category {category!r}; "
                f"allowed: {sorted(_THREAT_CATEGORIES)}"
            )

        unknown_meta = set(case) - _SCHEMA.keys() - _CASE_META_KEYS - _SEED_KEYS
        if unknown_meta:
            _fail(f"adversarial case '{case_id}' has unknown keys: {sorted(unknown_meta)}")

        message = case.get("message")
        if message is not None and not isinstance(message, str):
            _fail(f"adversarial case '{case_id}' message must be a string")
        if not message and not case.get("messages"):
            _fail(f"adversarial case '{case_id}' must have message or messages")
        if "messages" in case:
            messages = case["messages"]
            if not isinstance(messages, list) or not messages:
                _fail(f"adversarial case '{case_id}' messages must be a non-empty list")
            if not all(isinstance(m, str) for m in messages):
                _fail(f"adversarial case '{case_id}' messages must all be strings")
        if case.get("message") and "messages" in case:
            _fail(f"adversarial case '{case_id}' cannot have both message and messages")

        knowledge_seed = case.get("knowledge_seed")
        if knowledge_seed is not None and not isinstance(knowledge_seed, dict):
            _fail(f"adversarial case '{case_id}' knowledge_seed must be an object")
        attachment_seed = case.get("attachment_seed")
        if attachment_seed is not None and not isinstance(attachment_seed, dict):
            _fail(f"adversarial case '{case_id}' attachment_seed must be an object")

        conversation = case["conversation"]
        if set(conversation) - _CONVERSATION_KEYS:
            _fail(f"adversarial case '{case_id}' has unknown conversation keys")
        if not isinstance(conversation.get("customer_ref"), (str, type(None))):
            _fail(f"adversarial case '{case_id}' customer_ref must be a string or null")
        if not isinstance(conversation.get("channel"), str):
            _fail(f"adversarial case '{case_id}' channel must be a string")

        tenant_id = case.get("tenant_id")
        if tenant_id is not None and not isinstance(tenant_id, str):
            _fail(f"adversarial case '{case_id}' tenant_id must be a string")
        for key in ("prompt_version", "model_ref"):
            value = case.get(key)
            if value is None or not isinstance(value, str):
                _fail(f"adversarial case '{case_id}' must pin {key}")
        allowed = case.get("allowed_tools")
        # The closed tool set may legitimately be empty (no tool may run).
        if not isinstance(allowed, list):
            _fail(f"adversarial case '{case_id}' allowed_tools must be a list")
        if not all(isinstance(t, str) for t in allowed):
            _fail(f"adversarial case '{case_id}' allowed_tools must all be strings")

        expect = case["expect"]
        unknown_expect = set(expect) - _EXPECT_KEYS
        if unknown_expect:
            _fail(f"adversarial case '{case_id}' has unknown expect keys: {sorted(unknown_expect)}")
        missing_expect = _REQUIRED_EXPECT_KEYS - set(expect)
        if missing_expect:
            _fail(
                f"adversarial case '{case_id}' is missing required expect keys: {sorted(missing_expect)}"
            )
        if "in_content" in expect and "not_in_content" in expect:
            _fail(f"adversarial case '{case_id}' cannot expect both in_content and not_in_content")

        citation = expect.get("citation")
        if citation is not None:
            if not isinstance(citation, dict):
                _fail(f"adversarial case '{case_id}' citation must be an exact-match dict")
            # ADR-014 decision 1: the exact-match contract is id-prefix +
            # title/url/version equality; an empty/missing/non-string id would
            # vacuously match at runtime and must be rejected here instead.
            for key in ("id", "title", "url", "version"):
                value = citation.get(key)
                if not isinstance(value, str) or not value:
                    _fail(f"adversarial case '{case_id}' citation.{key} must be a non-empty string")
        for key in ("redaction", "canary"):
            values = expect.get(key)
            if values is not None and not isinstance(values, list):
                _fail(f"adversarial case '{case_id}' expect.{key} must be a list")
            if values is not None and not all(isinstance(v, str) for v in values):
                _fail(f"adversarial case '{case_id}' expect.{key} must all be strings")

        canary_assert = expect.get("canary_assert")
        if canary_assert is not None:
            if not isinstance(canary_assert, dict):
                _fail(f"adversarial case '{case_id}' canary_assert must be an object")
            unknown = set(canary_assert) - {"not_in_reply", "not_in_metadata"}
            if unknown:
                _fail(
                    f"adversarial case '{case_id}' canary_assert has unknown keys: "
                    f"{sorted(unknown)}; allowed: ['not_in_reply', 'not_in_metadata']"
                )
            if not all(isinstance(v, bool) for v in canary_assert.values()):
                _fail(f"adversarial case '{case_id}' canary_assert values must be booleans")

        tool_calls = expect.get("tool_calls")
        if tool_calls is not None:
            if not isinstance(tool_calls, list) or not tool_calls:
                _fail(f"adversarial case '{case_id}' tool_calls must be a non-empty list")
            if not all(isinstance(call, str) for call in tool_calls):
                _fail(f"adversarial case '{case_id}' tool_calls must all be tool names")

        # Cross-tenant cases must pin a tenant different from the demo tenant
        # so the harness knows which principal to authenticate with.
        if category == "cross_tenant_retrieval" and tenant_id in (None, "demo"):
            _fail(f"adversarial case '{case_id}' cross_tenant_retrieval must pin tenant_id != demo")

        # Redaction/canary are assertions about the reply; they are meaningless
        # unless the case reaches an assistant reply at all.
        if (expect.get("redaction") or expect.get("canary")) and expect.get("status") == "blocked":
            _fail(f"adversarial case '{case_id}' redaction/canary cannot assert on a blocked reply")
    return cases


__all__ = ["load_adversarial_set"]
