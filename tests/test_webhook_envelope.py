"""Phase 43.2 (ROADMAP contract (b)): envelope-encrypted webhook secrets.

The ``webhook_endpoints.secret`` column is a restricted field. With a
``TenantEnvelopeCipher`` wired in, registration stores an envelope (JSON) and
the ``secret_format`` discriminator instead of plaintext; delivery decrypts
with the tenant DEK before signing, and any secret that cannot be decrypted
dead-letters the delivery (fail closed) rather than signing with an unverified
value. Legacy plaintext rows keep working in the same deployment.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.database import Database
from app.envelope_crypto import (
    DatabaseDekKeystore,
    DiskKeyManagementService,
    EnvelopeCryptoError,
    TenantEnvelopeCipher,
)
from app.migrations import all_migrations, run_migrations
from app.webhooks import (
    EVENT_CONVERSATION_CREATED,
    WebhookConfig,
    WebhookService,
    _sign,
)


def _public_resolve(_host: str, _port: int) -> list[str]:
    """Offline resolver: treat every hostname as a public unicast address."""
    return ["93.184.216.34"]


class RecordingTransport:
    """Records every delivery POST; returns a configurable status."""

    def __init__(self, status: int = 200) -> None:
        self.calls: list[tuple[str, dict[str, str], bytes]] = []
        self.status = status

    def __call__(
        self, url: str, headers: dict[str, str], body: bytes, timeout: float
    ) -> tuple[int, str | None]:
        self.calls.append((url, dict(headers), body))
        return self.status, None


def _make_cipher(database: Database, kms_dir: Path) -> TenantEnvelopeCipher:
    kms = DiskKeyManagementService(kms_dir)
    kms.generate_initial()
    return TenantEnvelopeCipher(kms, DatabaseDekKeystore(database), database=database)


class WebhookEnvelopeServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "test.db"
        self.database = Database(self.db_path, pool_size=2)
        with self.database.connect() as conn:
            run_migrations(conn, all_migrations())
        self.database.ensure_tenant("tenant-1", "One")
        self.database.ensure_tenant("tenant-2", "Two")
        self.transport = RecordingTransport()
        self.cipher = _make_cipher(self.database, root / "kms")
        self.webhooks = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
            envelope_cipher=self.cipher,
        )

    def tearDown(self) -> None:
        self.database.close()
        self._tmp.cleanup()

    def _row(self, endpoint_id: str) -> tuple[str, str]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT secret, secret_format FROM webhook_endpoints WHERE id = ?",
                (endpoint_id,),
            ).fetchone()
        return row["secret"], row["secret_format"]

    def _delivery(self, endpoint_id: str) -> dict[str, object]:
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT status, attempts, last_error FROM webhook_deliveries "
                "WHERE endpoint_id = ? ORDER BY created_at DESC LIMIT 1",
                (endpoint_id,),
            ).fetchone()
        return {
            "status": row["status"],
            "attempts": row["attempts"],
            "last_error": row["last_error"],
        }

    # ------------------------------------------------------------- registration

    def test_register_with_cipher_stores_envelope_not_plaintext(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        stored, fmt = self._row(endpoint["id"])
        self.assertEqual(fmt, "envelope")
        envelope = json.loads(stored)
        # The stored value is an envelope carrying its own key versions and a
        # wrapped DEK copy -- never the plaintext signing secret.
        self.assertNotIn("shhh-secret", stored)
        self.assertEqual(envelope["tenant_id"], "tenant-1")
        self.assertIn("wrapped_dek", envelope)
        self.assertIn("ciphertext", envelope)

    def test_register_without_cipher_stores_plaintext(self) -> None:
        legacy = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
        )
        endpoint = legacy.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "plain-secret"
        )
        stored, fmt = self._row(endpoint["id"])
        self.assertEqual(fmt, "plain")
        self.assertEqual(stored, "plain-secret")

    def test_register_refused_when_envelope_required_but_unavailable(self) -> None:
        # ROADMAP 43.2 fail-closed: envelope feature enabled but the cipher
        # could not boot -- never silently downgrade to plaintext.
        degraded = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
            envelope_required=True,
        )
        with self.assertRaises(EnvelopeCryptoError) as raised:
            degraded.register_endpoint(
                "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh"
            )
        self.assertEqual(raised.exception.status_code, 503)

    # ---------------------------------------------------------------- delivery

    def test_deliver_signs_with_decrypted_secret(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 1, "retried": 0, "dead": 0})
        url, headers, body = self.transport.calls[0]
        self.assertEqual(url, "https://example.com/hook")
        timestamp = headers["X-Helix-Timestamp"]
        self.assertEqual(headers["X-Helix-Signature"], _sign("shhh-secret", timestamp, body))
        delivery = self._delivery(endpoint["id"])
        self.assertEqual(delivery["status"], "delivered")
        self.assertEqual(delivery["attempts"], 1)

    def test_legacy_plain_rows_keep_delivering_in_mixed_deployment(self) -> None:
        # A cipher-less service registers the plaintext row (legacy); the
        # envelope-aware service must still deliver it unchanged.
        legacy = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
        )
        legacy.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "plain-secret"
        )
        legacy.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 1, "retried": 0, "dead": 0})
        _, headers, body = self.transport.calls[0]
        timestamp = headers["X-Helix-Timestamp"]
        self.assertEqual(headers["X-Helix-Signature"], _sign("plain-secret", timestamp, body))

    def test_envelope_secret_dead_letters_when_cipher_unavailable(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        # A degraded instance (envelope feature enabled but cipher did not
        # boot) must never sign with an unverifiable secret -- fail closed.
        degraded = WebhookService(
            self.database,
            transport=self.transport,
            config=WebhookConfig(max_attempts=3),
            resolve_host=_public_resolve,
            envelope_required=True,
        )
        results = degraded.deliver_pending()
        self.assertEqual(results, {"delivered": 0, "retried": 0, "dead": 1})
        self.assertEqual(self.transport.calls, [])
        delivery = self._delivery(endpoint["id"])
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["last_error"], "envelope secret could not be decrypted")

    def test_tampered_envelope_dead_letters(self) -> None:
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        stored, _ = self._row(endpoint["id"])
        envelope = json.loads(stored)
        envelope["ciphertext"] = envelope["ciphertext"][:-4] + "0000"
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_endpoints SET secret = ? WHERE id = ?",
                (json.dumps(envelope, sort_keys=True), endpoint["id"]),
            )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 0, "retried": 0, "dead": 1})
        delivery = self._delivery(endpoint["id"])
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["last_error"], "envelope secret could not be decrypted")

    def test_envelope_missing_keys_dead_letters_not_stuck(self) -> None:
        # A JSON-valid envelope missing its key-material fields must dead-letter
        # (fail closed) rather than leak a KeyError into the worker loop and
        # leave the delivery re-claimable forever.
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_endpoints SET secret = ? WHERE id = ?",
                (json.dumps({"v": 1, "tenant_id": "tenant-1"}), endpoint["id"]),
            )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 0, "retried": 0, "dead": 1})
        delivery = self._delivery(endpoint["id"])
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["last_error"], "envelope secret could not be decrypted")
        # No HTTP round-trip happened, so the delivery records no response code.
        with self.database.connect() as conn:
            row = conn.execute(
                "SELECT last_response_code FROM webhook_deliveries WHERE endpoint_id = ?",
                (endpoint["id"],),
            ).fetchone()
        self.assertIsNone(row["last_response_code"])

    def test_cross_tenant_delivery_swap_dead_letters(self) -> None:
        # An envelope is bound to its tenant via AAD; a delivery re-routed
        # under another tenant must fail tenant binding and dead-letter.
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        self.webhooks.emit_event(
            "tenant-1", EVENT_CONVERSATION_CREATED, {"conversation_id": "c1"}, "evt-1"
        )
        with self.database.connect() as conn:
            conn.execute(
                "UPDATE webhook_deliveries SET tenant_id = 'tenant-2' WHERE endpoint_id = ?",
                (endpoint["id"],),
            )
        results = self.webhooks.deliver_pending()
        self.assertEqual(results, {"delivered": 0, "retried": 0, "dead": 1})
        delivery = self._delivery(endpoint["id"])
        self.assertEqual(delivery["status"], "dead")
        self.assertEqual(delivery["last_error"], "envelope secret could not be decrypted")

    def test_cross_tenant_endpoint_isolation(self) -> None:
        # Registration stores the envelope under the registering tenant; the
        # other tenant cannot decrypt it (AAD binds the ciphertext).
        endpoint = self.webhooks.register_endpoint(
            "tenant-1", "https://example.com/hook", [EVENT_CONVERSATION_CREATED], "shhh-secret"
        )
        stored, _ = self._row(endpoint["id"])
        from app.envelope_crypto import EnvelopeTamperError

        with self.assertRaises(EnvelopeTamperError):
            self.cipher.decrypt_text("tenant-2", json.loads(stored))


if __name__ == "__main__":
    unittest.main()
