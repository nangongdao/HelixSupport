"""Phase 26.4: frontend quality gate wired into the pytest suite.

CI runs the frontend engineering checks here so a frontend regression fails
the same gate as backend tests: every ES module passes ``node --check``, no
module exceeds 400 lines, and the Node unit-test suite passes with at least
30 tests.
"""

from __future__ import annotations

import unittest

from scripts.frontend_gate import check_asset_versions, check_line_limits, check_syntax, run_tests


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


if __name__ == "__main__":
    unittest.main()
