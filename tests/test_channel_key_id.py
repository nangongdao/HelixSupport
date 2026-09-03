"""InboundChannelRegistry key-id selection (Phase 41.1) — branch coverage.

The key_id credential-store path of channel_webhooks.py selects a rotated
signing secret by registry credential. Every failure mode (unknown id,
wrong type/tenant, inactive credential, fingerprint mismatch, store error)
must resolve to the same uniform None so callers cannot probe key ids.
"""

from __future__ import annotations

import json
import unittest

from app.channel_webhooks import InboundChannelRegistry

SECRET = "channel-secret-for-key-id-tests-long-enough"
OTHER_SECRET = "another-channel-secret-for-key-id-tests"


def _registry(
    raw: str | None = None, replay_window: int = 300, credential_store: object | None = None
) -> InboundChannelRegistry:
    raw = raw or json.dumps(
        {
            "support-main": {
                "tenant_id": "demo",
                "channel": "formal_chat",
                "secret": SECRET,
            },
            "support-other": {
                "tenant_id": "other-tenant",
                "channel": "formal_chat",
                "secret": OTHER_SECRET,
            },
        }
    )
    return InboundChannelRegistry(raw, replay_window, credential_store=credential_store)


class _FakeStore:
    """Minimal credential-store stand-in for the key-id branch."""

    def __init__(self, row: dict | None = None) -> None:
        self._row = row

    def get(self, key_id: str) -> dict | None:
        return self._row


class KeyIdSelectionTests(unittest.TestCase):
    def test_no_key_id_returns_none(self) -> None:
        registry = _registry()
        self.assertIsNone(registry._secret_for_key_id("support-main", None))

    def test_unknown_account_returns_none(self) -> None:
        registry = _registry()
        self.assertIsNone(registry._secret_for_key_id("no-such-account", "cred_1"))

    def test_no_credential_store_returns_none(self) -> None:
        registry = _registry()
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_store_error_returns_none(self) -> None:
        class _BoomStore:
            def get(self, key_id: str) -> dict:
                raise RuntimeError("store down")

        registry = _registry(credential_store=_BoomStore())
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_missing_row_returns_none(self) -> None:
        registry = _registry(credential_store=_FakeStore(None))
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_wrong_type_returns_none(self) -> None:
        store = _FakeStore({"type": "api_key", "tenant_id": "demo"})
        registry = _registry(credential_store=store)
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_wrong_tenant_returns_none(self) -> None:
        store = _FakeStore({"type": "channel", "tenant_id": "other-tenant"})
        registry = _registry(credential_store=store)
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_inactive_credential_returns_none(self) -> None:
        store = _FakeStore(
            {
                "credential_id": "cred_1",
                "type": "channel",
                "tenant_id": "demo",
                "status": "revoked",
                "key_ref": "ref-x",
                "not_before": "",
                "expires_at": "",
            }
        )
        registry = _registry(credential_store=store)
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_no_key_ref_returns_none(self) -> None:
        store = _FakeStore(
            {
                "credential_id": "cred_1",
                "type": "channel",
                "tenant_id": "demo",
                "status": "active",
                "key_ref": "",
                "not_before": "",
                "expires_at": "",
            }
        )
        registry = _registry(credential_store=store)
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_fingerprint_no_match_returns_none(self) -> None:
        from app.credentials import key_ref_for

        store = _FakeStore(
            {
                "credential_id": "cred_1",
                "type": "channel",
                "tenant_id": "demo",
                "status": "active",
                "key_ref": key_ref_for("completely-different-secret-000"),
                "not_before": "",
                "expires_at": "",
            }
        )
        registry = _registry(credential_store=store)
        self.assertIsNone(registry._secret_for_key_id("support-main", "cred_1"))

    def test_matching_fingerprint_returns_secret(self) -> None:
        from app.credentials import key_ref_for

        store = _FakeStore(
            {
                "credential_id": "cred_1",
                "type": "channel",
                "tenant_id": "demo",
                "status": "active",
                "key_ref": key_ref_for(SECRET),
                "not_before": "",
                "expires_at": "",
            }
        )
        registry = _registry(credential_store=store)
        secret = registry._secret_for_key_id("support-main", "cred_1")
        self.assertEqual(secret, SECRET.encode("utf-8"))

    def test_configured_tenants_and_count(self) -> None:
        registry = _registry()
        self.assertEqual(registry.configured_tenants, {"demo", "other-tenant"})
        self.assertEqual(registry.account_count, 2)


class RegistryConfigValidationTests(unittest.TestCase):
    def test_replay_window_out_of_bounds_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(replay_window=10)

    def test_invalid_json_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry("{not json")

    def test_non_object_config_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry("[]")

    def test_bad_account_id_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(json.dumps({"bad id!": {"tenant_id": "demo", "channel": "x", "secret": SECRET}}))

    def test_non_object_account_value_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(json.dumps({"ok": ["not", "object"]}))

    def test_missing_tenant_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(json.dumps({"ok": {"channel": "x", "secret": SECRET}}))

    def test_invalid_channel_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(json.dumps({"ok": {"tenant_id": "demo", "channel": "bad channel!", "secret": SECRET}}))

    def test_short_secret_rejected(self) -> None:
        with self.assertRaises(Exception):
            _registry(json.dumps({"ok": {"tenant_id": "demo", "channel": "x", "secret": "short"}}))

    def test_credential_store_property_exposes_store(self) -> None:
        store = _FakeStore(None)
        registry = _registry(credential_store=store)
        self.assertIs(registry.credential_store, store)


if __name__ == "__main__":
    unittest.main()