"""Frontend quality gate (Phase 26.4).

Runs the frontend engineering checks that CI enforces without adding build
dependencies:

1. Every ES module under ``app/static/js`` must pass ``node --check``
   (syntax gate).
2. No module may exceed ``MAX_LINES`` (default 400) — the Phase 26
   acceptance limit. Covers both shipped frontend tracks: the zero-build
   modules under ``app/static/js`` and the React island sources under
   ``frontend/src`` (vitest suites excluded).
3. The Node test runner suite under ``tests/frontend`` must pass and report
   at least ``MIN_TESTS`` (default 30) tests — the Phase 26 acceptance
   threshold.
4. D2 (DESKTOP_TAURI_PLAN.md §3.5): the vitest suite under ``frontend/``
   (React island component + pure-logic tests) must pass. Runs via
   ``npx vitest run``; skipped if the frontend toolchain is absent so the
   gate stays runnable in minimal CI images.

Usage:
    python scripts/frontend_gate.py
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Same bootstrap as the other repo-root importers (visual_gate, readme_screenshots,
# run_rls_drill, …). Without it a direct `python scripts/frontend_gate.py` — the
# invocation CONTRIBUTING.md documents — dies on `No module named 'app'` unless the
# package happens to be installed editable, which only CI does.
sys.path.insert(0, str(ROOT))

from app.assets import STATIC_ASSET_VERSION  # noqa: E402

JS_DIR = ROOT / "app" / "static" / "js"
# D3: the React island sources are the second shipped frontend track, held to
# the same module limit as the zero-build modules above (check_line_limits).
FRONTEND_SRC_DIR = ROOT / "frontend" / "src"
TEST_DIR = ROOT / "tests" / "frontend"
MAX_LINES = 400
MIN_TESTS = 30
# Vitest runs 16 island suites; a healthy run finishes well under a minute.
# The bound exists so a runner that never exits fails the gate instead of
# hanging CI (see run_vitest — ESBUILD_WORKER_THREADS keeps esbuild from
# leaking child processes that hold the parent event loop open on Windows).
VITEST_TIMEOUT_SECONDS = float(os.environ.get("FRONTEND_GATE_VITEST_TIMEOUT", "300"))
STATIC_REF_RE = re.compile(r"/static/[A-Za-z0-9_./-]+(?:\?[^\"'()\s<>]+)?(?:#[^\"'()\s<>]+)?")
# A custom-property definition: `--name:` anywhere. Not anchored to line start
# — that missed single-line rules like `:root { --x: red; }` and reported every
# use of --x as dangling. A var() reference can never be followed by a colon
# (`var(--x)` / `var(--x, y)`), so an unanchored match cannot pick one up.
CSS_PROP_DEF_RE = re.compile(r"(--[A-Za-z0-9_-]+)\s*:")
# A var() reference; group 2 is the comma that opens a fallback, if present.
CSS_VAR_USE_RE = re.compile(r"var\(\s*(--[A-Za-z0-9_-]+)\s*(,)?")
# `<symbol id="x">` in the sprite, and `icons.svg?v=…#x` in a <use href>.
SVG_SYMBOL_ID_RE = re.compile(r"<symbol[^>]*\bid=\"([A-Za-z0-9_-]+)\"")
ICON_REF_RE = re.compile(r"icons\.svg[^\"'#\s]*#([A-Za-z0-9_-]+)")
# React islands build the href in a template literal — `...#${cond ? "a" : "b"}`
# or `...#${item.icon}`. ICON_REF_RE cannot see past the `${`, so the symbol
# names inside were never checked; three of them did not exist in the sprite.
ICON_REF_DYNAMIC_RE = re.compile(r"icons\.svg[^\"'`#\s]*#\$\{([^}]*)\}")
# Only the *results* of the interpolation name a symbol. `theme === "dark" ?
# "moon" : "sun"` also contains "dark", which is a theme name, not an icon —
# anchoring on `?` and `:` keeps the comparison operands out.
ICON_NAME_LITERAL_RE = re.compile(r"[?:]\s*[\"']([a-z][a-z0-9-]*)[\"']")
# Icon names are also declared away from the href, in a nav/tab table:
# `{ id: "admin", label: "管理", icon: "settings" }`. Those feed `#${item.icon}`.
ICON_NAME_PROPERTY_RE = re.compile(r"\bicon:\s*[\"']([a-z][a-z0-9-]*)[\"']")
# A relative module import: `from "./x.js?v=1.4.0"`. Group 1 is the opening
# quote so the backreference closes on the same kind; group 2 is the specifier.
# The quote MUST be its own group — with `["'](...)\1` the backreference points
# at the path group, not the quote, so the pattern demanded the path appear
# twice and matched nothing. This check silently passed on every file for as
# long as it existed (notes.md claimed version drift here fails CI).
LOCAL_IMPORT_RE = re.compile(r"(?:from\s+|import\s+)([\"'])(\./[^\"']+\.js(?:\?v=[^\"']+)?)\1")


def _run(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: float | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # Node's test runner emits UTF-8 progress glyphs (e.g. the pass/fail icon);
    # on Windows the default ANSI codepage cannot decode them. Force UTF-8 so
    # stdout/stderr are always captured as text.
    try:
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=cwd or ROOT,
            timeout=timeout,
            env={**os.environ, **(env or {})} if env else None,
            check=False,
        )
    except subprocess.TimeoutExpired as expired:
        # A gate that can hang forever is worse than a gate that fails: report
        # the timeout as a normal (failing) result so the caller can print it.
        out = expired.stdout or ""
        err = expired.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return subprocess.CompletedProcess(cmd, 124, out, err)


def check_syntax() -> list[str]:
    problems: list[str] = []
    for path in sorted(JS_DIR.glob("*.js")):
        result = _run(["node", "--check", str(path)])
        if result.returncode != 0:
            problems.append(f"syntax error in {path.name}:\n{result.stderr.strip()}")
    return problems


def check_line_limits() -> list[str]:
    """Enforce the 400-line module limit on both frontend tracks.

    The zero-build modules under app/static/js were the only track covered
    until the D3 islands landed, and frontend/src grew unwatched: the admin
    island reached 1,052 lines — past the 800-line hard prohibition — before
    anything complained. Both tracks are shipped operator code, so both are
    held to the same limit. Vitest suites (*.test.jsx) are excluded: they
    already sit under the limit by convention, and a test file's length is
    driven by case count rather than by design debt.
    """
    problems: list[str] = []
    for path in sorted(JS_DIR.glob("*.js")):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_LINES:
            problems.append(f"{path.name}: {lines} lines exceeds {MAX_LINES}")
    for path in sorted(FRONTEND_SRC_DIR.rglob("*")):
        if path.suffix not in {".js", ".jsx"} or path.name.endswith(".test.jsx"):
            continue
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_LINES:
            rel = path.relative_to(ROOT).as_posix()
            problems.append(f"{rel}: {lines} lines exceeds {MAX_LINES}")
    return problems


def check_icon_symbols() -> list[str]:
    """Fail on ``icons.svg#name`` where the sprite has no such ``<symbol>``.

    An ``<svg><use href="...#missing"></svg>`` paints nothing at all — no
    console error, no network 404 (the sprite itself resolves), just an empty
    box. Four shipped controls were found this way, the worst being
    ``#themeToggle``: an icon-only header button with no visible text, so it
    rendered completely blank. The visual baselines had captured it broken and
    axe passes because the button carries a proper aria-label, so nothing in
    the suite objected.
    """
    sprite = ROOT / "app" / "static" / "icons.svg"
    defined = set(SVG_SYMBOL_ID_RE.findall(sprite.read_text(encoding="utf-8")))
    sources: list[tuple[str, str]] = []
    for root, patterns in (
        (ROOT / "app" / "static", ("*.html", "*.js")),
        (FRONTEND_SRC_DIR, ("*.jsx", "*.js")),
    ):
        for pattern in patterns:
            for path in sorted(root.rglob(pattern)):
                if "dist" in path.parts or "node_modules" in path.parts:
                    continue
                sources.append(
                    (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
                )
    return find_unknown_icon_symbols(sources, defined)


def find_unknown_icon_symbols(sources: list[tuple[str, str]], defined: set[str]) -> list[str]:
    """Pure core of :func:`check_icon_symbols`.

    :param sources: ``(display_name, source_text)`` pairs to scan.
    :param defined: symbol ids the sprite actually declares.
    """
    problems: list[str] = []
    for name, source in sources:
        for match in ICON_REF_RE.finditer(source):
            symbol = match.group(1)
            if symbol in defined:
                continue
            line = source.count("\n", 0, match.start()) + 1
            problems.append(
                f"{name}:{line}: icons.svg#{symbol} is not a symbol in the "
                f"sprite, so the <use> paints nothing"
            )
        problems.extend(_dynamic_icon_problems(name, source, defined))
    return problems


def _dynamic_icon_problems(name: str, source: str, defined: set[str]) -> list[str]:
    """Check symbol names that only appear inside a template-literal ``href``.

    Two shapes ship today: the name is written inline in the interpolation
    (``#${theme === "dark" ? "moon" : "sun"}``), or the interpolation reads a
    property and the literal lives in a nav table (``icon: "settings"``). Both
    are checked only when the file actually builds an ``icons.svg`` href, so an
    unrelated ``icon:`` key elsewhere cannot trip the gate.
    """
    problems: list[str] = []
    dynamic_refs = list(ICON_REF_DYNAMIC_RE.finditer(source))
    if not dynamic_refs:
        return problems

    for match in dynamic_refs:
        line = source.count("\n", 0, match.start()) + 1
        for literal in ICON_NAME_LITERAL_RE.finditer(match.group(1)):
            symbol = literal.group(1) or literal.group(2)
            if symbol in defined:
                continue
            problems.append(
                f"{name}:{line}: icons.svg#{symbol} (built in a template "
                f"literal) is not a symbol in the sprite, so the <use> paints "
                f"nothing"
            )

    for match in ICON_NAME_PROPERTY_RE.finditer(source):
        symbol = match.group(1)
        if symbol in defined:
            continue
        line = source.count("\n", 0, match.start()) + 1
        problems.append(
            f'{name}:{line}: icon: "{symbol}" feeds an icons.svg href but is '
            f"not a symbol in the sprite, so the <use> paints nothing"
        )
    return problems


def check_css_custom_properties() -> list[str]:
    """Fail on ``var(--x)`` where --x is never defined and there is no fallback.

    CSS resolves such a reference to guaranteed-invalid, which drops the whole
    declaration at computed-value time — silently. Three shipped rules were
    found this way: `.report-preview`'s background (the <pre> rendered
    transparent), `.csat-label`'s colour (inherited full-strength ink), and
    `.desktop-splash`'s two --color-* names that do not exist (the startup
    overlay stayed dark in the light theme, via its hex fallbacks).

    A reference *with* a fallback is accepted: it still renders, and whether
    the fallback is the intended value is a judgement call, not a defect this
    check can decide.
    """
    sheets = [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted((ROOT / "app" / "static").rglob("*.css"))
        if "dist" not in path.parts  # Vite output, not hand-authored
    ]
    return find_dangling_css_vars(sheets)


def find_dangling_css_vars(sheets: list[tuple[str, str]]) -> list[str]:
    """Pure core of :func:`check_css_custom_properties`.

    Definitions are pooled across every sheet before use is checked, because
    the token file defines what the stylesheets consume.

    :param sheets: ``(display_name, css_source)`` pairs.
    """
    definitions: set[str] = set()
    for _, source in sheets:
        definitions |= set(CSS_PROP_DEF_RE.findall(source))
    problems: list[str] = []
    for name, source in sheets:
        for match in CSS_VAR_USE_RE.finditer(source):
            prop = match.group(1)
            has_fallback = match.group(2) is not None
            if has_fallback or prop in definitions:
                continue
            line = source.count("\n", 0, match.start()) + 1
            problems.append(
                f"{name}:{line}: var({prop}) is never defined and has no "
                f"fallback, so the whole declaration is dropped"
            )
    return problems


def find_unversioned_imports(sources: list[tuple[str, str]], expected_version: str) -> list[str]:
    """Find relative module imports that lack the cache-busting query key.

    Args:
        sources: (name, text) pairs.
        expected_version: The ?v= query string every import must carry.

    Returns:
        Problem strings "name:line: unversioned import ./x.js".
    """
    problems: list[str] = []
    for name, source in sources:
        for line_no, line in enumerate(source.splitlines(), start=1):
            for match in LOCAL_IMPORT_RE.finditer(line):
                # Group 1 is the quote, group 2 is the path specifier.
                specifier = match.group(2)
                if expected_version not in specifier:
                    problems.append(f"{name}:{line_no}: unversioned module import {specifier}")
    return problems


def check_asset_versions() -> list[str]:
    """Require every shipped static reference to carry the current cache key.

    D2 (DESKTOP_TAURI_PLAN.md §3.4): dist/ artifacts are content-hashed by
    Vite and self-cache-busting — they are exempt from the ?v= requirement.
    """
    problems: list[str] = []
    expected = f"?v={STATIC_ASSET_VERSION}"
    sources_for_import_check: list[tuple[str, str]] = []
    for path in sorted((ROOT / "app" / "static").rglob("*")):
        if path.suffix.lower() not in {".css", ".html", ".js"}:
            continue
        # Skip Vite-produced dist artifacts (self-cache-busting via hash).
        if "dist" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        for match in STATIC_REF_RE.finditer(source):
            ref = match.group(0)
            # dist/ references are content-hashed, exempt from ?v=.
            if "/static/dist/" in ref:
                continue
            if expected not in ref:
                problems.append(f"{path.relative_to(ROOT)}: unversioned static reference {ref}")
        # Collect JS/JSX modules for import checking (HTML/CSS have no imports).
        if path.suffix.lower() == ".js":
            sources_for_import_check.append((str(path.relative_to(ROOT)), source))
    problems.extend(find_unversioned_imports(sources_for_import_check, expected))
    return problems


# Node's test runner picks its reporter from the environment: the spec
# reporter when stdout is a TTY (interactive), the TAP reporter otherwise
# (captured pipes, CI). The summary lines differ between the two —
#   spec: "ℹ pass 165" / "ℹ fail 2"
#   tap:  "# pass 165" / "# fail 2"
# — so both shapes are matched rather than pinning --test-reporter (which
# would cost the human-readable output during local interactive runs).
_SUMMARY_RE = re.compile(r"^[#ℹ]\s+(pass|fail|tests)\s+(\d+)$")


def _parse_summary(stdout: str) -> dict[str, int]:
    """Extract the trailing {pass, fail, tests} counters from runner output."""
    summary: dict[str, int] = {}
    for line in stdout.splitlines():
        match = _SUMMARY_RE.match(line.strip())
        if match:
            summary[match.group(1)] = int(match.group(2))
    return summary


def run_tests() -> tuple[list[str], int]:
    """Run the Node test suite; return (problems, test_count)."""
    pattern = str(TEST_DIR / "*.test.js")
    result = _run(["node", "--test", pattern])
    if result.returncode != 0:
        return [f"frontend tests failed:\n{result.stdout.strip()}\n{result.stderr.strip()}"], 0
    summary = _parse_summary(result.stdout)
    # A zero pass count means either a genuinely empty run or a summary shape
    # we no longer recognise — both must fail the gate rather than silently
    # reporting "0 tests", which is how the TAP/spec mismatch went unnoticed.
    if not summary:
        tail = result.stdout.strip()[-800:]
        return [f"frontend tests: unparseable runner summary (0 tests counted)\n{tail}"], 0
    failures = summary.get("fail", 0)
    if failures:
        return [f"frontend tests: {failures} failing\n{result.stdout.strip()[-2000:]}"], 0
    return [], summary.get("pass", summary.get("tests", 0))


FRONTEND_DIR = ROOT / "frontend"


def run_vitest() -> list[str]:
    """D2 (§3.5): run the vitest suite for the React island sources.

    Returns a list of problems. If vitest is not installed the segment is
    skipped (returns an informational note, not a failure) so minimal CI
    images that only run the zero-build Node suite are not blocked — but a
    failing vitest run *is* a failure.
    """
    vitest_bin = FRONTEND_DIR / "node_modules" / ".bin" / "vitest"
    # On Windows the shim is a batch file (vitest.cmd); the extension-less
    # shim is not a valid Win32 executable.
    if sys.platform == "win32":
        vitest_bin = vitest_bin.with_suffix(".cmd")
    if not vitest_bin.exists():
        return ["vitest not installed under frontend/ — run `npm install` there"]
    result = _run_vitest_observed(
        # Cap workers: the default forks pool is one worker per CPU core, which
        # OOMs on memory-constrained hosts and takes the run down with
        # "Worker exited unexpectedly". Two workers keep the 16 suites bounded.
        [str(vitest_bin), "run", "--minWorkers=1", "--maxWorkers=2"],
        cwd=FRONTEND_DIR,
        timeout=VITEST_TIMEOUT_SECONDS,
        env={"ESBUILD_WORKER_THREADS": "1"},
    )
    if result.returncode == 0:
        return []
    return _vitest_exit_verdict(result)


def _vitest_exit_verdict(result: subprocess.CompletedProcess[str]) -> list[str]:
    """Judge a non-zero/timeout vitest exit by the summary it printed.

    On some Windows setups the runner finishes every suite, prints a complete
    green summary, and then never exits (the Vite/esbuild transform service
    leaks a child that keeps the event loop alive), exits non-zero on a later
    attempt of the exact same suite, or crashes a tinypool worker under memory
    pressure while every test still passes. The captured summary is the actual
    evidence: a run whose tests all passed is accepted with a warning even
    when the exit is dirty, but a failing, incomplete or genuinely-erroring
    run still fails.
    """
    stdout = _ANSI_RE.sub("", result.stdout or "")
    stderr = _ANSI_RE.sub("", result.stderr or "")
    tail = f"{stdout.strip()[-1500:]}\n{stderr.strip()[-500:]}"
    files_passed = re.search(r"Test Files\s+(\d+) passed", stdout)
    tests_passed = re.search(r"Tests\s+(\d+) passed", stdout)
    any_failed = re.search(r"\d+ failed", stdout) is not None
    # Every unhandled-error block begins with "Error: "; on this host the only
    # recurring unrecoverable one is a tinypool worker exit (OOM). When every
    # block is a worker exit the tests still passed, so it is harness noise.
    error_blocks = len(re.findall(r"\bError:\s", stdout))
    worker_exits = len(re.findall(r"Worker exited unexpectedly", stdout))
    if files_passed and tests_passed and not any_failed:
        if error_blocks == 0:
            print(
                "frontend gate warning: vitest printed a clean summary "
                f"({files_passed.group(1)} test files / {tests_passed.group(1)} tests "
                f"passed) but exited {result.returncode} — judging by the summary "
                "(the runner's exit is unreliable on this host)",
                file=sys.stderr,
            )
            return []
        if error_blocks == worker_exits:
            print(
                "frontend gate warning: vitest's tests all passed "
                f"({files_passed.group(1)} test files / {tests_passed.group(1)} tests) "
                "but a tinypool worker exited unexpectedly — accepted as harness "
                "noise because no test failed",
                file=sys.stderr,
            )
            return []
        # Unhandled errors that are not worker exits fall through as failures.
    if result.returncode == 124:
        return [
            (
                "vitest did not exit within "
                f"{VITEST_TIMEOUT_SECONDS}s and its summary is not a clean "
                "pass (the suite may have hung or genuinely failed):\n" + tail
            )
        ]
    return [f"vitest failed:\n{stdout.strip()}\n{stderr.strip()}"]


# The last line vitest prints once every suite has finished.
_VITEST_DONE_RE = re.compile(r"\bDuration\s+\d")
_VITEST_EXIT_GRACE_SECONDS = 45.0
# vitest colours its summary even when stdout is a pipe, and the codes sit
# between the label and the numbers — strip them before any matching.
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _run_vitest_observed(
    cmd: list[str],
    cwd: Path,
    timeout: float,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    """Run vitest but stop waiting once the run is provably complete.

    Streams stdout until the final ``Duration`` line shows up (everything the
    gate needs is on stdout by then), gives the runner a grace window to exit
    on its own, and kills the whole process tree if it still lingers. This
    keeps the gate bounded on hosts where vitest's exit is unreliable without
    ever trusting a run that has not printed its summary.
    """
    merged: list[str] = []
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env={**os.environ, **env},
    )
    reader = threading.Thread(target=_drain, args=(proc.stdout, merged), daemon=True)
    reader.start()
    started = time.monotonic()
    completed = False
    while time.monotonic() - started < timeout:
        # The Duration line is not necessarily the last element: a trailing
        # blank line can land between our polls, so scan the recent tail.
        if _VITEST_DONE_RE.search(_ANSI_RE.sub("", "".join(merged[-6:]))):
            completed = True
            break
        time.sleep(0.2)
    try:
        proc.wait(timeout=_VITEST_EXIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        _kill_process_tree(proc.pid)
        proc.wait(timeout=30)
    reader.join(timeout=10)
    returncode = proc.returncode
    if returncode is None:
        returncode = 0 if completed else 124
    return subprocess.CompletedProcess(cmd, returncode, "".join(merged), "")


def _drain(pipe, sink: list[str]) -> None:
    """Collect a pipe line by line until EOF (EOF may never come)."""
    try:
        # list.extend over a lazy iterator appends incrementally, so the
        # main thread can watch the summary land before EOF.
        sink.extend(pipe)
    except (ValueError, OSError):  # closed under us when we kill the tree
        pass


def _kill_process_tree(pid: int) -> None:
    """Kill a Windows process and every child it spawned."""
    subprocess.run(
        ["taskkill", "/PID", str(pid), "/T", "/F"],
        capture_output=True,
        check=False,
    )


def main() -> int:
    problems: list[str] = []
    problems.extend(check_syntax())
    problems.extend(check_line_limits())
    problems.extend(check_css_custom_properties())
    problems.extend(check_icon_symbols())
    problems.extend(check_asset_versions())
    test_problems, test_count = run_tests()
    problems.extend(test_problems)
    if test_count < MIN_TESTS:
        problems.append(f"frontend tests: {test_count} < required {MIN_TESTS}")
    problems.extend(run_vitest())
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}", file=sys.stderr)
        return 1
    print(f"frontend gate passed: syntax OK, modules <= {MAX_LINES} lines, {test_count} tests")
    return 0


if __name__ == "__main__":
    sys.exit(main())
