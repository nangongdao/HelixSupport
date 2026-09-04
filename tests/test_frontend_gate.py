"""Phase 26.4: frontend quality gate wired into the pytest suite.

CI runs the frontend engineering checks here so a frontend regression fails
the same gate as backend tests: every ES module passes ``node --check``, no
module exceeds 400 lines, and the Node unit-test suite passes with at least
30 tests.

:class:`GateCoverageTests` asserts the checks reach every shipped file. A gate
that scans the wrong directory returns an empty problem list, which looks
exactly like a pass — so scope is pinned by test, not by reading the globs.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from scripts import frontend_gate
from scripts.frontend_gate import (
    _parse_summary,
    _vitest_exit_verdict,
    check_asset_versions,
    check_css_custom_properties,
    check_icon_symbols,
    check_line_limits,
    check_syntax,
    find_dangling_css_vars,
    find_unknown_icon_symbols,
    find_unversioned_imports,
    run_tests,
)

# Node's test runner switches reporters based on whether stdout is a TTY: the
# spec reporter writes "ℹ pass N" lines when interactive, the TAP reporter
# writes "# pass N" when piped (CI, and every subprocess.run capture). Both
# shapes must be counted or the MIN_TESTS floor silently reports zero.
TAP_TAIL = """# Subtest: channel ids are stable-shaped and unique
ok 165 - channel ids are stable-shaped and unique
  ---
  duration_ms: 0.6751
  type: 'test'
  ...
