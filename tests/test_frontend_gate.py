"""Phase 26.4: frontend quality gate wired into the pytest suite.

CI runs the frontend engineering checks here so a frontend regression fails
the same gate as backend tests: every ES module passes ``node --check``, no
module exceeds 400 lines, and the Node unit-test suite passes with at least
30 tests.
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace

from scripts.frontend_gate import (
    _parse_summary,
    _vitest_exit_verdict,
    check_asset_versions,
    check_css_custom_properties,
    check_line_limits,
    check_syntax,
    find_dangling_css_vars,
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
