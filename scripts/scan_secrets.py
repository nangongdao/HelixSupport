"""Repository secret scanner (Phase 41.2 / ROADMAP 41.2 SEC-003).

Scans the tracked source tree for credentials that must never be committed:
private keys, well-known vendor token formats (Anthropic / OpenAI / AWS /
GitHub / Slack), Google service-account material and Fernet-shaped secrets.
Gitignored material, databases, build artifacts, caches and the node_modules
tree are skipped so the gate does not fight local tooling state.

The matchers are deliberately narrow — a committed secret must match a
*known* credential shape, not a generic ``key=`` assignment — so the gate
stays useful without flagging header names (``X-API-Key``), test fixtures or
config keys that merely reference secrets by name.

Usage:
    python scripts/scan_secrets.py [--root PATH]

Exit 0 when no candidate is found; non-zero prints every
``(path, pattern, line)`` hit and lists them on stderr.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_DESCRIPTION = (__doc__ or "supply-chain gate").strip().splitlines()[0]

ROOT = Path(__file__).resolve().parent.parent

#: Top-level entries whose contents are never scanned (build/local state).
#: ``tests`` is excluded because fixtures legitimately distribute mock
#: credentials that match vendor token shapes; tests never reach a published
#: artifact (the wheel ships only ``app`` and the Docker build excludes
#: ``tests``), so scanning them would produce only false positives.
IGNORED_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        ".claude",
        ".mypy_cache",
        ".nezha",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "artifacts",
        "build",
        "data",
        "dist",
        "node_modules",
        "tests",
    }
)

#: File-name suffixes skipped regardless of location.
IGNORED_SUFFIXES: frozenset[str] = frozenset(
    {
        ".db",
        ".db-shm",
        ".db-wal",
        ".egg-info",
        ".ico",
        ".jpg",
        ".jpeg",
        ".lock",
        ".pdf",
        ".png",
        ".pyc",
        ".webp",
    }
)

#: Whole files skipped regardless of location.  npm lockfiles carry public
#: registry integrity hashes (``"integrity": "sha512-..."``) that are not
#: secrets and otherwise trip the Fernet-shaped matcher.
IGNORED_FILE_NAMES: frozenset[str] = frozenset({"package-lock.json", "npm-shrinkwrap.json"})

#: Hard credential shapes.  Each hit is a real leak candidate — matching code
#: against one of these means a credential-formatted value reached a file.
SECRET_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "private-key",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"),
    ),
    ("anthropic-key", re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}")),
    ("openai-key", re.compile(r"\bsk-[A-Za-z0-9]{32,}\b")),
    ("aws-access-key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github-pat", re.compile(r"\bghp_[A-Za-z0-9]{36}\b")),
    ("github-app-token", re.compile(r"\bghs_[A-Za-z0-9]{36}\b")),
    ("slack-token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    ("google-service-account", re.compile(r'"type"\s*:\s*"service_account"')),
    # base64 of a 32-byte key (e.g. Fernet / AES-256): 43 chars + padding '='.
    ("fernet-key", re.compile(r"\b[A-Za-z0-9+/]{43}=")),
]


def _ignore(entry: Path, root: Path) -> bool:
    if entry.name in IGNORED_DIR_NAMES:
        return True
    if entry.name in IGNORED_FILE_NAMES:
        return True
    return any(entry.name.endswith(suffix) for suffix in IGNORED_SUFFIXES)


def scan(root: Path = ROOT) -> list[tuple[str, str, int, str]]:
    """Walk ``root`` and return ``(relpath, pattern, line_no, line_text)`` hits."""
    hits: list[tuple[str, str, int, str]] = []
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(_ignore(part, root) for part in rel.parents) or _ignore(path, root):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0 or size > 1_000_000:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for line_no, line in enumerate(text.splitlines(), start=1):
            for pattern_name, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    hits.append((rel.as_posix(), pattern_name, line_no, line.strip()))
                    break
    return hits


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=_DESCRIPTION)
    parser.add_argument("--root", type=Path, default=ROOT, help="repository root")
    args = parser.parse_args(argv)

    hits = scan(args.root)
    if not hits:
        print(f"scan_secrets: clean ({len(SECRET_PATTERNS)} patterns, no hits)")
        return 0
    for relpath, pattern_name, line_no, line_text in hits:
        print(f"{relpath}:{line_no} [{pattern_name}]: {line_text}", file=sys.stderr)
    print(f"scan_secrets: {len(hits)} potential credential(s) found", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
