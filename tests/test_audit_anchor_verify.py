"""Audit anchor signing + verification (Phase 41.3, SEC-005) — direct branch
coverage.

Complements tests/test_audit_anchors.py (subprocess-level fixtures) with the
pure function branches of verify_signed_anchor / verify_anchor_vs_head:
structural failures, base64/der decoding, kid trust, signature mismatch,
environment/hash/seq mismatches and the monotonic advance rule.
"""

from __future__ import annotations

import base64
import hashlib
import unittest

from app.audit_anchor import (
    Ed25519KmsSigner,
    build_anchor_claim,
    compute_claim_digest,
    public_key_id,
    verify_anchor_vs_head,
    verify_signed_anchor,
)


def _signer(seed: bytes = b"seed") -> Ed25519KmsSigner:
    digest = hashlib.sha256(seed).digest()
    return Ed25519KmsSigner(private_key=digest)


def _claim(signer: Ed25519KmsSigner | None = None, **overrides: object) -> dict[str, object]:
    signer = signer or _signer()
    claim = build_anchor_claim(
        environment="test",
        last_seq=3,
        last_hash="hash3",
        signer=signer,
        timestamp="2026-09-01T00:00:00Z",
    )
    claim.update(overrides)
    return claim


class VerifySignedAnchorTests(unittest.TestCase):
    def test_valid_claim_passes(self) -> None:
        signer = _signer()
        problems = verify_signed_anchor(_claim(signer), trusted_kids=(signer.kid,))
        self.assertEqual(problems, [])

    def test_missing_fields_reported(self) -> None:
        claim = _claim()
        del claim["last_hash"]
        problems = verify_signed_anchor(claim)
        self.assertEqual(len(problems), 1)
        self.assertIn("missing fields", problems[0])

    def test_non_integer_last_seq_reported(self) -> None:
        claim = _claim(last_seq="not-an-int")
        problems = verify_signed_anchor(claim)
        self.assertIn("last_seq is not an integer", problems[0])

    def test_wrong_schema_version_reported(self) -> None:
        claim = _claim(schema_version=99)
        problems = verify_signed_anchor(claim)
        self.assertTrue(any("schema_version" in p for p in problems))

    def test_bad_base64_reported(self) -> None:
        signer = _signer()
        claim = _claim(signer)
        claim["signature"] = "not base64!!!"
        problems = verify_signed_anchor(claim, trusted_kids=(signer.kid,))
        self.assertTrue(any("base64" in p for p in problems))

    def test_wrong_key_length_reported(self) -> None:
        signer = _signer()
        claim = _claim(signer)
        claim["public_key"] = base64.b64encode(b"tooshort").decode("ascii")
        problems = verify_signed_anchor(claim, trusted_kids=(signer.kid,))
        self.assertTrue(any("Ed25519 key" in p for p in problems))

    def test_kid_mismatch_reported(self) -> None:
        signer = _signer(b"other")
        claim = _claim(signer)
        claim["kid"] = "ed25519-deadbeefdead"
        problems = verify_signed_anchor(claim)
        self.assertTrue(any("does not match" in p for p in problems))

    def test_untrusted_kid_reported(self) -> None:
        signer = _signer()
        problems = verify_signed_anchor(_claim(signer), trusted_kids=("ed25519-other",))
        self.assertTrue(any("trusted set" in p for p in problems))

    def test_signature_mismatch_reported(self) -> None:
        signer = _signer()
        claim = _claim(signer)
        # Re-sign the same fields with a different key: signature won't verify.
        other = _signer(b"forger")
        claim["public_key"] = base64.b64encode(other.public_key).decode("ascii")
        claim["kid"] = other.kid
        problems = verify_signed_anchor(claim, trusted_kids=(signer.kid, other.kid))
        self.assertTrue(any("signature" in p for p in problems))

    def test_unsupported_scheme_reported(self) -> None:
        signer = _signer()
        claim = _claim(signer, signature_scheme="rsa2048")
        problems = verify_signed_anchor(claim, trusted_kids=(signer.kid,))
        self.assertTrue(any("unsupported" in p for p in problems))


class VerifyAnchorVsHeadTests(unittest.TestCase):
    def test_matching_head_passes(self) -> None:
        signer = _signer()
        claim = _claim(signer)
        problems = verify_anchor_vs_head(
            claim,
            chain_head="hash3",
            chain_last_seq=3,
            environment="test",
        )
        self.assertEqual(problems, [])

    def test_environment_mismatch_reported(self) -> None:
        signer = _signer()
        problems = verify_anchor_vs_head(
            _claim(signer),
            chain_head="hash3",
            chain_last_seq=3,
            environment="prod",
        )
        self.assertTrue(any("environment" in p for p in problems))

    def test_hash_mismatch_reported(self) -> None:
        signer = _signer()
        problems = verify_anchor_vs_head(
            _claim(signer, last_hash="hashX"),
            chain_head="hash3",
            chain_last_seq=3,
            environment="test",
        )
        self.assertTrue(any("last_hash" in p for p in problems))

    def test_seq_mismatch_reported(self) -> None:
        signer = _signer()
        problems = verify_anchor_vs_head(
            _claim(signer, last_seq=2),
            chain_head="hash3",
            chain_last_seq=3,
            environment="test",
        )
        self.assertTrue(any("last_seq" in p for p in problems))

    def test_non_advancing_seq_reported(self) -> None:
        signer = _signer()
        problems = verify_anchor_vs_head(
            _claim(signer, last_seq=2),
            chain_head="hash3",
            chain_last_seq=3,
            environment="test",
            previous_last_seq=2,
        )
        self.assertTrue(any("advance" in p or "monotonic" in p for p in problems))

    def test_non_integer_last_seq_reported(self) -> None:
        signer = _signer()
        problems = verify_anchor_vs_head(
            _claim(signer, last_seq="x"),
            chain_head="hash3",
            chain_last_seq=3,
            environment="test",
        )
        self.assertTrue(any("not an integer" in p for p in problems))


class AnchorDigestTests(unittest.TestCase):
    def test_compute_claim_digest_is_stable(self) -> None:
        a = compute_claim_digest(environment="t", last_seq=1, last_hash="h", timestamp="ts")
        b = compute_claim_digest(environment="t", last_seq=1, last_hash="h", timestamp="ts")
        self.assertEqual(a, b)

    def test_public_key_id_is_sha256_prefix(self) -> None:
        signer = _signer()
        self.assertTrue(public_key_id(signer.public_key).startswith("ed25519-"))
        self.assertEqual(public_key_id(signer.public_key), signer.kid)

    def test_signer_generate_creates_working_key(self) -> None:
        signer = Ed25519KmsSigner.generate()
        self.assertTrue(signer.public_key)
        claim = build_anchor_claim(
            environment="test",
            last_seq=1,
            last_hash="h",
            signer=signer,
        )
        # Signer's own key signs and verifies.
        problems = verify_signed_anchor(claim, trusted_kids=(signer.kid,))
        self.assertEqual(problems, [])

    def test_build_claim_without_arguments_uses_utc_now(self) -> None:
        signer = _signer()
        claim = build_anchor_claim(
            environment="test",
            last_seq=1,
            last_hash="h",
            signer=signer,
        )
        # No explicit timestamp: the claim carries a real UTC ISO timestamp
        # (+00:00 offset — Windows isoformat omits Z for UTC with
        # timespec=milliseconds).
        self.assertIn("T", str(claim["timestamp"]))
        self.assertTrue(str(claim["timestamp"]).endswith(("Z", "+00:00")))


if __name__ == "__main__":
    unittest.main()
