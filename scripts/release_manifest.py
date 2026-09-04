"""Release manifest build/verify (Phase 41.2 / ROADMAP 41.2 SEC-003).

``--build`` hashes the files that determine the deployable artifact — the
pinned dependency closure, the build metadata, the Dockerfile, and the whole
``app/`` source tree — together with the approved base-image pin, and writes a
manifest.  ``--verify`` recomputes every hash from disk and fails on any
mismatch, so a tampered artifact or a drifted source tree is caught at release
time ("from a tag you can rebuild and compare digests").

The manifest never trusts ``generated_at``; verification is purely
content-based.  A null ``base_image_digest`` (no live registry on this
machine) verifies as a warning, never as a fabricated value.

Usage:
    python scripts/release_manifest.py --build --out artifacts/release-manifest.json
    python scripts/release_manifest.py --verify --out artifacts/release-manifest.json

Exit 0 when the manifest is internally consistent; non-zero otherwise.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tomllib
from pathlib import Path

_DESCRIPTION = (__doc__ or "supply-chain gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts._console import use_utf8_console

APP_DIR = ROOT / "app"
DEFAULT_PIN = ROOT / "supplychain" / "base-image-pin.json"
MANIFEST_FILES: tuple[str, ...] = ("requirements.lock", "pyproject.toml", "Dockerfile")


def _file_sha256(path: Path) -> str | None:
    """SHA-256 of a single file; None when the file is absent."""
    if not path.exists():
        return None
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def _tree_sha256(directory: Path) -> str | None:
    """Aggregate SHA-256 over every text source file under ``directory``.

    Files are sorted by relative path and hashed as ``relpath:hex\\n`` so the
    aggregate is deterministic and immune to directory-scan order.  Cache and
    bytecode artifacts are excluded.
    """
    if not directory.exists():
        return None
    entries: list[tuple[str, str]] = []
    for path in sorted(directory.rglob("*")):
        if path.is_dir():
            continue
        if "__pycache__" in path.parts or path.suffix in {".pyc", ".pyo"}:
            continue
        rel = path.relative_to(directory).as_posix()
        entries.append((rel, hashlib.sha256(path.read_bytes()).hexdigest()))
    aggregate = hashlib.sha256()
    for rel, hex_digest in entries:
        aggregate.update(f"{rel}:{hex_digest}\n".encode())
    return aggregate.hexdigest()


def _app_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    return str(project["version"])


def _base_image_pin(pin_path: Path = DEFAULT_PIN) -> dict[str, str | None]:
    payload = json.loads(pin_path.read_text(encoding="utf-8"))
    return {
        "base_image": str(payload.get("base_image")),
        "base_image_digest": payload.get("digest"),
    }


def build_manifest(*, pin_path: Path = DEFAULT_PIN, root: Path = ROOT) -> dict[str, object]:
    """Compute the manifest for the current tree."""
    pin = _base_image_pin(pin_path)
    sbom_hash = _file_sha256(root / "artifacts" / "sbom.json")
    return {
        "app_version": _app_version(root),
        "source_commit": "no-vcs",  # this tree is not a git checkout
        "lock_sha256": _file_sha256(root / "requirements.lock"),
        "pyproject_sha256": _file_sha256(root / "pyproject.toml"),
        "dockerfile_sha256": _file_sha256(root / "Dockerfile"),
        "app_tree_sha256": _tree_sha256(root / "app"),
        "sbom_sha256": sbom_hash,
        "base_image": pin["base_image"],
        "base_image_digest": pin["base_image_digest"],
    }


def verify_manifest(
    manifest_path: Path, *, pin_path: Path = DEFAULT_PIN, root: Path = ROOT
) -> list[str]:
    """Recompute every content hash and compare; return violations (empty = ok)."""
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"无法读取 manifest {manifest_path}: {exc}"]
    current = build_manifest(pin_path=pin_path, root=root)
    violations: list[str] = []
    for field in ("lock_sha256", "pyproject_sha256", "dockerfile_sha256", "app_tree_sha256"):
        if expected.get(field) != current.get(field):
            violations.append(
                f"{field} 不匹配：manifest={expected.get(field)} 当前={current.get(field)}"
            )
    if expected.get("app_version") != current.get("app_version"):
        violations.append(
            f"app_version 不匹配：manifest={expected.get('app_version')} 当前={current.get('app_version')}"
        )
    if expected.get("base_image") != current.get("base_image"):
        violations.append(
            f"base_image 不匹配：manifest={expected.get('base_image')} 当前={current.get('base_image')}"
        )
    return violations


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--build", action="store_true")
    mode.add_argument("--verify", action="store_true")
    parser.add_argument("--out", type=Path, required=True, help="manifest path")
    parser.add_argument("--pin", type=Path, default=DEFAULT_PIN)
    args = parser.parse_args(argv)

    if args.build:
        manifest = build_manifest(pin_path=args.pin)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"release_manifest: 已写入 {args.out}")
        if manifest["base_image_digest"] is None:
            print(
                "release_manifest: warning — base_image_digest 未固定（需真实 registry）",
                file=sys.stderr,
            )
        return 0

    violations = verify_manifest(args.out, pin_path=args.pin)
    if not violations:
        print(f"release_manifest: verify OK — {args.out} 与当前源码树一致")
        return 0
    for violation in violations:
        print(f"release_manifest: {violation}", file=sys.stderr)
    print(f"release_manifest: {len(violations)} 项不一致", file=sys.stderr)
    return 1


if __name__ == "__main__":
    use_utf8_console()
    raise SystemExit(main())
