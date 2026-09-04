"""Gate B (1.4) item #2: image admission gate tests.

The admission check is fail-closed: a release image without a pinned base
digest, a drifted source tree, or a missing SBOM must be rejected.  These
tests exercise the three offline checks (pin / manifest / SBOM) with temp
fixtures so they run anywhere, including machines without a registry.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.image_admission_check import admission_gate
from scripts.release_manifest import build_manifest


def _write_payload(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


class ImageAdmissionGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.pin = self.root / "base-image-pin.json"
        self.sbom = self.root / "sbom.json"
        self.manifest = self.root / "release-manifest.json"
        _write_payload(
            self.pin,
            {"base_image": "python:3.11-slim", "digest": "sha256:" + "a" * 64},
        )
        _write_payload(self.sbom, {"components": [{"name": "helix", "version": "1.4.0"}]})
        _write_payload(
            self.manifest,
            {"everything": "in this fixture is consistent", "note": "admission test"},
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_pin_is_rejected(self) -> None:
        violations = admission_gate(
            pin_path=self.root / "nope.json",
            manifest_path=self.manifest,
            sbom_path=self.sbom,
        )
        self.assertTrue(any("digest 未固定" in v or "缺失" in v for v in violations))

    def test_null_digest_is_rejected(self) -> None:
        _write_payload(self.pin, {"base_image": "python:3.11-slim", "digest": None})
        violations = admission_gate(
            pin_path=self.pin, manifest_path=self.manifest, sbom_path=self.sbom
        )
        self.assertTrue(any("digest 未固定" in v for v in violations))

    def test_bad_digest_format_is_rejected(self) -> None:
        _write_payload(self.pin, {"base_image": "python:3.11-slim", "digest": "abc"})
        violations = admission_gate(
            pin_path=self.pin, manifest_path=self.manifest, sbom_path=self.sbom
        )
        self.assertTrue(any("digest 格式异常" in v for v in violations))

    def test_missing_sbom_is_rejected(self) -> None:
        violations = admission_gate(
            pin_path=self.pin, manifest_path=self.manifest, sbom_path=self.root / "nope.json"
        )
        self.assertTrue(any("SBOM 缺失" in v for v in violations))

    def test_empty_sbom_is_rejected(self) -> None:
        _write_payload(self.sbom, {"components": []})
        violations = admission_gate(
            pin_path=self.pin, manifest_path=self.manifest, sbom_path=self.sbom
        )
        self.assertTrue(any("SBOM 无组件" in v for v in violations))

    def test_clean_fixture_passes_admission(self) -> None:
        """Digest + SBOM + manifest (via the real verifier) are consistent."""
        _write_payload(self.manifest, build_manifest())
        violations = admission_gate(
            pin_path=self.pin, manifest_path=self.manifest, sbom_path=self.sbom
        )
        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