1..165
# tests 165
# suites 0
# pass 165
# fail 0
# cancelled 0
# skipped 0
# todo 0
# duration_ms 2355.2061
"""

SPEC_TAIL = """ℹ tests 165
ℹ suites 0
ℹ pass 164
ℹ fail 1
ℹ cancelled 0
ℹ skipped 0
ℹ todo 0
ℹ duration_ms 2355.2061
"""


class FrontendGateTests(unittest.TestCase):
    def test_all_modules_pass_syntax_check(self) -> None:
        problems = check_syntax()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_no_module_exceeds_400_lines(self) -> None:
        problems = check_line_limits()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_static_references_use_current_cache_version(self) -> None:
        problems = check_asset_versions()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_node_unit_tests_pass_with_at_least_30(self) -> None:
        problems, count = run_tests()
        self.assertEqual(problems, [], "\n".join(problems))
        self.assertGreaterEqual(
            count,
            30,
            f"frontend unit tests must be >= 30, got {count}",
        )

    def test_summary_parser_reads_tap_reporter_output(self) -> None:
        self.assertEqual(_parse_summary(TAP_TAIL), {"tests": 165, "pass": 165, "fail": 0})

    def test_summary_parser_reads_spec_reporter_output(self) -> None:
        self.assertEqual(_parse_summary(SPEC_TAIL), {"tests": 165, "pass": 164, "fail": 1})

    def test_summary_parser_reports_nothing_for_foreign_output(self) -> None:
        # A summary shape we do not recognise must not be mistaken for a
        # green zero-test run.
        self.assertEqual(_parse_summary("ok 1 - some test\n1..1\n"), {})

    def test_no_shipped_css_var_is_dangling(self) -> None:
        problems = check_css_custom_properties()
        self.assertEqual(problems, [], "\n".join(problems))

    def test_every_icon_reference_resolves_to_a_sprite_symbol(self) -> None:
        problems = check_icon_symbols()
        self.assertEqual(problems, [], "\n".join(problems))


class GateCoverageTests(unittest.TestCase):
    """The checks must actually reach every shipped file.

    Each assertion here pins a file that was silently exempt: a gate that
    scans the wrong directory reports zero problems, which is indistinguishable
    from a pass. These fail if a glob narrows again.
    """

    def test_entry_points_are_scanned(self) -> None:
        names = {path.name for path in frontend_gate._entry_point_paths()}
        self.assertEqual(names, {"app.js", "widget-app.js"})

    def test_entry_point_ceiling_is_enforced(self) -> None:
        # app.js is 477 lines against a 500 ceiling. Tightening below its
        # real size must produce a violation — proof the file is measured
        # rather than skipped.
        with mock.patch.object(frontend_gate, "ENTRY_MAX_LINES", 400):
            problems = frontend_gate.check_line_limits()
        self.assertTrue(
            any(p.startswith("app.js:") for p in problems),
            f"app.js must be measured, got {problems}",
        )

    def test_island_sources_are_scanned_for_stale_cache_keys(self) -> None:
        # The islands hardcode ?v= in JSX. Bumping the expected version must
        # flag them; before frontend/src was walked, this reported nothing.
        with mock.patch.object(frontend_gate, "STATIC_ASSET_VERSION", "9.9.9"):
            problems = frontend_gate.check_asset_versions()
        island = [p for p in problems if p.startswith("frontend")]
        self.assertTrue(island, "frontend/src static references must be scanned")

    def test_island_imports_are_exempt_from_cache_keys(self) -> None:
        # Vite resolves `./constants.js` at build time into a content-hashed
        # chunk, so ?v= there would be meaningless — only the zero-build
        # track needs it. Guards against re-introducing 20 false positives.
        problems = frontend_gate.check_asset_versions()
        self.assertEqual(problems, [], "\n".join(problems))


class UnknownIconSymbolTests(unittest.TestCase):
    """``<use href="icons.svg#missing">`` paints nothing, silently.

    Four shipped controls referenced symbols the sprite never declared; the
    worst, #themeToggle, is icon-only and rendered blank. No console error, no
    404 (the sprite resolves), and axe passes on the aria-label — so these
    cases pin the detection instead.
    """

    DEFINED = {"check", "moon"}

    def test_known_symbol_is_clean(self) -> None:
        html = '<svg><use href="/static/icons.svg?v=1.4.0#check" /></svg>'
        self.assertEqual(find_unknown_icon_symbols([("index.html", html)], self.DEFINED), [])

    def test_unknown_symbol_is_reported(self) -> None:
        html = '<svg><use href="/static/icons.svg?v=1.4.0#nope" /></svg>'
        problems = find_unknown_icon_symbols([("index.html", html)], self.DEFINED)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("icons.svg#nope", problems[0])

    def test_jsx_href_form_is_scanned_too(self) -> None:
        # The islands write the same reference in JSX; a symbol missing there
        # is just as blank (mentions-island.jsx carried one).
        jsx = 'const I = () => <svg><use href="/static/icons.svg?v=1.4.0#ghost" /></svg>;'
        problems = find_unknown_icon_symbols([("island.jsx", jsx)], self.DEFINED)
        self.assertEqual(len(problems), 1, problems)

    def test_reference_without_version_query_is_matched(self) -> None:
        html = '<svg><use href="/static/icons.svg#nope" /></svg>'
        self.assertEqual(len(find_unknown_icon_symbols([("i.html", html)], self.DEFINED)), 1)

    def test_template_literal_href_is_scanned(self) -> None:
        # `#${...}` hid three missing symbols through all of D3–D5: the static
        # regex cannot see past the `${`, so session-shell shipped a blank
        # theme toggle and two blank nav buttons.
        jsx = 'href={`/static/icons.svg?v=1.4.0#${theme === "dark" ? "moon" : "sun"}`}'
        problems = find_unknown_icon_symbols([("shell.jsx", jsx)], self.DEFINED)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("icons.svg#sun", problems[0])

    def test_comparison_operand_is_not_read_as_an_icon_name(self) -> None:
        # "dark" is the theme being compared, not a symbol; anchoring on ?/:
        # keeps it out. Both branches here are real symbols, so this is clean.
        jsx = 'href={`/static/icons.svg#${theme === "dark" ? "moon" : "check"}`}'
        self.assertEqual(find_unknown_icon_symbols([("shell.jsx", jsx)], self.DEFINED), [])

    def test_icon_property_table_feeding_a_dynamic_href_is_scanned(self) -> None:
        # `#${item.icon}` reads a nav table declared far from the href, so the
        # literal must be resolved there (NAV_VIEWS carried two dead names).
        jsx = (
            'const NAV = [{ id: "admin", icon: "settings" }];\n'
            "const I = () => <use href={`/static/icons.svg#${item.icon}`} />;"
        )
        problems = find_unknown_icon_symbols([("shell.jsx", jsx)], self.DEFINED)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("settings", problems[0])

    def test_icon_property_without_any_sprite_href_is_ignored(self) -> None:
        # An unrelated `icon:` key in a file that never builds an icons.svg
        # href must not trip the gate.
        js = 'export const cfg = { icon: "not-a-sprite-symbol" };'
        self.assertEqual(find_unknown_icon_symbols([("cfg.js", js)], self.DEFINED), [])

    def test_line_number_points_at_the_reference(self) -> None:
        html = '<div>\n  <p>x</p>\n</div>\n<svg><use href="/static/icons.svg#nope" /></svg>'
        problems = find_unknown_icon_symbols([("i.html", html)], self.DEFINED)
        self.assertIn("i.html:4", problems[0])


class UnversionedImportTests(unittest.TestCase):
    """A stale ``?v=`` on a relative import serves cached JS after a release.

    This check existed but never fired: its pattern wrote ``["'](path)\\1``,
    so the backreference closed on the *path* group instead of the quote and
    demanded the specifier appear twice. Nothing ever matched, on any file, for
    as long as the check shipped — while notes.md claimed version drift here
    fails CI. These cases pin both the match and the version comparison.
    """

    VERSION = "?v=1.4.0"

    def test_current_version_is_clean(self) -> None:
        src = 'import { api } from "./http.js?v=1.4.0";'
        self.assertEqual(find_unversioned_imports([("app.js", src)], self.VERSION), [])

    def test_stale_version_is_reported(self) -> None:
        src = 'import { api } from "./http.js?v=1.3.9";'
        problems = find_unversioned_imports([("app.js", src)], self.VERSION)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("./http.js?v=1.3.9", problems[0])

    def test_missing_version_is_reported(self) -> None:
        src = 'import { api } from "./http.js";'
        problems = find_unversioned_imports([("app.js", src)], self.VERSION)
        self.assertEqual(len(problems), 1, problems)

    def test_single_quoted_import_is_matched(self) -> None:
        # The backreference must close on the same quote kind it opened with.
        src = "import { api } from './http.js';"
        self.assertEqual(len(find_unversioned_imports([("app.js", src)], self.VERSION)), 1)

    def test_from_and_bare_import_forms_are_both_matched(self) -> None:
        src = 'import "./side-effect.js";\nimport { x } from "./other.js";'
        self.assertEqual(len(find_unversioned_imports([("app.js", src)], self.VERSION)), 2)

    def test_line_number_points_at_the_import(self) -> None:
        src = '// header\n\nimport { x } from "./late.js";'
        problems = find_unversioned_imports([("app.js", src)], self.VERSION)
        self.assertIn("app.js:3", problems[0])

    def test_bare_package_specifier_is_ignored(self) -> None:
        # Only relative (./) specifiers are ours to cache-bust; node_modules
        # imports in the island sources carry no version key.
        src = 'import React from "react";\nimport { z } from "zustand";'
        self.assertEqual(find_unversioned_imports([("island.jsx", src)], self.VERSION), [])


class DanglingCssVarTests(unittest.TestCase):
    """``var(--x)`` with no definition and no fallback drops its declaration.

    Three shipped rules were lost this way before the check existed, so the
    cases below pin the boundaries: a fallback makes a reference acceptable,
    definitions pool across sheets, and an inline ``:root { --x: y }`` counts
    as a definition (anchoring the pattern to line start made every use of
    such a property look dangling).
    """

    TOKENS = ":root {\n  --ink: #eee;\n  --muted: #888;\n}"

    def test_reference_to_defined_property_is_clean(self) -> None:
        sheets = [("tokens.css", self.TOKENS), ("styles.css", ".a { color: var(--ink); }")]
        self.assertEqual(find_dangling_css_vars(sheets), [])

    def test_undefined_reference_without_fallback_is_reported(self) -> None:
        sheets = [("tokens.css", self.TOKENS), ("styles.css", ".a { background: var(--bg); }")]
        problems = find_dangling_css_vars(sheets)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("var(--bg)", problems[0])
        self.assertIn("styles.css:1", problems[0])

    def test_undefined_reference_with_fallback_is_accepted(self) -> None:
        # It still renders; whether the fallback is the intended value is a
        # judgement call this check deliberately does not make.
        sheets = [
            ("styles.css", ".a { color: var(--nope, var(--muted)); }"),
            ("t.css", self.TOKENS),
        ]
        self.assertEqual(find_dangling_css_vars(sheets), [])

    def test_nested_fallback_with_undefined_inner_is_reported(self) -> None:
        # var(--a, var(--b)) with --b undefined: --a falls back to a
        # guaranteed-invalid value, so the declaration drops anyway.
        sheets = [("styles.css", ".a { color: var(--nope, var(--also-nope)); }")]
        problems = find_dangling_css_vars(sheets)
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("var(--also-nope)", problems[0])

    def test_inline_definition_counts(self) -> None:
        sheets = [
            ("tokens.css", ":root { --accent: red; }"),
            ("styles.css", ".a { color: var(--accent); }"),
        ]
        self.assertEqual(find_dangling_css_vars(sheets), [])

    def test_line_number_points_at_the_reference(self) -> None:
        source = ".a {\n  color: red;\n}\n.b {\n  color: var(--absent);\n}"
        problems = find_dangling_css_vars([("styles.css", source)])
        self.assertEqual(len(problems), 1, problems)
        self.assertIn("styles.css:5", problems[0])


class VitestExitVerdictTests(unittest.TestCase):
    """The vitest segment consumes a captured summary, not the exit code."""

    @staticmethod
    def _verdict(stdout: str, returncode: int = 1) -> list[str]:
        return _vitest_exit_verdict(
            SimpleNamespace(returncode=returncode, stdout=stdout, stderr=""),
        )

    def test_worker_exit_noise_is_accepted_when_all_tests_pass(self) -> None:
        # Memory pressure on Windows can OOM a tinypool worker after its tests
        # finish — every suite passes, the only errors are worker exits.
        stdout = (
            "Vitest caught 2 unhandled errors during the test run.\n"
            "\u2500\u2500 Unhandled Error \u2500\u2500\n"
            "Error: Worker exited unexpectedly\n"
            "  \u276f worker\n"
            "\u2500\u2500 Unhandled Error \u2500\u2500\n"
            "Error: Worker exited unexpectedly\n"
            "  \u276f worker\n"
            " Test Files  14 passed (14)\n"
            "      Tests  149 passed (149)\n"
            "     Errors  2 errors\n"
        )
        self.assertEqual(self._verdict(stdout), [])

    def test_genuine_unhandled_error_is_a_failure(self) -> None:
        stdout = (
            "\u2500\u2500 Unhandled Error \u2500\u2500\n"
            "Error: some island promise rejected\n"
            "  \u276f src/quality.tsx\n"
            " Test Files  14 passed (14)\n"
            "      Tests  149 passed (149)\n"
            "     Errors  1 errors\n"
        )
        self.assertTrue(self._verdict(stdout), "a non-worker unhandled error must fail")

    def test_failed_tests_are_a_failure(self) -> None:
        stdout = " Test Files  13 passed (14)\n      Tests  148 passed | 1 failed\n"
        self.assertTrue(self._verdict(stdout))

    def test_clean_summary_is_accepted_even_with_a_nonzero_exit(self) -> None:
        stdout = " Test Files  14 passed (14)\n      Tests  149 passed (149)\n"
        self.assertEqual(self._verdict(stdout, returncode=1), [])

    def test_empty_run_is_a_failure(self) -> None:
        self.assertTrue(self._verdict("no summary here", returncode=1))


if __name__ == "__main__":
    unittest.main()
