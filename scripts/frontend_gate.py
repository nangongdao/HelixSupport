"""Frontend quality gate (Phase 26.4).

Runs the frontend engineering checks that CI enforces without adding build
dependencies:

1. Every ES module under ``app/static/js`` must pass ``node --check``
   (syntax gate).
2. No module may exceed ``MAX_LINES`` (default 400) — the Phase 26
   acceptance limit.
3. The Node test runner suite under ``tests/frontend`` must pass and report
   at least ``MIN_TESTS`` (default 30) tests — the Phase 26 acceptance
   threshold.

Usage:
    python scripts/frontend_gate.py
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from app.assets import STATIC_ASSET_VERSION

ROOT = Path(__file__).resolve().parent.parent
JS_DIR = ROOT / "app" / "static" / "js"
TEST_DIR = ROOT / "tests" / "frontend"
MAX_LINES = 400
MIN_TESTS = 30
STATIC_REF_RE = re.compile(r"/static/[A-Za-z0-9_./-]+(?:\?[^\"'()\s<>]+)?(?:#[^\"'()\s<>]+)?")
LOCAL_IMPORT_RE = re.compile(r"(?:from\s+|import\s+)[\"'](\./[^\"']+\.js(?:\?v=[^\"']+)?)\1")


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    # Node's test runner emits UTF-8 progress glyphs (e.g. the pass/fail icon);
    # on Windows the default ANSI codepage cannot decode them. Force UTF-8 so
    # stdout/stderr are always captured as text.
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=ROOT,
    )


def check_syntax() -> list[str]:
    problems: list[str] = []
    for path in sorted(JS_DIR.glob("*.js")):
        result = _run(["node", "--check", str(path)])
        if result.returncode != 0:
            problems.append(f"syntax error in {path.name}:\n{result.stderr.strip()}")
    return problems


def check_line_limits() -> list[str]:
    problems: list[str] = []
    for path in sorted(JS_DIR.glob("*.js")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_LINES:
            problems.append(f"{path.name}: {lines} lines exceeds {MAX_LINES}")
    return problems


def check_asset_versions() -> list[str]:
    """Require every shipped static reference to carry the current cache key."""
    problems: list[str] = []
    expected = f"?v={STATIC_ASSET_VERSION}"
    for path in sorted((ROOT / "app" / "static").rglob("*")):
        if path.suffix.lower() not in {".css", ".html", ".js"}:
            continue
        source = path.read_text(encoding="utf-8")
        for match in STATIC_REF_RE.finditer(source):
            if expected not in match.group(0):
                problems.append(
                    f"{path.relative_to(ROOT)}: unversioned static reference {match.group(0)}"
                )
        for match in LOCAL_IMPORT_RE.finditer(source):
            if expected not in match.group(2):
                problems.append(
                    f"{path.relative_to(ROOT)}: unversioned module import {match.group(2)}"
                )
    return problems


def run_tests() -> tuple[list[str], int]:
    """Run the Node test suite; return (problems, test_count)."""
    pattern = str(TEST_DIR / "*.test.js")
    result = _run(["node", "--test", pattern])
    if result.returncode != 0:
        return [f"frontend tests failed:\n{result.stdout.strip()}\n{result.stderr.strip()}"], 0
    # Count "ok N" / "pass N" lines from the TAP-ish summary the runner prints.
    count = 0
    for line in result.stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("ℹ pass ") or stripped.startswith("pass "):
            try:
                count = int(stripped.split()[-1])
            except ValueError:
                continue
    return [], count


def main() -> int:
    problems: list[str] = []
    problems.extend(check_syntax())
    problems.extend(check_line_limits())
    problems.extend(check_asset_versions())
    test_problems, test_count = run_tests()
    problems.extend(test_problems)
    if test_count < MIN_TESTS:
        problems.append(f"frontend tests: {test_count} < required {MIN_TESTS}")
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"frontend gate passed: syntax OK, modules <= {MAX_LINES} lines, {test_count} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
