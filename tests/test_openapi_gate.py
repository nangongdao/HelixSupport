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

    def test_live_snapshot_compares_clean_against_itself(self) -> None:
        """No false positives on the real 141-operation spec.

        The comparator resolves $refs recursively; if that normalization were
        not deterministic (or recursed into a self-referential schema), the
        gate would fail on an unchanged spec and every future dump would look
        breaking. Guards the fix as much as the detections below do.
        """
        import copy

        baseline = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
        self.assertEqual(_breaking_changes(baseline, copy.deepcopy(baseline)), [])


class OpenApiComparatorDepthTests(unittest.TestCase):
    """Breaking changes the comparator used to report as no change at all.

    Each case below returned ``[]`` before the shapes were resolved
    recursively and parameters/requestBody were compared. The array case is
    the load-bearing one: 31 of the 141 live operations return arrays,
    including ``GET /api/conversations``, so dropping a field from any
    conversation-list row was invisible.
    """

    ARRAY_SPEC = {
        "openapi": "3.1.0",
        "paths": {
            "/x": {
                "get": {
                    "responses": {
                        "200": {
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "array",
                                        "items": {"$ref": "#/components/schemas/It"},
                                    }
                                }
                            }
                        }
                    }
                }
            }
        },
        "components": {
            "schemas": {"It": {"type": "object", "properties": {"id": {}, "email": {}}}}
        },
    }

    PARAM_SPEC = {
        "openapi": "3.1.0",
        "paths": {
            "/y": {
                "get": {
                    "parameters": [
                        {
                            "name": "tenant",
                            "in": "query",
                            "required": True,
                            "schema": {"type": "string"},
                        },
                        {
                            "name": "page",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "integer"},
                        },
                    ],
                    "responses": {"200": {"description": "ok"}},
                }
            }
        },
    }

    def test_removed_property_inside_array_response_is_breaking(self) -> None:
        import copy

        current = copy.deepcopy(self.ARRAY_SPEC)
        del current["components"]["schemas"]["It"]["properties"]["email"]
        breaking = _breaking_changes(self.ARRAY_SPEC, current)
        self.assertTrue(
            any("email" in b for b in breaking),
            f"array element field removal must be breaking, got {breaking}",
        )

    def test_removed_property_in_nested_object_is_breaking(self) -> None:
        import copy

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/n": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {
                                            "type": "object",
                                            "properties": {
                                                "user": {"$ref": "#/components/schemas/U"}
                                            },
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {"U": {"type": "object", "properties": {"id": {}, "phone": {}}}}
            },
        }
        current = copy.deepcopy(spec)
        del current["components"]["schemas"]["U"]["properties"]["phone"]
        self.assertTrue(any("phone" in b for b in _breaking_changes(spec, current)))

    def test_removed_required_parameter_is_breaking(self) -> None:
        import copy

        current = copy.deepcopy(self.PARAM_SPEC)
        current["paths"]["/y"]["get"]["parameters"] = [
            self.PARAM_SPEC["paths"]["/y"]["get"]["parameters"][1]
        ]
        self.assertTrue(any("tenant" in b for b in _breaking_changes(self.PARAM_SPEC, current)))

    def test_optional_parameter_becoming_required_is_breaking(self) -> None:
        import copy

        current = copy.deepcopy(self.PARAM_SPEC)
        current["paths"]["/y"]["get"]["parameters"][1]["required"] = True
        self.assertTrue(
            any("became required" in b for b in _breaking_changes(self.PARAM_SPEC, current))
        )

    def test_parameter_type_change_is_breaking(self) -> None:
        import copy

        current = copy.deepcopy(self.PARAM_SPEC)
        current["paths"]["/y"]["get"]["parameters"][0]["schema"]["type"] = "integer"
        self.assertTrue(
            any("type changed" in b for b in _breaking_changes(self.PARAM_SPEC, current))
        )

    def test_new_required_request_body_field_is_breaking(self) -> None:
        import copy

        spec = copy.deepcopy(self.PARAM_SPEC)
        spec["paths"]["/y"]["get"]["requestBody"] = {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {
                        "type": "object",
                        "required": ["q"],
                        "properties": {"q": {}, "o": {}},
                    }
                }
            },
        }
        current = copy.deepcopy(spec)
        current["paths"]["/y"]["get"]["requestBody"]["content"]["application/json"]["schema"][
            "required"
        ] = ["q", "o"]
        self.assertTrue(
            any("new required fields" in b for b in _breaking_changes(spec, current)),
            "a field becoming required rejects every existing caller",
        )

    def test_contentless_response_removal_is_breaking(self) -> None:
        import copy

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/z": {
                    "delete": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"type": "object", "properties": {"ok": {}}}
                                    }
                                }
                            },
                            "204": {"description": "no content"},
                        }
                    }
                }
            },
        }
        current = copy.deepcopy(spec)
        del current["paths"]["/z"]["delete"]["responses"]["204"]
        self.assertTrue(
            any("204" in b for b in _breaking_changes(spec, current)),
            "a bare 204 declared after a 200 was dropped by the old `if not shapes`",
        )

    def test_self_referential_schema_does_not_recurse_forever(self) -> None:
        import copy

        spec = {
            "openapi": "3.1.0",
            "paths": {
                "/t": {
                    "get": {
                        "responses": {
                            "200": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/Node"}
                                    }
                                }
                            }
                        }
                    }
                }
            },
            "components": {
                "schemas": {
                    "Node": {
                        "type": "object",
                        "properties": {
                            "id": {},
                            "children": {
                                "type": "array",
                                "items": {"$ref": "#/components/schemas/Node"},
                            },
                        },
                    }
                }
            },
        }
        self.assertEqual(_breaking_changes(spec, copy.deepcopy(spec)), [])


if __name__ == "__main__":
    unittest.main()
