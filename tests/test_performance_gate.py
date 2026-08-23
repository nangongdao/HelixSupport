"""Performance budget gate wired into pytest (ROADMAP section 43.6).

The static byte-budget layer runs in the default pass (no browser needed).
The real-browser layer (LCP/CLS/long tasks/10k queue render/heap) is a
nightly/CI job — it needs Playwright Chromium plus a live server, so here we
only assert the gate script itself stays importable and its budgets
internally consistent.
"""

from __future__ import annotations

import unittest

from scripts.performance_gate import BROWSER_BUDGETS, BUDGETS, check_static_budgets


class StaticBudgetTests(unittest.TestCase):
    def test_first_paint_payload_within_budgets(self) -> None:
        sizes, problems = check_static_budgets()
        self.assertEqual(problems, [], "\n".join(problems))
        # Sanity: the measured payload is non-trivial (guards against the
        # glob silently matching nothing after a layout change).
        self.assertGreater(sizes["operator_js_bytes"], 50_000)
        self.assertGreater(sizes["operator_css_bytes"], 10_000)
        self.assertGreater(sizes["widget_js_bytes"], 5_000)

    def test_budget_keys_are_complete(self) -> None:
        sizes, _ = check_static_budgets()
        self.assertEqual(set(sizes), set(BUDGETS))

    def test_browser_budgets_cover_43_6_dimensions(self) -> None:
        for key in (
            "lcp_ms",
            "cls",
            "long_task_count_30s",
            "queue_10k_render_ms",
            "heap_growth_mb",
        ):
            self.assertIn(key, BROWSER_BUDGETS)


if __name__ == "__main__":
    unittest.main()
