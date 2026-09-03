"""Phase 43.2 runtime wiring: envelope cipher assembly + KEK-rotation sweep.

Contracts exercised end-to-end through create_app (ROADMAP_2_X §43.2): the
per-tenant envelope cipher is assembled into AppServices; disabling the
feature removes it (fail closed); provisioned tenants are enumerated by
``database.list_tenants``; and the housekeeping sweep re-wraps every tenant's
stored DEKs onto the active KEK after a rotation, converging to zero writes
and zero audit noise on repeated sweeps.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.envelope_crypto import EnvelopeTamperError, TenantEnvelopeCipher
from app.main import create_app


ADMIN_KEY = "admin-test-key-0001"
ACME_KEY = "acme-admin-key-0001"


def _public_resolve(_host: str, _port: int) -> list[str]:
    """Hermetic resolver so example.com is treated as public (test_webhooks
    pattern). Sandbox DNS maps example.com to reserved 198.18.x.x, which the
    SSRF guard rejects as private/loopback — without this the webhook tests
    get 422 before the envelope fail-closed path can assert its 503."""
    return ["93.184.216.34"]


class EnvelopeRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        root = Path(self._tmp.name)
        self.db_path = root / "runtime.db"
        self.kms_dir = root / "kek"
        principals = {
            ADMIN_KEY: {
                "tenant_id": "demo",
                "actor_id": "agent.admin",
                "role": "admin",
            },
            ACME_KEY: {
                "tenant_id": "acme",
                "actor_id": "acme.admin",
                "role": "admin",
            },
        }
        self.settings = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(principals),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            envelope_kms_dir=self.kms_dir,
        )
        self.client = TestClient(create_app(self.settings))
        self.services = cast(Any, self.client.app).state.services
        self.services.webhooks._resolve_host = _public_resolve

    def tearDown(self) -> None:
        self.client.close()
        self.services.database.close()
        self._tmp.cleanup()

    def _housekeeping_rewrap(self) -> Any:
        callbacks = [
            cb
            for cb in self.services.turn_worker.housekeeping
            if cb.__name__ == "_housekeeping_envelope_rewrap"
        ]
        self.assertEqual(len(callbacks), 1)
        return callbacks[0]

    def _rewrap_audit_count(self, event_type: str = "security.dek.rewrapped") -> int:
        with self.services.database.connect() as connection:
            rows = connection.execute(
                "SELECT COUNT(*) AS n FROM audit_events WHERE event_type = ?",
                (event_type,),
            ).fetchall()
        return int(rows[0]["n"])

    # -- AppServices assembly ---------------------------------------------

    def test_envelope_cipher_assembled_into_app_services(self) -> None:
        self.assertIsInstance(self.services.envelope_cipher, TenantEnvelopeCipher)
        # Fresh KMS stand-in boots with KEK v1.
        self.assertEqual(self.services.envelope_cipher.kms.active_version(), 1)

    def test_disabled_envelope_fails_closed(self) -> None:
        disabled = Settings(
            database_path=Path(self._tmp.name) / "plain.db",
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {"disabled-key-0001": {"tenant_id": "demo", "actor_id": "a", "role": "admin"}}
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            envelope_enabled=False,
            envelope_kms_dir=Path(self._tmp.name) / "kek-disabled",
        )
        client = TestClient(create_app(disabled))
        services = cast(Any, client.app).state.services
        try:
            self.assertIsNone(services.envelope_cipher)
            names = [cb.__name__ for cb in services.turn_worker.housekeeping]
            self.assertNotIn("_housekeeping_envelope_rewrap", names)
        finally:
            client.close()
            services.database.close()

    def test_invalid_rewrap_cadence_rejected_at_boot(self) -> None:
        bad = Settings(
            database_path=self.db_path,
            auth_mode="api_key",
            api_keys_json=json.dumps(
                {"invalid-key-0001": {"tenant_id": "demo", "actor_id": "a", "role": "admin"}}
            ),
            rate_limit_per_minute=10000,
            docs_enabled=False,
            envelope_rewrap_cadence_hours=0,
        )
        with self.assertRaises(ValueError):
            create_app(bad)

    def test_webhook_registration_fails_closed_with_503(self) -> None:
        # ROADMAP 43.2 contract (b): with envelope encryption mandated but the
        # KMS stand-in unable to boot, webhook registration is refused with a
        # 503 (fail closed) -- never a plaintext downgrade or a bare 500.
        from unittest.mock import patch

        with patch(
            "app.envelope_crypto.DiskKeyManagementService",
            side_effect=RuntimeError("kms down"),
        ):
            settings = Settings(
                database_path=Path(self._tmp.name) / "no-kms.db",
                auth_mode="api_key",
                api_keys_json=json.dumps(
                    {
                        ADMIN_KEY: {
                            "tenant_id": "demo",
                            "actor_id": "agent.admin",
                            "role": "admin",
                        }
                    }
                ),
                rate_limit_per_minute=10000,
                docs_enabled=False,
                envelope_enabled=True,
                envelope_kms_dir=Path(self._tmp.name) / "kek-fail",
            )
            client = TestClient(create_app(settings))
            services = cast(Any, client.app).state.services
            services.webhooks._resolve_host = _public_resolve
            try:
                self.assertIsNone(services.envelope_cipher)
                response = client.post(
                    "/api/webhooks",
                    json={
                        "url": "https://example.com/hook",
                        "events": ["conversation.created"],
                        "secret": "shhh-secret",
                    },
                    headers={"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"},
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertIn("envelope encryption is required", response.json()["detail"])
            finally:
                client.close()
                services.database.close()

    # -- tenant enumeration ------------------------------------------------

    def test_list_tenants_enumerates_provisioned_tenants(self) -> None:
        tenants = self.services.database.list_tenants()
        self.assertIn("demo", tenants)
        self.assertIn("acme", tenants)

    # -- housekeeping sweep ------------------------------------------------

    def test_housekeeping_rewrap_migrates_all_tenants(self) -> None:
        cipher = self.services.envelope_cipher
        for tenant_id in ("demo", "acme"):
            cipher.encrypt_text(tenant_id, "seed")
        for tenant_id in ("demo", "acme"):
            self.assertEqual(int(cipher.keystore.list_versions(tenant_id)[0]["kek_version"]), 1)
        cipher.kms.rotate()
        self.assertEqual(cipher.kms.active_version(), 2)

        self._housekeeping_rewrap()()

        for tenant_id in ("demo", "acme"):
            row = cipher.keystore.list_versions(tenant_id)[0]
            self.assertEqual(int(row["kek_version"]), 2)
        self.assertEqual(self._rewrap_audit_count(), 2)

    def test_repeated_sweep_is_idempotent_zero_write(self) -> None:
        cipher = self.services.envelope_cipher
        cipher.encrypt_text("demo", "seed")
        cipher.kms.rotate()
        self._housekeeping_rewrap()()
        before = self._rewrap_audit_count()
        self.assertEqual(before, 1)
        # Second sweep: no stored DEK is off the active KEK, so no writes and
        # no new audit events.
        for tenant_id in ("demo", "acme"):
            self.assertEqual(cipher.rewrap_tenant_deks(tenant_id), {"rewrapped": 0})
        self._housekeeping_rewrap()()
        self.assertEqual(self._rewrap_audit_count(), before)

    def test_runtime_cipher_round_trip_and_cross_tenant(self) -> None:
        cipher = self.services.envelope_cipher
        envelope = cipher.encrypt_text("demo", "restricted-payload")
        self.assertEqual(cipher.decrypt_text("demo", envelope), "restricted-payload")
        with self.assertRaises(EnvelopeTamperError):
            cipher.decrypt_text("acme", envelope)


if __name__ == "__main__":
    unittest.main()
