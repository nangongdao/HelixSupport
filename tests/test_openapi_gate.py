"""Phase 25.2: OpenAPI governance gate.

- The committed snapshot ``api/openapi.json`` must match the live spec
  (breaking changes fail; additive changes require a fresh dump).
- Every operation must carry summary/description/tags so the generated docs
  and SDK are complete.
- The gate itself must reject a deliberately-broken snapshot (red-light
  proof), so a future regression in the comparator cannot silently pass.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from scripts.openapi_snapshot import (
    SNAPSHOT,
    _breaking_changes,
    _build_spec,
    _operations,
)

ROOT = Path(__file__).resolve().parent.parent


class OpenApiSnapshotGateTests(unittest.TestCase):
    def test_snapshot_matches_live_spec(self) -> None:
        self.assertTrue(SNAPSHOT.exists(), "api/openapi.json missing; run --dump")
        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        current = _build_spec()
        breaking = _breaking_changes(baseline, current)
        self.assertEqual(
            breaking,
            [],
            f"OpenAPI breaking changes vs snapshot: {breaking} "
            "(regenerate with --dump only for intentional API changes)",
        )

    def test_snapshot_is_current_with_code(self) -> None:
        """Snapshot must be identical to the live spec (no drift at all)."""
        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        current = _build_spec()
        self.assertEqual(baseline, current, "api/openapi.json is stale; run --dump")


class OpenApiMetadataCoverageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.spec = _build_spec()

    def test_every_operation_has_summary_description_and_tags(self) -> None:
        missing: list[str] = []
        for key, operation in _operations(self.spec).items():
            if not operation.get("summary"):
                missing.append(f"{key}: no summary")
            if not operation.get("description"):
                missing.append(f"{key}: no description")
            if not operation.get("tags"):
                missing.append(f"{key}: no tags")
        self.assertEqual(
            missing,
            [],
            f"{len(missing)} operations lack OpenAPI metadata: {missing[:10]}",
        )

    def test_error_responses_documented(self) -> None:
        """Operations should document at least a 4xx problem-details response."""
        undoc_4xx: list[str] = []
        for key, operation in _operations(self.spec).items():
            responses = operation.get("responses", {})
            has_4xx = any(status.startswith("4") for status in responses if status.isdigit())
            if not has_4xx:
                undoc_4xx.append(key)
        # Health/auth probes are unauthenticated and may legitimately omit 4xx;
        # everything under /api must document error responses.
        api_undoc = [
            k
            for k in undoc_4xx
            if "/api/" in k and not k.startswith("GET /api/") or " /api/" in k and "/auth" not in k
        ]
        self.assertEqual(api_undoc, [], f"operations missing 4xx docs: {api_undoc[:10]}")


class OpenApiComparatorRedLightTests(unittest.TestCase):
    """The comparator must reject a snapshot that diverges from the code."""

    def test_removed_property_is_breaking(self) -> None:
        import copy

        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        current = copy.deepcopy(baseline)
        # Simulate the code dropping a field from a response schema.
        current["components"]["schemas"]["ConversationOut"]["properties"].pop("customer_name", None)
        breaking = _breaking_changes(baseline, current)
        self.assertTrue(
            any("customer_name" in b for b in breaking),
            f"expected customer_name removal to be breaking, got {breaking}",
        )

    def test_removed_endpoint_is_breaking(self) -> None:
        import copy

        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        current = copy.deepcopy(baseline)
        current["paths"].pop("/api/dashboard", None)
        breaking = _breaking_changes(baseline, current)
        self.assertTrue(
            any("removed endpoint" in b and "dashboard" in b for b in breaking),
            f"expected dashboard removal to be breaking, got {breaking}",
        )


if __name__ == "__main__":
    unittest.main()
