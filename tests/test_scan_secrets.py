"""Phase 41.2 / SEC-003: repository secret scanner gate tests.

Verifies the scanner flags committed credentials in known shapes, ignores
build/local state, and never mistakes npm registry integrity hashes or
reference-only constant names for secrets.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.scan_secrets import scan


class ScanSecretsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write(self, relpath: str, content: str) -> Path:
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def test_anthropic_token_is_flagged(self) -> None:
        self._write("leak.py", 'CRED = "sk-ant-api03-0123456789abcdefghijklmnopqrstuvwxyzABCDEF"\n')
        hits = scan(self.root)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0][1], "anthropic-key")
        self.assertEqual(hits[0][0], "leak.py")

    def test_private_key_is_flagged(self) -> None:
        self._write(
            "secrets/id_rsa",
            "-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n",
        )
        hits = scan(self.root)
        self.assertEqual(hits[0][1], "private-key")

    def test_aws_and_github_tokens_are_flagged(self) -> None:
        # GitHub PATs are exactly 36 chars after ``ghp_``; the AWS sample is
        # the canonical 20-char example from the AWS documentation.
        self._write(
            "config.py",
            "AWS = 'AKIAIOSFODNN7EXAMPLE'\nPAT = 'ghp_0123456789abcdefghijklmnopqrstuvwxyz'\n",
        )
        names = {hit[1] for hit in scan(self.root)}
        self.assertIn("aws-access-key", names)
        self.assertIn("github-pat", names)

    def test_harmless_constant_name_is_clean(self) -> None:
        self._write(
            "app.js", "const apiKeyRef = 'name-only-reference';\nconst secretTarget = 42;\n"
        )
        self.assertEqual(scan(self.root), [])

    def test_node_modules_tree_is_skipped(self) -> None:
        self._write(
            "node_modules/pkg/index.js", "var key = 'ghp_0123456789abcdefghijklmnopqrstuvwxyzab';\n"
        )
        self.assertEqual(scan(self.root), [])

    def test_npm_lock_integrity_hash_is_not_a_secret(self) -> None:
        # A 43-char base64 run trailing in '=' would normally trip the
        # Fernet-shaped matcher; package-lock.json is the canonical false
        # positive and must be skipped wholesale.
        integrity = f'"sha512-{"A" * 43}=="'
        self._write("package-lock.json", '{"packages":{"x":{"integrity":' + integrity + "}}\n")
        self.assertEqual(scan(self.root), [])

    def test_artifacts_dir_is_skipped(self) -> None:
        self._write(
            "artifacts/debug.pem", "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"
        )
        self.assertEqual(scan(self.root), [])

    def test_rust_target_dir_is_skipped(self) -> None:
        # src-tauri incremental caches embed CSP hashes ('sha256-<43 b64>=')
        # that trip the Fernet matcher; Cargo build state is gitignored and
        # must be skipped like node_modules.
        csp_hash = "sha256-" + "A" * 43 + "='sha256-" + "B" * 43 + "='"
        self._write("src-tauri/target/debug/incremental/lib.helix.o", csp_hash + "\n")
        self.assertEqual(scan(self.root), [])


if __name__ == "__main__":
    unittest.main()
