"""Phase 41.2 / SEC-003: release manifest build/verify gate tests.

Proves the manifest round-trips cleanly against an unchanged tree and that
verification fails on any tamper — a modified source file, a drifted
requirements closure, or an edited digest inside the manifest itself.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.release_manifest import build_manifest, verify_manifest

PIN = {
    "schema_version": 1,
    "base_image": "python:3.11-slim",
    "digest": None,
}


class ReleaseManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        (self.root / "app").mkdir(parents=True)
        (self.root / "app" / "one.py").write_text("VALUE = 1\n", encoding="utf-8")
        (self.root / "app" / "two.py").write_text("VALUE = 2\n", encoding="utf-8")
        (self.root / "requirements.lock").write_text("demo-req==1.0\n", encoding="utf-8")
        (self.root / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\nversion = '9.9.9'\n", encoding="utf-8"
        )
        (self.root / "Dockerfile").write_text("FROM scratch\n", encoding="utf-8")
        self.pin = self.root / "base-image-pin.json"
        self.pin.write_text(json.dumps(PIN), encoding="utf-8")
        self.manifest = self.root / "release-manifest.json"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build_and_write(self) -> dict[str, object]:
        manifest = build_manifest(pin_path=self.pin, root=self.root)
        self.manifest.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return manifest

    def test_build_then_verify_passes(self) -> None:
        self._build_and_write()
        self.assertEqual(verify_manifest(self.manifest, pin_path=self.pin, root=self.root), [])

    def test_tampered_dockerfile_fails_verify(self) -> None:
        self._build_and_write()
        (self.root / "Dockerfile").write_text("FROM tampered\n", encoding="utf-8")
        violations = verify_manifest(self.manifest, pin_path=self.pin, root=self.root)
        self.assertEqual(len(violations), 1)
        self.assertIn("dockerfile_sha256", violations[0])

    def test_tampered_app_source_fails_verify(self) -> None:
        self._build_and_write()
        (self.root / "app" / "one.py").write_text("VALUE = 999\n", encoding="utf-8")
        violations = verify_manifest(self.manifest, pin_path=self.pin, root=self.root)
        self.assertIn("app_tree_sha256", violations[0])

    def test_tampered_lock_fails_verify(self) -> None:
        self._build_and_write()
        (self.root / "requirements.lock").write_text("demo-req==2.0\n", encoding="utf-8")
        violations = verify_manifest(self.manifest, pin_path=self.pin, root=self.root)
        self.assertIn("lock_sha256", violations[0])

    def test_edited_manifest_digest_fails_verify(self) -> None:
        self._build_and_write()
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["app_version"] = "1.0.0"
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        violations = verify_manifest(self.manifest, pin_path=self.pin, root=self.root)
        self.assertIn("app_version", violations[0])

    def test_null_base_image_digest_round_trips(self) -> None:
        manifest = self._build_and_write()
        self.assertIsNone(manifest["base_image_digest"])
        self.assertEqual(verify_manifest(self.manifest, pin_path=self.pin, root=self.root), [])

    def test_missing_pin_file_raises(self) -> None:
        missing = self.root / "missing.json"
        with self.assertRaises(FileNotFoundError):
            build_manifest(pin_path=missing, root=self.root)


if __name__ == "__main__":
    unittest.main()
