"""Phase 41 / SEC-004: unified credential lifecycle registry.

Covers the acceptance criteria:
- registry seeding keeps the deterministic API-key credential id and honours
  per-credential tenant declarations;
- the state machine rejects illegal transitions and treats repeat actions as
  idempotent;
- ``is_allowed`` enforces not_before / expires_at with the ±5s clock skew and
  the bounded retiring overlap window;
- the authenticator consults the persistent registry on every request, so a
  revoke from one instance is observed immediately by a fresh one (cross-instance
  revocation), and expiry / staged-activation rows the config does not know
  about are honoured.
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.config import Settings
from app.credentials import (
    MAX_CLOCK_SKEW_SECONDS,
    Credential,
    CredentialStatus,
    CredentialStore,
    InvalidCredentialTransition,
    key_ref_for,
    register_configured_from_json,
)
from app.db._util import utc_after_seconds, utc_now
from app.main import create_app

ADMIN_KEY = "creds-admin-key-001"
OPERATOR_KEY = "creds-op-key-001"
SECONDARY_TENANT_KEY = "creds-tenant-b-key-001"


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"},
        SECONDARY_TENANT_KEY: {
            "tenant_id": "tenant-b",
            "actor_id": "op.b",
            "role": "operator",
        },
    }
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


class CredentialRegistryUnitTests(unittest.TestCase):
    """The registry itself: seeding, transitions, fingerprint-only storage."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "registry.db"
        from app.database import Database

        self.db = Database(self.db_path)
        self.db.initialize()
        self.store = CredentialStore(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    def test_seeding_uses_deterministic_api_key_credential_id(self) -> None:
        count = register_configured_from_json(
            store=self.store,
            type="api_key",
            tenant_id="demo",
            raw_json=json.dumps(
                {
                    OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "o", "role": "operator"},
                    SECONDARY_TENANT_KEY: {
                        "tenant_id": "tenant-b",
                        "actor_id": "b",
                        "role": "operator",
                    },
                }
            ),
            created_at=utc_now(),
        )
        self.assertEqual(count, 2)
        # Deterministic id matches the historical credential_id exposed via /api/me.
        expected = key_ref_for(OPERATOR_KEY)[:12]
        row = self.store.get(expected)
        self.assertIsNotNone(row)
        assert row is not None
        self.assertEqual(row["status"], CredentialStatus.ACTIVE.value)
        # Full fingerprint stored, never the secret.
        self.assertEqual(row["key_ref"], key_ref_for(OPERATOR_KEY))
        self.assertNotIn(OPERATOR_KEY, json.dumps(row))
        # Per-credential tenant honoured.
        other = self.store.get(key_ref_for(SECONDARY_TENANT_KEY)[:12])
        assert other is not None
        self.assertEqual(other["tenant_id"], "tenant-b")

    def test_re_seed_is_idempotent(self) -> None:
        raw = json.dumps({OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "o", "role": "operator"}})
        for _ in range(3):
            register_configured_from_json(
                store=self.store,
                type="api_key",
                tenant_id="demo",
                raw_json=raw,
                created_at=utc_now(),
            )
        with self.db.connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM credential_registry").fetchone()["c"]
        self.assertEqual(count, 1)

    def test_illegal_transition_rejected(self) -> None:
        store = self.store
        store.register(
            credential_id="cred_x",
            type="api_key",
            tenant_id="demo",
            key_ref="ref-x",
            not_before="",
            created_at=utc_now(),
        )
        # active -> retired is legal.
        self.assertEqual(
            store.transition("cred_x", "retire", now=utc_now(), by="admin"), "retiring"
        )
        # retiring -> active is not.
        with self.assertRaises(InvalidCredentialTransition):
            store.transition("cred_x", "activate", now=utc_now(), by="admin")
        # repeated revoke is idempotent (mirrors legacy ON CONFLICT DO NOTHING).
        self.assertEqual(store.transition("cred_x", "revoke", now=utc_now(), by="admin"), "revoked")
        self.assertEqual(store.transition("cred_x", "revoke", now=utc_now(), by="admin"), "revoked")

    def test_concurrent_transition_cannot_overwrite_revocation(self) -> None:
        """Phase 41.4 (SEC-404): the transition UPDATE is compare-and-set.

        A concurrent revoke discovered after a retire read the same row must
        not let the retire overwrite status back to ``retiring`` (which
        ``is_allowed`` still accepts during the overlap window). The stale
        retire either fails with InvalidCredentialTransition or succeeds as a
        no-op only when the row already moved; it must never resurrect an
        active-key path.
        """
        store = self.store
        store.register(
            credential_id="cred_race",
            type="api_key",
            tenant_id="demo",
            key_ref="ref-race",
            not_before="",
            created_at=utc_now(),
        )
        # T1 revoke wins.
        self.assertEqual(
            store.transition("cred_race", "revoke", now=utc_now(), by="admin"), "revoked"
        )
        # T2 retire against the now-revoked row: the state machine forbids
        # revoked -> retiring, so a stale retire is rejected outright.
        with self.assertRaises(InvalidCredentialTransition):
            store.transition("cred_race", "retire", now=utc_now(), by="admin")
        row = store.get("cred_race")
        assert row is not None
        self.assertEqual(row["status"], CredentialStatus.REVOKED.value)
        # And the authenticator never accepts a revoked credential.
        self.assertFalse(Credential(**row).is_allowed(int(time.time())))

    def test_unknown_action_and_unknown_credential(self) -> None:
        with self.assertRaises(InvalidCredentialTransition):
            self.store.transition("cred_x", "explode", now=utc_now(), by="admin")
        with self.assertRaises(LookupError):
            self.store.transition("nope", "revoke", now=utc_now(), by="admin")

    def test_pending_activation_window(self) -> None:
        self.store.register(
            credential_id="cred_p",
            type="api_key",
            tenant_id="demo",
            key_ref="ref-p",
            not_before="",
            created_at=utc_now(),
            status=CredentialStatus.PENDING.value,
        )
        row = self.store.get("cred_p")
        assert row is not None
        self.assertFalse(Credential(**row).is_allowed(int(time.time())))
        self.store.transition("cred_p", "activate", now=utc_now(), by="admin")
        row = self.store.get("cred_p")
        assert row is not None
        self.assertTrue(Credential(**row).is_allowed(int(time.time())))

    def test_is_allowed_boundaries(self) -> None:
        now = int(time.time())
        active = Credential(
            credential_id="c1",
            type="api_key",
            tenant_id="demo",
            status="active",
            key_ref="k",
            not_before="",
            created_at=utc_now(),
        )
        self.assertTrue(active.is_allowed(now))
        # not_before in the future is rejected (within skew); past is fine.
        pending = Credential(
            credential_id="c2",
            type="api_key",
            tenant_id="demo",
            status="active",
            key_ref="k",
            not_before=utc_after_seconds(60),
            created_at=utc_now(),
        )
        self.assertFalse(pending.is_allowed(now))
        # Expiry: within max_skew tolerated at the boundary.
        expires_soon = utc_after_seconds(-3)
        soon = Credential(
            credential_id="c3",
            type="api_key",
            tenant_id="demo",
            status="active",
            key_ref="k",
            not_before="",
            expires_at=expires_soon,
            created_at=utc_now(),
        )
        self.assertTrue(soon.is_allowed(now))  # 3s inside the 5s skew
        expired = Credential(
            credential_id="c4",
            type="api_key",
            tenant_id="demo",
            status="active",
            key_ref="k",
            not_before="",
            expires_at=utc_after_seconds(-60),
            created_at=utc_now(),
        )
        self.assertFalse(expired.is_allowed(now))

    def test_retiring_overlap_window(self) -> None:
        now = int(time.time())
        from app.credentials import DEFAULT_ROTATION_OVERLAP_SECONDS

        retiring = Credential(
            credential_id="c5",
            type="api_key",
            tenant_id="demo",
            status="retiring",
            key_ref="k",
            not_before="",
            retired_at=utc_now(),
            created_at=utc_now(),
        )
        self.assertTrue(retiring.is_allowed(now))
        outside = int(time.time() + DEFAULT_ROTATION_OVERLAP_SECONDS + MAX_CLOCK_SKEW_SECONDS + 1)
        self.assertFalse(
            Credential(
                credential_id="c6",
                type="api_key",
                tenant_id="demo",
                status="retiring",
                key_ref="k",
                not_before="",
                retired_at=utc_now(),
                created_at=utc_now(),
            ).is_allowed(outside)
        )

    def test_fail_closed_states(self) -> None:
        now = int(time.time())
        for status in (CredentialStatus.REVOKED, CredentialStatus.EXPIRED):
            with self.subTest(status=status):
                self.assertFalse(
                    Credential(
                        credential_id="c",
                        type="api_key",
                        tenant_id="demo",
                        status=status,
                        key_ref="k",
                        not_before="",
                        created_at=utc_now(),
                    ).is_allowed(now)
                )
        # Retiring without a retired_at anchor must never be accepted.
        self.assertFalse(
            Credential(
                credential_id="c",
                type="api_key",
                tenant_id="demo",
                status="retiring",
                key_ref="k",
                not_before="",
                created_at=utc_now(),
            ).is_allowed(now)
        )


