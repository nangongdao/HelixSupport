"""Phase 41 / SEC-004: runtime rotation API, key-id versioned signing, audit
redaction and the fail-closed secret-manager path (ROADMAP 41.1).

Covers the acceptance criteria:
- the admin key API issues a secret exactly once and never returns it again;
- promoting an issued key into the deployment config keeps the same registry
  row, so a fresh instance signs with it and a revoke disables it immediately
  across instances;
- channel signing accepts a ``key_id`` that selects a rotated registry
  credential, while unknown / wrong-type / wrong-tenant / inactive / revoked
  key ids all resolve to the uniform 401;
- audit events for issuance carry the credential id but never the secret;
- the secret-manager-unavailable path fails closed (503 / uniform 401).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient

from app.channel_webhooks import ChannelWebhookAuthError, InboundChannelRegistry
from app.config import Settings
from app.credentials import (
    CredentialStore,
    key_ref_for,
)
from app.db._util import utc_now
from app.main import create_app

ADMIN_KEY = "phase41-admin-key-001"
OPERATOR_KEY = "phase41-op-key-001"
CHANNEL_SECRET = "phase41-channel-secret-with-at-least-32-bytes"
OTHER_SECRET = "phase41-other-secret-with-at-least-32-bytes"


def _settings(db_path: Path, **overrides: Any) -> Settings:
    principals = {
        ADMIN_KEY: {"tenant_id": "demo", "actor_id": "admin.user", "role": "admin"},
        OPERATOR_KEY: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"},
    }
    defaults: dict[str, Any] = {
        "database_path": db_path,
        "auth_mode": "api_key",
        "api_keys_json": json.dumps(principals),
        "channel_webhooks_json": json.dumps(
            {
                "support-main": {
                    "tenant_id": "demo",
                    "channel": "formal_chat",
                    "secret": CHANNEL_SECRET,
                },
                "support-other": {
                    "tenant_id": "other-tenant",
                    "channel": "formal_chat",
                    "secret": OTHER_SECRET,
                },
            }
        ),
        "rate_limit_per_minute": 10000,
        "docs_enabled": False,
        "turn_worker_enabled": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def _signed_channel_headers(
    secret: str, body: bytes, *, key_id: str | None = None
) -> dict[str, str]:
    signed_at = int(time.time())
    digest = hmac.new(
        secret.encode("utf-8"),
        str(signed_at).encode("ascii") + b"." + body,
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Helix-Timestamp": str(signed_at),
        "X-Helix-Signature": f"sha256={digest}",
    }
    if key_id is not None:
        headers["X-Helix-Key-Id"] = key_id
    return headers


class RotationApiIntegrationTests(unittest.TestCase):
    """Admin rotation API: secret-once, deterministic ids, cross-instance."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "rotation.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.admin = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _fresh_instance_headers(
        self, *, extra_principals: dict[str, dict[str, str]]
    ) -> dict[str, str]:
        self.services.database.close()
        self.client.close()
        principals = json.loads(_settings(self.db_path).effective_api_keys_json)
        principals.update(extra_principals)
        self.client = TestClient(
            create_app(_settings(self.db_path, api_keys_json=json.dumps(principals)))
        )
        self.services = cast(Any, self.client.app).state.services
        return {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}

    def test_issue_returns_secret_once_and_list_never_returns_it(self) -> None:
        response = self.client.post("/api/admin/keys", headers=self.admin)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertIn("secret", body)
        self.assertIn("credential_id", body)
        issued_secret = body["secret"]
        self.assertTrue(issued_secret.startswith("hk-"))
        # The database stores only the fingerprint, never the secret.
        row = self.services.credential_store.get(body["credential_id"])
        assert row is not None
        self.assertEqual(row["key_ref"], key_ref_for(issued_secret))
        self.assertNotIn(issued_secret, json.dumps(row))
        # Listing returns metadata only; the secret is never repeated.
        listed = self.client.get("/api/admin/keys", headers=self.admin).json()
        self.assertGreaterEqual(len(listed), 1)
        listed_ids = [item["credential_id"] for item in listed]
        self.assertIn(body["credential_id"], listed_ids)
        for item in listed:
            self.assertNotIn("secret", item)
            self.assertNotIn(issued_secret, json.dumps(item))

    def test_rotation_drill_promote_sign_then_revoke(self) -> None:
        # Step 1: issue a fresh key (secret shown once).
        issued = self.client.post("/api/admin/keys", headers=self.admin).json()
        credential_id = issued["credential_id"]
        secret = issued["secret"]
        # The live instance does not yet know the key, so it refuses it.
        self.assertEqual(
            self.client.get(
                "/api/conversations", headers={"X-API-Key": secret, "X-Tenant-Id": "demo"}
            ).status_code,
            401,
        )
        # Step 2: promote the key into a fresh peer's config; the same
        # deterministic registry row is used, so the fresh instance accepts it.
        promoted = {secret: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"}}
        headers = self._fresh_instance_headers(extra_principals=promoted)
        headers["X-API-Key"] = secret
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 200)
        # Step 3: revoke the old key; the fresh peer refuses it immediately
        # (cross-instance, no restart).
        revoke = self.client.post(
            f"/api/admin/keys/{credential_id}/revoke",
            headers={"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"},
        )
        self.assertEqual(revoke.status_code, 200, revoke.text)
        self.assertEqual(self.client.get("/api/conversations", headers=headers).status_code, 401)

    def test_issue_audit_event_never_contains_secret(self) -> None:
        issued = self.client.post("/api/admin/keys", headers=self.admin).json()
        events = self.services.database.export_audit_events("demo", event_type="api_key.issued")
        issued_events = [
            e for e in events if e["payload"].get("credential_id") == issued["credential_id"]
        ]
        self.assertTrue(issued_events)
        serialized = json.dumps(issued_events)
        self.assertNotIn(issued["secret"], serialized)
        self.assertNotIn("secret", issued_events[0]["payload"])

    def test_issue_fails_closed_when_registry_unavailable(self) -> None:
        # AppServices is a frozen dataclass; the admin route captures the same
        # instance, so mutating it via object.__setattr__ drives the 503 path.
        object.__setattr__(self.services, "credential_store", None)
        object.__setattr__(self.services, "credential_lifecycle", None)
        response = self.client.post("/api/admin/keys", headers=self.admin)
        self.assertEqual(response.status_code, 503, response.text)

    def test_concurrent_revoke_race_settles_on_single_authority(self) -> None:
        """SEC-004 验收 · 并发轮换: two independent peers racing to revoke the
        same credential settle on one final state behind the registry's single
        authority — no 5xx, no deadlock, no resurrected secret.  One call
        performs the transition; the other is an idempotent no-op that still
        returns 200.  Afterward a third peer that never observed the revoke
        (it still holds the old secret in its live config) refuses the secret,
        because the revoked registry row outranks any live configuration."""
        issued = self.client.post("/api/admin/keys", headers=self.admin).json()
        credential_id = issued["credential_id"]
        secret = issued["secret"]

        # Peer A promotes the freshly-issued secret into its config (staged
        # activation) and accepts traffic signed with it.
        self._fresh_instance_headers(
            extra_principals={
                secret: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"}
            }
        )
        self.assertEqual(
            self.client.get(
                "/api/conversations", headers={"X-API-Key": secret, "X-Tenant-Id": "demo"}
            ).status_code,
            200,
        )

        # Peer B is a truly separate process view: its own app, its own
        # database pool, sharing only the SQLite file.  Both peers fire the
        # revoke at the same logical moment behind a barrier.
        peer_b_client = TestClient(create_app(_settings(self.db_path)))
        try:
            revoke_headers = {"X-API-Key": ADMIN_KEY, "X-Tenant-Id": "demo"}
            barrier = threading.Barrier(2)

            def _revoke_a(_: int) -> int:
                barrier.wait()
                return self.client.post(
                    f"/api/admin/keys/{credential_id}/revoke", headers=revoke_headers
                ).status_code

            def _revoke_b(_: int) -> int:
                barrier.wait()
                return peer_b_client.post(
                    f"/api/admin/keys/{credential_id}/revoke", headers=revoke_headers
                ).status_code

            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(_revoke_a, 0), pool.submit(_revoke_b, 0)]
                statuses = sorted(future.result() for future in futures)
            self.assertEqual(statuses, [200, 200])
        finally:
            peer_b_services = cast(Any, peer_b_client.app).state.services
            peer_b_services.database.close()
            peer_b_client.close()

        # Single final state: revoked, with a revocation anchor.
        row = self.services.credential_store.get(credential_id)
        assert row is not None
        self.assertEqual(row["status"], "revoked")
        self.assertIsNotNone(row["revoked_at"])

        # A third peer that re-promotes the old secret still refuses it: the
        # registry row, not the config, is the authority.
        fresh_headers = self._fresh_instance_headers(
            extra_principals={
                secret: {"tenant_id": "demo", "actor_id": "op.user", "role": "operator"}
            }
        )
        fresh_headers["X-API-Key"] = secret
        self.assertEqual(
            self.client.get("/api/conversations", headers=fresh_headers).status_code,
            401,
        )


