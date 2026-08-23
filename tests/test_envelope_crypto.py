"""Phase 43.2: per-tenant envelope encryption (ROADMAP_2_X §8 / 43.2).

Contracts: a restricted payload encrypts under a per-tenant DEK that is itself
wrapped under a KMS-held KEK; the envelope carries its key versions and a copy
of the wrapped DEK so a restored backup can decrypt; rotation is online
(old envelopes keep decrypting, new writes use the new DEK); KEK revocation
fails closed for anything not re-wrapped; and ciphertext swapped between
tenants or key generations fails authentication.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.envelope_crypto import (
    DatabaseDekKeystore,
    DiskKeyManagementService,
    EnvelopeCryptoError,
    EnvelopeTamperError,
    TenantEnvelopeCipher,
    ensure_searchable_fields_are_classified,
)
from app.redaction import FIELD_REGISTRY


def _seed(db: Database) -> None:
    db.initialize()
    db.ensure_tenant("t1")
    db.ensure_tenant("t2")


class EnvelopeCryptoTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.db = Database(root / "envelope.db")
        _seed(self.db)
        self.kms = DiskKeyManagementService(root / "kek")
        self.kms.generate_initial()
        self.keystore = DatabaseDekKeystore(self.db)
        self.cipher = TenantEnvelopeCipher(self.kms, self.keystore, database=self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- DEK lifecycle ----------------------------------------------------

    def test_active_dek_provisioned_on_demand(self) -> None:
        row = self.cipher.active_dek("t1")
        self.assertEqual(row["status"], "active")
        self.assertEqual(int(row["dek_version"]), 1)
        # Provisioning is idempotent: the same DEK comes back.
        self.assertEqual(int(self.cipher.active_dek("t1")["dek_version"]), 1)
        # A different tenant gets its own DEK.
        self.assertEqual(int(self.cipher.active_dek("t2")["dek_version"]), 1)
        self.assertNotEqual(
            self.cipher.active_dek("t1")["wrapped_dek"],
            self.cipher.active_dek("t2")["wrapped_dek"],
        )

    def test_rotate_tenant_dek_preserves_history(self) -> None:
        self.cipher.active_dek("t1")
        new_version = self.cipher.rotate_tenant_dek("t1")
        self.assertEqual(new_version, 2)
        versions = self.keystore.list_versions("t1")
        self.assertEqual([int(v["dek_version"]) for v in versions], [1, 2])
        self.assertEqual(versions[0]["status"], "rotated")
        self.assertEqual(versions[1]["status"], "active")

    def test_rotate_then_encrypt_uses_new_dek(self) -> None:
        self.cipher.active_dek("t1")
        self.cipher.rotate_tenant_dek("t1")
        envelope = self.cipher.encrypt_text("t1", "hello")
        self.assertEqual(int(envelope["dek_version"]), 2)

    def test_revoke_metadata_transitions(self) -> None:
        self.cipher.active_dek("t1")
        self.keystore.set_status("t1", 1, "rotated")
        self.keystore.set_status("t1", 1, "revoked")
        with self.assertRaises(EnvelopeCryptoError):
            self.keystore.set_status("t1", 1, "active")

    def test_revoked_dek_fails_closed(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "secret-payload")
        self.keystore.set_status("t1", int(envelope["dek_version"]), "revoked")
        with self.assertRaises(EnvelopeCryptoError):
            self.cipher.decrypt_text("t1", envelope)

    # -- payload round-trip / tamper --------------------------------------

    def test_encrypt_decrypt_round_trip(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "top secret")
        self.assertEqual(self.cipher.decrypt_text("t1", envelope), "top secret")

    def test_cross_tenant_envelope_rejected(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "top secret")
        with self.assertRaises(EnvelopeTamperError):
            self.cipher.decrypt_text("t2", envelope)

    def test_tampered_ciphertext_rejected(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "top secret")
        forged = dict(envelope)
        forged["ciphertext"] = envelope["ciphertext"][:-4] + "AAAA"
        with self.assertRaises(EnvelopeTamperError):
            self.cipher.decrypt_text("t1", forged)

    def test_tampered_nonce_rejected(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "top secret")
        forged = dict(envelope)
        forged["nonce"] = "AAAA" + envelope["nonce"][4:]
        with self.assertRaises(EnvelopeTamperError):
            self.cipher.decrypt_text("t1", forged)

    def test_malformed_envelope_rejected(self) -> None:
        with self.assertRaises(EnvelopeTamperError):
            self.cipher.decrypt_text("t1", {"v": 999, "tenant_id": "t1"})

    def test_unknown_tenant_has_no_dek(self) -> None:
        with self.assertRaises(EnvelopeCryptoError):
            self.cipher.active_dek("ghost")

    # -- KMS rotation & re-wrap -------------------------------------------

    def test_kek_rotation_keeps_envelopes_decryptable(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "stable")
        self.kms.rotate()
        new_envelope = self.cipher.encrypt_text("t1", "new write")
        # Old ciphertext decrypts through its historical KEK version.
        self.assertEqual(self.cipher.decrypt_text("t1", envelope), "stable")
        # New writes carry the current KEK version.
        self.assertEqual(int(new_envelope["kek_version"]), 2)

    def test_rewrap_moves_stored_deks_to_active_kek(self) -> None:
        # Provision a DEK, rotate the KEK, then re-wrap stored material onto
        # the active KEK.
        self.cipher.encrypt_text("t1", "seed")
        self.kms.rotate()
        result = self.cipher.rewrap_tenant_deks("t1")
        self.assertEqual(result["rewrapped"], 1)
        row = self.keystore.list_versions("t1")[0]
        self.assertEqual(int(row["kek_version"]), 2)

    def test_lazy_forward_rewrap_on_kek_rotation(self) -> None:
        # New writes after a KEK rotation automatically migrate the stored DEK
        # onto the active KEK (no manual re-wrap needed).
        self.cipher.encrypt_text("t1", "seed")
        self.kms.rotate()
        envelope = self.cipher.encrypt_text("t1", "post-rotation write")
        self.assertEqual(int(envelope["kek_version"]), 2)
        self.assertEqual(int(self.keystore.list_versions("t1")[0]["kek_version"]), 2)

    def test_rewrap_skips_revoked_deks(self) -> None:
        self.cipher.encrypt_text("t1", "x")
        self.keystore.set_status("t1", 1, "revoked")
        result = self.cipher.rewrap_tenant_deks("t1")
        self.assertEqual(result["rewrapped"], 0)

    def test_kek_revocation_fails_closed_for_unrewrapped(self) -> None:
        # Encrypt under KEK v1, rotate to v2, then revoke v1 while the stored
        # DEK is still wrapped there. The next unwrap must fail closed rather
        # than silently re-key.
        envelope = self.cipher.encrypt_text("t1", "still under v1")
        self.kms.rotate()
        self.kms.revoke(1)
        with self.assertRaises(EnvelopeCryptoError):
            self.cipher.decrypt_text("t1", envelope)

    # -- backup restore / self-contained envelope -------------------------

    def test_envelope_carries_wrapped_dek_for_restore(self) -> None:
        envelope = self.cipher.encrypt_text("t1", "recover me")
        self.assertIn("wrapped_dek", envelope)
        self.assertIn("kek_version", envelope)
        self.assertEqual(self.cipher.decrypt_text("t1", envelope), "recover me")


class EnvelopeFormatTests(unittest.TestCase):
    """Envelope shape and the searchable-field classification gate."""

    def test_envelope_is_json_safe_ascii(self) -> None:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            root = Path(tmp)
            db = Database(root / "f.db")
            _seed(db)
            kms = DiskKeyManagementService(root / "kek")
            kms.generate_initial()
            cipher = TenantEnvelopeCipher(kms, DatabaseDekKeystore(db), database=db)
            envelope = cipher.encrypt_text("t1", "u")
            for value in envelope.values():
                self.assertIsInstance(value, (str, int))

    def test_searchable_fields_require_classification(self) -> None:
        # A field that exists in FIELD_REGISTRY passes.
        classified = ensure_searchable_fields_are_classified(["customer_name", "channel"])
        self.assertEqual(classified["customer_name"], "confidential")
        self.assertEqual(classified["channel"], "public")
        # An unclassified field must not be indexable.
        with self.assertRaises(ValueError):
            ensure_searchable_fields_are_classified(["made_up_searchable_field"])

    def test_fts_indexes_pass_classification_gate_at_startup(self) -> None:
        # 43.2 bullet 3 wiring: Database.initialize() runs both FTS index
        # definitions through the classification gate, so the shipped column
        # sets are classified (this test failing means an FTS column was
        # added without a FIELD_REGISTRY entry).
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            db = Database(Path(tmp) / "fts-gate.db")
            db.initialize()

        knowledge_columns = ["title", "content", "tags", "category", "search_terms"]
        message_columns = ["content", "search_terms"]
        for name in knowledge_columns + message_columns:
            self.assertIn(name, FIELD_REGISTRY)
        classifications = ensure_searchable_fields_are_classified(knowledge_columns)
        self.assertEqual(classifications["category"], "public")
        self.assertEqual(classifications["title"], "confidential")


if __name__ == "__main__":
    unittest.main()
