"""Signed inbound webhook authentication for formal messaging channels.

The reference contract is provider-neutral: a configured account id binds one
tenant and channel, while HMAC authentication binds each request to that
account. Tenant identity is never accepted from the webhook body.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

ACCOUNT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
CHANNEL_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
SIGNATURE_PATTERN = re.compile(r"^sha256=([0-9a-fA-F]{64})$")


class ChannelWebhookConfigError(ValueError):
    """Raised when the server-side account mapping is malformed."""


class ChannelWebhookAuthError(ValueError):
    """Raised for every unknown, stale, or incorrectly signed request."""


@dataclass(frozen=True)
class InboundChannelAccount:
    account_id: str
    tenant_id: str
    channel: str
    secret: bytes = field(repr=False)


class InboundChannelRegistry:
    """Validated account registry and HMAC verifier."""

    def __init__(
        self,
        raw: str,
        replay_window_seconds: int = 300,
        credential_store: Any | None = None,
    ) -> None:
        if not 30 <= replay_window_seconds <= 3600:
            raise ChannelWebhookConfigError(
                "CHANNEL_WEBHOOK_REPLAY_WINDOW_SECONDS must be between 30 and 3600"
            )
        self.replay_window_seconds = replay_window_seconds
        self._accounts = self._parse_accounts(raw)
        self._credential_store = credential_store

    @staticmethod
    def _parse_accounts(raw: str) -> dict[str, InboundChannelAccount]:
        try:
            payload: Any = json.loads(raw or "{}")
        except json.JSONDecodeError as exc:
            raise ChannelWebhookConfigError("CHANNEL_WEBHOOKS_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ChannelWebhookConfigError(
                "CHANNEL_WEBHOOKS_JSON must be an object keyed by account id"
            )

        accounts: dict[str, InboundChannelAccount] = {}
        for account_id, value in payload.items():
            if not isinstance(account_id, str) or not ACCOUNT_ID_PATTERN.fullmatch(account_id):
                raise ChannelWebhookConfigError(
                    "Channel account ids must match [A-Za-z0-9._-] and be at most 64 characters"
                )
            if not isinstance(value, dict):
                raise ChannelWebhookConfigError(
                    f"Channel account {account_id!r} must map to an object"
                )
            tenant_id = value.get("tenant_id")
            channel = value.get("channel")
            secret = value.get("secret")
            if not isinstance(tenant_id, str) or not tenant_id.strip() or len(tenant_id) > 80:
                raise ChannelWebhookConfigError(
                    f"Channel account {account_id!r} requires a non-empty tenant_id"
                )
            if not isinstance(channel, str) or not CHANNEL_PATTERN.fullmatch(channel):
                raise ChannelWebhookConfigError(
                    f"Channel account {account_id!r} requires a valid channel"
                )
            if not isinstance(secret, str) or len(secret.encode("utf-8")) < 32:
                raise ChannelWebhookConfigError(
                    f"Channel account {account_id!r} secret must be at least 32 bytes"
                )
            accounts[account_id] = InboundChannelAccount(
                account_id=account_id,
                tenant_id=tenant_id.strip(),
                channel=channel,
                secret=secret.encode("utf-8"),
            )
        return accounts

    @property
    def configured_tenants(self) -> set[str]:
        return {account.tenant_id for account in self._accounts.values()}

    @property
    def account_count(self) -> int:
        return len(self._accounts)

    @property
    def credential_store(self) -> Any | None:
        """Persistent credential registry for key-id/version selection (41.1)."""
        return self._credential_store

    def _secret_for_key_id(self, account_id: str, key_id: str | None) -> bytes | None:
        """Resolve the signing secret for an optional ``key_id`` (Phase 41.1).

        A caller may present a ``key_id`` (the registry credential id,
        ``cred_...``) to select which rotated key signed the request.  The
        registry stores only fingerprints, so the configured secret candidates
        are matched by the same deterministic fingerprint the registry was
        seeded with.  Returns ``None`` for an unknown key id, a key id of the
        wrong type/tenant, an inactive (revoked/expired/pending) key, or a key
        whose fingerprint matches no configured secret — every one of those
        resolves to the uniform authentication failure.
        """
        account = self._accounts.get(account_id)
        if account is None:
            return None
        if key_id is None:
            return None
        if self._credential_store is None:
            return None
        try:
            row = self._credential_store.get(key_id)
        except Exception:
            return None
        if row is None or row.get("type") != "channel":
            return None
        if row.get("tenant_id") != account.tenant_id:
            return None
        from app.credentials import Credential

        if not Credential(**row).is_allowed(int(time.time())):
            return None
        fingerprint = row.get("key_ref")
        if not fingerprint:
            return None
        from app.credentials import key_ref_for

        for secret in {candidate.secret for candidate in self._accounts.values()}:
            if key_ref_for(secret.decode("utf-8", errors="ignore")) == fingerprint:
                return secret
        return None

    def authenticate(
        self,
        account_id: str,
        timestamp: str | None,
        signature: str | None,
        body: bytes,
        *,
        key_id: str | None = None,
        now: float | None = None,
    ) -> InboundChannelAccount:
        """Authenticate one raw request and return its server-bound account.

        ``key_id`` (Phase 41.1) selects a rotated registry credential; unknown
        or inactive key ids resolve to the same uniform failure as a bad
        signature, so callers cannot probe which key ids exist.
        """
        account = self._accounts.get(account_id)
        match = SIGNATURE_PATTERN.fullmatch(signature or "")
        try:
            signed_at = int(timestamp or "")
        except ValueError:
            signed_at = 0
        current = int(time.time() if now is None else now)
        fresh = abs(current - signed_at) <= self.replay_window_seconds

        # Calculate a dummy digest for unknown accounts / key ids as well,
        # keeping the externally visible failure path uniform and avoiding
        # account probes. A key id that resolves to nothing falls back to the
        # dummy secret, so an unknown key never authenticates.
        if account is not None and key_id is not None:
            secret = (
                self._secret_for_key_id(account_id, key_id) or b"unknown-channel-account-secret"
            )
        elif account is not None:
            secret = account.secret
        else:
            secret = b"unknown-channel-account-secret"
        signed_payload = (timestamp or "").encode("ascii", errors="ignore") + b"." + body
        expected = hmac.new(secret, signed_payload, hashlib.sha256).hexdigest()
        supplied = match.group(1).lower() if match else "0" * 64
        signature_valid = hmac.compare_digest(expected, supplied)
        if account is None or not fresh or not signature_valid:
            raise ChannelWebhookAuthError("Invalid webhook authentication")
        return account


def channel_message_key(account_id: str, message_id: str) -> str:
    """Return a bounded, character-safe turn idempotency key."""
    digest = hashlib.sha256(f"{account_id}\0{message_id}".encode()).hexdigest()
    return f"channel_{digest[:48]}"


def content_sha256(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
