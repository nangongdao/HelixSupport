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


def _make_deref(spec: dict):
    """Build a ``$ref``-resolving normalizer for one spec.

    Recursion is bounded by ``seen``: FastAPI emits self-referential schemas
    (a tree node whose ``children`` are the same model), and an unguarded
    resolver recurses until the stack blows. A ref already being expanded is
    left as a marker instead — the same marker on both sides compares equal,
    so a cycle is inert rather than fatal.
    """
    schemas = spec.get("components", {}).get("schemas", {})

    def deref(node: object, seen: frozenset[str] = frozenset()) -> object:
        if isinstance(node, list):
            return [deref(item, seen) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            name = ref.rsplit("/", 1)[-1]
            if name in seen:
                return {"$recursive": name}
            return deref(schemas.get(name, {"$ref": ref}), seen | {name})
        result: dict = {}
        for key, value in node.items():
            # `title` is FastAPI-generated prose ("Response Get Items"), not
            # part of the contract; it churns on every rename.
            if key in {"title", "description", "example", "examples"}:
                continue
            if key == "properties" and isinstance(value, dict):
                result[key] = {prop: deref(sub, seen) for prop, sub in value.items()}
            elif isinstance(value, (dict, list)):
                # Covers items / anyOf / allOf / additionalProperties / $defs
                # uniformly: array element schemas and nested objects were
                # previously left as raw $refs, so a field removed inside a
                # list response or one level down was invisible.
                result[key] = deref(value, seen)
            else:
                result[key] = value
        return result

    return deref


def _operation_schema(spec: dict) -> dict[str, dict]:
    """Resolve each operation's response schemas to concrete shapes.

    Returns {METHOD path: {"200:application/json": {...}, ...}} so the
    comparison can detect removed fields or changed types without chasing
    $refs in the caller. A response with no ``content`` (204, and any error
    status FastAPI declares bare) is recorded as an empty shape so its removal
    is still detected.
    """
    deref = _make_deref(spec)
    result: dict[str, dict] = {}
    for key, operation in _operations(spec).items():
        responses = operation.get("responses", {})
        shapes: dict[str, dict] = {}
        for status, resp in responses.items():
            if not isinstance(resp, dict):
                continue
            content = resp.get("content") or {}
            for media, media_spec in content.items():
                schema = deref((media_spec or {}).get("schema", {}))
                shapes[f"{status}:{media}"] = schema if isinstance(schema, dict) else {}
                break
            else:
                # `if not shapes` tested the accumulated dict, so a bare 204
                # following a 200 was silently dropped and its removal was
                # undetectable. Keyed per status, it is not.
                shapes[status] = {}
        result[key] = shapes
    return result


def _operation_params(spec: dict) -> dict[str, dict[str, dict]]:
    """Resolve each operation's parameters to {"in:name": {...}}.

    Parameters were never compared at all: deleting a required query parameter
    was reported as no change, even though every client that omits it now gets
    a 422. 133 of 141 live operations declare parameters.
    """
    deref = _make_deref(spec)
    result: dict[str, dict[str, dict]] = {}
    for key, operation in _operations(spec).items():
        params: dict[str, dict] = {}
        for raw in operation.get("parameters") or []:
            resolved = deref(raw)
            if not isinstance(resolved, dict):
                continue
            name = resolved.get("name")
            if not isinstance(name, str):
                continue
            params[f"{resolved.get('in', 'query')}:{name}"] = resolved
        result[key] = params
    return result


def _operation_bodies(spec: dict) -> dict[str, dict]:
    """Resolve each operation's requestBody to a comparable shape.

    48 live operations accept a body; none of it was compared, so a newly
    required field — which rejects every existing caller — was invisible.
    """
    deref = _make_deref(spec)
    result: dict[str, dict] = {}
    for key, operation in _operations(spec).items():
        body = operation.get("requestBody")
        if not isinstance(body, dict):
            continue
        resolved = deref(body)
        if isinstance(resolved, dict):
            result[key] = resolved
    return result


def _compare_shape(old: object, new: object, where: str) -> list[str]:
    """Walk two resolved schemas in step, reporting response-side breakage.

    Recurses through ``properties``, ``items``, and the ``anyOf``/``allOf``
    branches so a field removed from an array element — 31 of 141 live
    operations return arrays — or from a nested object is reported with the
    path that lost it. The old implementation only read top-level
    ``properties``, and an array schema has none of its own.
    """
    if not isinstance(old, dict) or not isinstance(new, dict):
        return []
    problems: list[str] = []

    old_type, new_type = old.get("type"), new.get("type")
    if old_type and new_type and old_type != new_type:
        problems.append(f"{where} type changed {old_type} -> {new_type}")

    removed_required = set(old.get("required") or []) - set(new.get("required") or [])
    if removed_required:
        problems.append(f"{where} removed required fields {sorted(removed_required)}")

    old_props = old.get("properties") or {}
    new_props = new.get("properties") or {}
    if isinstance(old_props, dict) and isinstance(new_props, dict):
        removed = set(old_props) - set(new_props)
        if removed:
            problems.append(f"{where} removed properties {sorted(removed)}")
        for prop in sorted(set(old_props) & set(new_props)):
            problems += _compare_shape(old_props[prop], new_props[prop], f"{where}.{prop}")

    if isinstance(old.get("items"), dict) and isinstance(new.get("items"), dict):
        problems += _compare_shape(old["items"], new["items"], f"{where}[]")

    for combinator in ("anyOf", "allOf", "oneOf"):
        old_branches, new_branches = old.get(combinator), new.get(combinator)
        if not isinstance(old_branches, list) or not isinstance(new_branches, list):
            continue
        if len(old_branches) != len(new_branches):
            problems.append(
                f"{where} {combinator} branches changed {len(old_branches)} -> {len(new_branches)}"
            )
            continue
        for index, (old_branch, new_branch) in enumerate(zip(old_branches, new_branches)):
            problems += _compare_shape(old_branch, new_branch, f"{where}.{combinator}[{index}]")

    return problems


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
        if key not in current_ops:
            continue  # already reported as a removed endpoint
        new_shapes = current_schema.get(key, {})
        for status, old_shape in old_shapes.items():
            new_shape = new_shapes.get(status)
            if new_shape is None:
                problems.append(f"{key}: removed response {status}")
                continue
            problems += _compare_shape(old_shape, new_shape, f"{key}: {status}")

    # Parameters: a removed or newly-required parameter breaks live callers.
    baseline_params = _operation_params(baseline)
    current_params = _operation_params(current)
    for key, old_params in baseline_params.items():
        if key not in current_ops:
            continue
        new_params = current_params.get(key, {})
        removed = set(old_params) - set(new_params)
        if removed:
            problems.append(f"{key}: removed parameters {sorted(removed)}")
        for name in sorted(set(old_params) & set(new_params)):
            old_param, new_param = old_params[name], new_params[name]
            if not old_param.get("required") and new_param.get("required"):
                problems.append(f"{key}: parameter {name} became required")
            problems += _compare_shape(
                old_param.get("schema"), new_param.get("schema"), f"{key}: parameter {name}"
            )
    for key, new_params in current_params.items():
        if key not in baseline_ops:
            continue  # a new endpoint is additive; its parameters are too
        old_params = baseline_params.get(key, {})
        newly_required = [
            name
            for name in sorted(set(new_params) - set(old_params))
            if new_params[name].get("required")
        ]
        if newly_required:
            problems.append(f"{key}: new required parameters {newly_required}")

    # Request bodies: a newly-required body or field rejects existing callers.
    baseline_bodies = _operation_bodies(baseline)
    current_bodies = _operation_bodies(current)
    for key, old_body in baseline_bodies.items():
        if key not in current_ops:
            continue
        new_body = current_bodies.get(key)
        if new_body is None:
            problems.append(f"{key}: removed requestBody")
            continue
        for media, old_media in (old_body.get("content") or {}).items():
            new_media = (new_body.get("content") or {}).get(media)
            if new_media is None:
                problems.append(f"{key}: requestBody dropped media type {media}")
                continue
            problems += _compare_shape(
                (old_media or {}).get("schema"),
                (new_media or {}).get("schema"),
                f"{key}: requestBody {media}",
            )
            old_required = set((old_media or {}).get("schema", {}).get("required") or [])
            new_required = set((new_media or {}).get("schema", {}).get("required") or [])
            became_required = new_required - old_required
            if became_required:
                problems.append(
                    f"{key}: requestBody {media} new required fields {sorted(became_required)}"
                )
    for key, new_body in current_bodies.items():
        if key in baseline_ops and key not in baseline_bodies and new_body.get("required"):
            problems.append(f"{key}: requestBody became required")

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
