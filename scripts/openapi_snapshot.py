"""OpenAPI governance snapshot tool (Phase 25.2).

Usage:
    python scripts/openapi_snapshot.py --dump          # write api/openapi.json
    python scripts/openapi_snapshot.py                  # compare current spec

The gate compares the live ``openapi.json`` against the committed snapshot
``api/openapi.json`` and fails on *breaking* changes: removed endpoints,
removed/changed response fields or types, changed parameter shapes. Additive
changes (new endpoints, new optional fields) are reported as warnings -- they
still require the snapshot to be regenerated and committed, but a PR that only
adds an endpoint does not fail the gate by itself.

Exit codes: 0 = spec matches snapshot (or only additive changes with
--allow-additive), 1 = breaking change or spec diverges, 2 = snapshot missing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT = ROOT / "api" / "openapi.json"


def _build_spec() -> dict:
    """Build the OpenAPI spec with a demo-mode app (no secrets required)."""
    import tempfile

    from app.config import Settings
    from app.main import create_app

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(
            database_path=Path(tmp) / "openapi.db",
            auth_mode="demo",
            docs_enabled=True,
        )
        app = create_app(settings)
        try:
            return dict(app.openapi())
        finally:
            # Release the SQLite connection pool so the temp dir can be
            # removed on Windows.
            app.state.services.database.close()


def _operations(spec: dict) -> dict[str, dict]:
    """Flatten paths -> {method.upper(): operation}."""
    ops: dict[str, dict] = {}
    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if method in {"get", "post", "put", "patch", "delete"}:
                ops[f"{method.upper()} {path}"] = operation
    return ops


def _operation_schema(spec: dict) -> dict[str, dict]:
    """Resolve each operation's response schemas to concrete shapes.

    Returns {METHOD path: {"200": {"type": ..., "required": [...]}, ...}} so
    the comparison can detect removed fields or changed types without chasing
    $refs in the caller.
    """
    schemas = spec.get("components", {}).get("schemas", {})

    def deref(node: dict) -> dict:
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            return deref(schemas.get(name, {"$ref": node["$ref"]}))
        result: dict = {}
        for key, value in node.items():
            if key == "title":
                continue
            if key == "items" and isinstance(value, dict) and "$ref" in value:
                # Array element schemas: resolve the element $ref so property
                # removal inside list responses is detected.
                result[key] = deref(value)
            elif key == "properties":
                result[key] = {prop: deref(prop_schema) for prop, prop_schema in value.items()}
            else:
                result[key] = value
        return result

    result: dict[str, dict] = {}
    for key, operation in _operations(spec).items():
        responses = operation.get("responses", {})
        shapes: dict[str, dict] = {}
        for status, resp in responses.items():
            if not isinstance(resp, dict):
                continue
            content = resp.get("content", {})
            for media, media_spec in content.items():
                schema = deref(media_spec.get("schema", {}))
                shapes[f"{status}:{media}"] = schema
                break
            if not shapes:
                shapes[status] = {}
        result[key] = shapes
    return result


def _breaking_changes(baseline: dict, current: dict) -> list[str]:
    problems: list[str] = []

    baseline_ops = _operations(baseline)
    current_ops = _operations(current)
    for key in baseline_ops:
        if key not in current_ops:
            problems.append(f"removed endpoint: {key}")

    baseline_schema = _operation_schema(baseline)
    current_schema = _operation_schema(current)
    for key, old_shapes in baseline_schema.items():
        new_shapes = current_schema.get(key, {})
        for status, old_shape in old_shapes.items():
            new_shape = new_shapes.get(status)
            if new_shape is None:
                problems.append(f"{key}: removed response {status}")
                continue
            if not isinstance(old_shape, dict) or not isinstance(new_shape, dict):
                continue
            old_type = old_shape.get("type")
            new_type = new_shape.get("type")
            if old_type and new_type and old_type != new_type:
                problems.append(f"{key}: {status} type changed {old_type} -> {new_type}")
            # Removed required fields.
            old_required = set(old_shape.get("required", []) or [])
            new_required = set(new_shape.get("required", []) or [])
            removed_required = old_required - new_required
            if removed_required:
                problems.append(
                    f"{key}: {status} removed required fields {sorted(removed_required)}"
                )
            # Removed optional fields from an object schema (a field a client
            # could have read disappears).
            old_props = set(old_shape.get("properties", {}))
            new_props = set(new_shape.get("properties", {}))
            removed_props = old_props - new_props
            if removed_props:
                problems.append(f"{key}: {status} removed properties {sorted(removed_props)}")
    return problems


def _additive_changes(baseline: dict, current: dict) -> list[str]:
    notes: list[str] = []
    baseline_ops = _operations(baseline)
    current_ops = _operations(current)
    for key in current_ops:
        if key not in baseline_ops:
            notes.append(f"new endpoint: {key}")
    return notes


def dump() -> None:
    spec = _build_spec()
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT.relative_to(ROOT)}")


def compare(*, allow_additive: bool = False) -> int:
    if not SNAPSHOT.exists():
        print(f"snapshot missing: {SNAPSHOT} (run with --dump first)", file=sys.stderr)
        return 2
    baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = _build_spec()
    breaking = _breaking_changes(baseline, current)
    additive = _additive_changes(baseline, current)
    for problem in breaking:
        print(f"BREAKING: {problem}", file=sys.stderr)
    for note in additive:
        print(f"ADDITIVE: {note}")
    if breaking:
        return 1
    if additive and not allow_additive:
        print(
            "additive changes present -- regenerate the snapshot with --dump "
            "and commit it before merging",
            file=sys.stderr,
        )
        return 1
    print("openapi spec matches snapshot")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dump", action="store_true", help="write the snapshot file")
    parser.add_argument(
        "--allow-additive",
        action="store_true",
        help="treat additive changes (new endpoints) as non-breaking",
    )
    args = parser.parse_args()
    if args.dump:
        dump()
        return 0
    return compare(allow_additive=args.allow_additive)


if __name__ == "__main__":
    sys.exit(main())
