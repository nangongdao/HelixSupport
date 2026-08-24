"""Phase 41.2 / SEC-003: CI workflow pin consistency gate tests.

Verifies every third-party actions reference is registered with a matching
ref, unknown actions and ref drifts fail, and local/``docker://`` references
are ignored as non-supply.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.check_workflows import review

PINS = {
    "schema_version": 1,
    "pins": {
        "actions/checkout": {"ref": "v4", "sha": None, "approved": "2026-08-20"},
        "actions/setup-python": {"ref": "v5", "sha": None, "approved": "2026-08-20"},
    },
}

WORKFLOW = """\
name: CI
jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: ./.github/actions/local
      - uses: docker://alpine:3.19
      - uses: actions/setup-python@v5
"""


def _files(
    root: Path, workflow: str = WORKFLOW, pins: dict[str, object] | None = None
) -> tuple[Path, Path]:
    workflows_dir = root / "workflows"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    (workflows_dir / "ci.yml").write_text(workflow, encoding="utf-8")
    pins_path = root / "ci-pins.json"
    pins_path.write_text(json.dumps(pins or PINS), encoding="utf-8")
    return workflows_dir, pins_path


class CheckWorkflowsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_registered_refs_pass_with_warnings_for_unpinned_sha(self) -> None:
        workflows_dir, pins_path = _files(self.root)
        violations, warnings = review(workflows_dir, pins_path)
        self.assertEqual(violations, [])
        self.assertEqual(len(warnings), 2)  # checkout + setup-python sha not pinned yet

    def test_unknown_action_fails(self) -> None:
        workflow = WORKFLOW.replace("actions/setup-python@v5", "actions/cache@v4")
        workflows_dir, pins_path = _files(self.root, workflow=workflow)
        violations, _ = review(workflows_dir, pins_path)
        self.assertEqual(len(violations), 1)
        self.assertIn("actions/cache", violations[0])

    def test_ref_drift_fails(self) -> None:
        pins = json.loads(json.dumps(PINS))
        pins["pins"]["actions/checkout"]["ref"] = "v3"
        workflows_dir, pins_path = _files(self.root, pins=pins)
        violations, _ = review(workflows_dir, pins_path)
        self.assertEqual(len(violations), 1)
        self.assertIn("漂移", violations[0])

    def test_local_and_docker_references_are_ignored(self) -> None:
        workflow = WORKFLOW.replace("actions/setup-python@v5", "actions/checkout@v4")
        workflow = workflow.replace(
            "      - uses: docker://alpine:3.19",
            "      - uses: some/action@v1\n      - uses: docker://alpine:3.19",
        )
        workflows_dir, _ = _files(self.root, workflow=workflow)
        # some/action@v1 is not pinned → violation; docker/local are ignored.
        violations, _ = review(workflows_dir, _files(self.root, workflow=workflow)[1])
        self.assertIn("some/action", violations[0])

    def test_missing_pins_file_fails(self) -> None:
        workflows_dir = self.root / "workflows"
        workflows_dir.mkdir(parents=True, exist_ok=True)
        (workflows_dir / "ci.yml").write_text(WORKFLOW, encoding="utf-8")
        violations, _ = review(workflows_dir, self.root / "missing.json")
        self.assertEqual(len(violations), 1)


if __name__ == "__main__":
    unittest.main()