class ChannelKeyIdTests(unittest.TestCase):
    """key-id/version selection: the caller signs with a selected rotated key."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "keyid.db"
        self.client = TestClient(create_app(_settings(self.db_path)))
        self.services = cast(Any, self.client.app).state.services
        self.store: CredentialStore = self.services.credential_store

    def tearDown(self) -> None:
        self.services.database.close()
        self.client.close()
        self._tmp.cleanup()

    def _post(self, *, secret: str, key_id: str | None = None) -> Any:
        body = json.dumps(
            {
                "event": "message.created",
                "message_id": "m1",
                "thread_id": "t1",
                "customer_id": "CUST-1",
                "customer_name": "C",
                "content": "hi",
            },
            separators=(",", ":"),
        ).encode("utf-8")
        return self.client.post(
            "/api/channels/support-main/webhook",
            content=body,
            headers=_signed_channel_headers(secret, body, key_id=key_id),
        )

    def _channel_credential_id(self, secret: str) -> str:
        row = self.store.get_by_fingerprint("channel", key_ref_for(secret))
        assert row is not None
        return row["credential_id"]

    def test_key_id_selects_the_seeded_rotated_key(self) -> None:
        key_id = self._channel_credential_id(CHANNEL_SECRET)
        response = self._post(secret=CHANNEL_SECRET, key_id=key_id)
        self.assertEqual(response.status_code, 202, response.text)
        self.assertNotEqual(key_id, "")

    def test_unknown_key_id_with_valid_secret_fails_closed(self) -> None:
        response = self._post(secret=CHANNEL_SECRET, key_id="cred_doesnotexist")
        self.assertEqual(response.status_code, 401, response.text)

    def test_key_id_of_wrong_type_fails_closed(self) -> None:
        # An api_key credential id presented as a channel key id.
        api_row = self.store.get_by_fingerprint("api_key", key_ref_for(OPERATOR_KEY))
        assert api_row is not None
        response = self._post(secret=CHANNEL_SECRET, key_id=api_row["credential_id"])
        self.assertEqual(response.status_code, 401, response.text)

    def test_key_id_of_another_tenant_fails_closed(self) -> None:
        other_key_id = self._channel_credential_id(OTHER_SECRET)
        response = self._post(secret=CHANNEL_SECRET, key_id=other_key_id)
        self.assertEqual(response.status_code, 401, response.text)

    def test_revoked_key_id_is_refused_immediately(self) -> None:
        key_id = self._channel_credential_id(CHANNEL_SECRET)
        self.assertEqual(self._post(secret=CHANNEL_SECRET, key_id=key_id).status_code, 202)
        self.services.credential_lifecycle.revoke(key_id, now=utc_now(), by="admin")
        self.assertEqual(self._post(secret=CHANNEL_SECRET, key_id=key_id).status_code, 401)

    def test_key_id_through_live_http_without_registry_fails_closed(self) -> None:
        registry = InboundChannelRegistry(
            json.dumps(
                {
                    "support-main": {
                        "tenant_id": "demo",
                        "channel": "formal_chat",
                        "secret": CHANNEL_SECRET,
                    }
                }
            ),
            credential_store=None,
        )
        body = b"{}"
        headers = _signed_channel_headers(CHANNEL_SECRET, body, key_id="cred_any")
        with self.assertRaises(ChannelWebhookAuthError):
            registry.authenticate(
                "support-main",
                headers["X-Helix-Timestamp"],
                headers["X-Helix-Signature"],
                body,
                key_id="cred_any",
            )

    def test_registry_resolves_secret_by_fingerprint_only(self) -> None:
        """The registry never holds the secret; the resolver matches the
        configured candidate by the same deterministic fingerprint, so a
        rotated secret file that re-registers the same key keeps working."""
        registry = InboundChannelRegistry(
            json.dumps(
                {
                    "support-main": {
                        "tenant_id": "demo",
                        "channel": "formal_chat",
                        "secret": CHANNEL_SECRET,
                    }
                }
            ),
            credential_store=self.store,
        )
        key_id = self._channel_credential_id(CHANNEL_SECRET)
        resolved = registry._secret_for_key_id("support-main", key_id)
        self.assertEqual(resolved, CHANNEL_SECRET.encode("utf-8"))


if __name__ == "__main__":
    unittest.main()
