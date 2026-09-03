"""Generate a static Markdown API reference from the OpenAPI snapshot (Phase 25.5).

Reads ``api/openapi.json`` (the committed contract snapshot) and writes
``docs/api/reference.md`` with every endpoint grouped by tag, including
parameters, request bodies, and response schemas. The integration guides
(auth, idempotency, pagination, streaming, webhook verification) live as
hand-written sibling docs.

Usage:
    python scripts/api_docs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT))
from scripts._console import use_utf8_console

SNAPSHOT = ROOT / "api" / "openapi.json"
OUTPUT = ROOT / "docs" / "api" / "reference.md"

_METHOD_COLORS = {
    "get": "green",
    "post": "blue",
    "put": "orange",
    "patch": "purple",
    "delete": "red",
}


def _deref(schema: dict, schemas: dict) -> dict:
    if not isinstance(schema, dict):
        return schema
    if "$ref" in schema:
        name = schema["$ref"].rsplit("/", 1)[-1]
        return _deref(schemas.get(name, {"$ref": schema["$ref"]}), schemas)
    return {k: _deref(v, schemas) if k != "title" else v for k, v in schema.items()}


def _schema_text(schema: dict | None, schemas: dict, indent: int = 0) -> str:
    if not schema:
        return ""
    schema = _deref(schema, schemas)
    pad = "  " * indent
    lines: list[str] = []
    if schema.get("type") == "array":
        items = _deref(schema.get("items", {}), schemas)
        if "properties" in items:
            lines.append(f"{pad}array of:")
            lines.append(_schema_text(items, schemas, indent + 1))
        else:
            lines.append(f"{pad}array of {items.get('type', 'object')}")
        return "\n".join(lines)
    props = schema.get("properties")
    if props:
        required = set(schema.get("required", []) or [])
        lines.append(f"{pad}{{")
        for name, prop in props.items():
            prop = _deref(prop, schemas)
            ptype = prop.get("type", "object")
            req = " (required)" if name in required else ""
            lines.append(f"{pad}  {name}: {ptype}{req}")
            if "properties" in prop:
                lines.append(_schema_text(prop, schemas, indent + 2))
        lines.append(f"{pad}}}")
    else:
        lines.append(f"{pad}{schema.get('type', 'object')}")
    return "\n".join(lines)


def _operation_md(method: str, path: str, operation: dict, schemas: dict) -> str:
    parts: list[str] = []
    parts.append(f"### {method.upper()} `{path}`")
    parts.append("")
    parts.append(f"**{operation.get('summary', '')}**")
    if operation.get("description"):
        parts.append("")
        parts.append(operation["description"])
    tags = operation.get("tags") or []
    if tags:
        parts.append("")
        parts.append(f"*Tags:* {', '.join(f'`{t}`' for t in tags)}")
    params = operation.get("parameters") or []
    if params:
        parts.append("")
        parts.append("**Parameters**")
        parts.append("")
        parts.append("| Name | In | Required | Description |")
        parts.append("|------|----|----------|-------------|")
        for param in params:
            parts.append(
                f"| `{param.get('name')}` | {param.get('in')} | "
                f"{'yes' if param.get('required') else 'no'} | "
                f"{param.get('description', '')} |"
            )
    request_body = operation.get("requestBody")
    if request_body:
        parts.append("")
        parts.append("**Request body**")
        content = request_body.get("content", {})
        for media, media_spec in content.items():
            parts.append("")
            parts.append(f"`{media}`")
            parts.append("")
            parts.append(_schema_text(media_spec.get("schema"), schemas))
    responses = operation.get("responses", {})
    if responses:
        parts.append("")
        parts.append("**Responses**")
        for status in sorted(responses, key=lambda s: (not s.isdigit(), s)):
            response = responses[status]
            parts.append("")
            parts.append(f"- `{status}` {response.get('description', '')}")
            content = response.get("content", {})
            for media, media_spec in content.items():
                parts.append("")
                parts.append(f"  `{media}`")
                parts.append("")
                text = _schema_text(media_spec.get("schema"), schemas)
                parts.append("\n".join(f"  {line}" for line in text.splitlines()))
    return "\n".join(parts)


def generate() -> str:
    spec = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    schemas = spec.get("components", {}).get("schemas", {})
    paths = spec.get("paths", {})
    info = spec.get("info", {})

    lines: list[str] = [
        "# Helix Support API Reference",
        "",
        f"Version: `{info.get('version', 'unknown')}`",
        "",
        ("This reference is generated from the OpenAPI contract snapshot "
        "(`api/openapi.json`) by `scripts/api_docs.py`. The error contract is "
        "documented in [ERRORS.md](../ERRORS.md); versioning and deprecation "
        "policy in [API_POLICY.md](../API_POLICY.md)."),
        "",
    ]
    # Group by first tag.
    grouped: dict[str, list[tuple[str, str, dict]]] = {}
    for path, methods in paths.items():
        for method, operation in methods.items():
            if method not in {"get", "post", "put", "patch", "delete"}:
                continue
            tag = (operation.get("tags") or ["general"])[0]
            grouped.setdefault(tag, []).append((method, path, operation))
    for tag in sorted(grouped):
        lines.append(f"## {tag.capitalize()}")
        lines.append("")
        for method, path, operation in sorted(grouped[tag], key=lambda item: item[1]):
            lines.append(_operation_md(method, path, operation, schemas))
            lines.append("")
    return "\n".join(lines)


def main() -> int:
    if not SNAPSHOT.exists():
        print(f"snapshot missing: {SNAPSHOT}", file=sys.stderr)
        return 2
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(generate(), encoding="utf-8")
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    use_utf8_console()
    sys.exit(main())