class CredentialRegistryIntegrationTests(unittest.TestCase):
    """Cross-instance semantics through the live HTTP app + authenticator."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "integ.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
        self.operator = {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _fresh_instance_headers(
        self, *, extra_principals: dict[str, dict[str, str]] | None = None
    ) -> dict[str, str]:
        """A brand-new app process against the same DB — the cross-instance peer.

        ``extra_principals`` mirrors a config change on the new peer (e.g. a
        staged key being promoted) so the new instance recognises the key.
        """
        self.services.database.close()
        self.client.close()
        principals = json.loads(_settings(self.db_path).effective_api_keys_json)
        principals.update(extra_principals or {})
        self.client = TestClient(
            create_app(_settings(self.db_path, api_keys_json=json.dumps(principals)))
        )
        self.services = cast(Any, self.client.app).state.services
        return {"X-API-Key": OPERATOR_KEY, "X-Tenant-Id": "demo"}

    def test_cross_instance_revocation_is_immediate(self) -> None:
        credential = self.client.get("/api/me", headers=self.operator).json()["credential_id"]
        self.assertEqual(
            self.client.post(
                f"/api/admin/keys/{credential}/revoke", headers=self.admin
            ).status_code,
            200,
        )
        headers = self._fresh_instance_headers()
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)

    def test_registry_row_change_is_observed_without_restart(self) -> None:
        """The authenticator reads the persisted registry per request — a state
        change made by another writer (e.g. a lifecycle worker, or a second
        instance) is effective immediately."""
        credential = self.client.get("/api/me", headers=self.operator).json()["credential_id"]
        # Another instance retires then revokes the credential directly.
        self.services.credential_lifecycle.rotate(credential, now=utc_now(), by="admin")
        self.services.credential_lifecycle.revoke(credential, now=utc_now(), by="admin")
        self.assertEqual(
            self.client.get("/api/conversations", headers=self.operator).status_code, 401
        )

    def test_expired_row_blocks_new_instance(self) -> None:
        credential = self.client.get("/api/me", headers=self.operator).json()["credential_id"]
        row = self.services.credential_store.get(credential)
        assert row is not None
        with self.services.database.connect() as conn:
            conn.execute(
                "UPDATE credential_registry SET status='expired', expires_at=? WHERE credential_id=?",
                (utc_after_seconds(-10), credential),
            )
        headers = self._fresh_instance_headers()
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)

    def test_staged_activation_blocks_until_not_before(self) -> None:
        # Seed a credential whose not_before lies in the future, then prove a
        # fresh instance refuses it until the clock passes the boundary.
        pending_key = "creds-staged-key-0001"
        credential_id = key_ref_for(pending_key)[:12]
        self.services.credential_store.register(
            credential_id=credential_id,
            type="api_key",
            tenant_id="demo",
            key_ref=key_ref_for(pending_key),
            not_before=utc_after_seconds(3600),
            created_at=utc_now(),
        )
        headers = {"X-API-Key": pending_key, "X-Tenant-Id": "demo"}
        # The live instance, which does not know the key, rejects it (401, not 404).
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)
        # Promote the staged key on a fresh peer whose config declares it; the
        # registry's not_before still vetoes it until the boundary passes.
        promoted = {pending_key: {"tenant_id": "demo", "actor_id": "o", "role": "operator"}}
        headers = self._fresh_instance_headers(extra_principals=promoted)
        headers["X-API-Key"] = pending_key
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)
        # Move the window to the past, staged activation unlocks it.
        with self.services.database.connect() as conn:
            conn.execute(
                "UPDATE credential_registry SET not_before=?, status='active' WHERE credential_id=?",
                (utc_after_seconds(-60), credential_id),
            )
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 200)

    def test_legacy_revocations_seed_registry_status(self) -> None:
        """Backward-path: a key revoked through the legacy table (old instance)
        is still refused by a registry-aware peer because list_revoked_api_keys
        still feeds the in-memory set."""
        credential = self.client.get("/api/me", headers=self.operator).json()["credential_id"]
        self.services.database.revoke_api_key(credential, "admin.user")
        headers = self._fresh_instance_headers()
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)


if __name__ == "__main__":
    unittest.main()
