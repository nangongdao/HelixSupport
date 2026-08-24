"""Unified credential lifecycle registry (Phase 41 / ROADMAP 41.1 SEC-004).

Every secret-bearing credential — API key, formal-channel secret, widget
signing key, session key — is registered as a logical credential with
``id/type/tenant/status/not_before/expires_at/last_used_at/version``.  The
registry is the single arbiter of lifecycle state, and it stores only a
fingerprint of the secret (``key_ref``), never the secret itself; secrets
continue to live in the secret manager / configuration.

State machine (per resource, every transition is validated):

    pending --activate--> active <--activate-- pending(early)
    active --retire--> retiring
    retiring --revoke--> revoked
    any                --(expires_at passes)--> expired
    pending/active --> revoked (emergency revocation)

``retiring`` is still accepted during the bounded overlap window so an old
key can phase out gracefully while its replacement comes up.  ``revoked``
and ``expired`` are rejected immediately.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
import string
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)

#: Accepted while a retiring credential drains its overlap window.
MIN_ROTATION_OVERLAP_SECONDS = 60
DEFAULT_ROTATION_OVERLAP_SECONDS = 86400  # 24h bounded window
MAX_CLOCK_SKEW_SECONDS = 5  # matches widget-token / OIDC skew semantics

#: Empirically-strong runtime-issued API keys: 256 bits of entropy with a
#: readable, dash-separated 160-bit ``hk-`` prefix the caller can scan for.
#: Contains no characters the URL path alphabet rejects.
ROTATION_SECRET_ENTROPY_BITS = 256
KEY_ALPHABET = string.ascii_letters + string.digits
KEY_PREFIX = "hk-"
KEY_SEGMENT = 20  # 120 bits per dash-group
KEY_GROUPS = 4

#: Verifier-setting for a config-managed verify-only secret.
CONFIG_VERIFY_SECRET = "*configured*"

#: Deterministic API-key identifier: matches the historical "credential_id"
#: used by the client, the admin revoke route and the legacy ``revoked_api_keys``
#: table, so a registered API key is addressable everywhere without any caller
#: changing how it references an identity.
API_KEY_CREDENTIAL_ID_PREFIX_LENGTH = 12


class CredentialStatus(StrEnum):
    """Canonical lifecycle states; ordering reflects progression."""

    PENDING = "pending"
    ACTIVE = "active"
    RETIRING = "retiring"
    REVOKED = "revoked"
    EXPIRED = "expired"


#: Legal transitions; any action outside these sets is rejected.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    CredentialStatus.PENDING: frozenset(
        {CredentialStatus.ACTIVE, CredentialStatus.REVOKED, CredentialStatus.EXPIRED}
    ),
    CredentialStatus.ACTIVE: frozenset(
        {CredentialStatus.RETIRING, CredentialStatus.REVOKED, CredentialStatus.EXPIRED}
    ),
    CredentialStatus.RETIRING: frozenset({CredentialStatus.REVOKED, CredentialStatus.EXPIRED}),
    CredentialStatus.REVOKED: frozenset(),
    CredentialStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class Credential:
    credential_id: str
    type: str
    tenant_id: str
    status: str
    key_ref: str
    version: int = 1
    not_before: str = ""
    expires_at: str | None = None
    last_used_at: str | None = None
    retired_at: str | None = None
    revoked_at: str | None = None
    revoked_by: str | None = None
    rotation_of: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def is_allowed(self, now: int, *, max_skew_seconds: int = MAX_CLOCK_SKEW_SECONDS) -> bool:
        """Whether a request authenticated with this credential is accepted.

        ``retiring`` credentials remain accepted inside their overlap window so
        rotation is seamless; ``revoked``/``expired`` fail closed.
        """
        status = self.status.value if isinstance(self.status, CredentialStatus) else self.status
        if status in {CredentialStatus.REVOKED.value, CredentialStatus.EXPIRED.value}:
            return False
        if status == CredentialStatus.PENDING.value:
            return False
        if self.not_before and now + max_skew_seconds < _parse(self.not_before):
            return False
        if self.expires_at and now > _parse(self.expires_at) + max_skew_seconds:
            return False
        if status == CredentialStatus.RETIRING.value:
            if not self.retired_at:
                return False
            if now > _parse(self.retired_at) + DEFAULT_ROTATION_OVERLAP_SECONDS + max_skew_seconds:
                return False
        return True


def _parse(iso_value: str) -> int:
    """Parse an ISO-8601 UTC timestamp to epoch seconds (``utc_now`` format)."""
    try:
        from datetime import datetime

        return int(datetime.fromisoformat(iso_value).timestamp())
    except ValueError:
        return 0


def key_ref_for(secret: str) -> str:
    """Deterministic fingerprint of a secret; the registry never stores more."""
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def issue_credential_id() -> str:
    return f"cred_{secrets.token_hex(10)}"


def issue_api_key() -> str:
    """Generate a fresh runtime API key with a scannable, bounded format."""
    chars = [secrets.choice(KEY_ALPHABET) for _ in range(KEY_GROUPS * KEY_SEGMENT)]
    grouped = "-".join(
        "".join(chars[index : index + KEY_SEGMENT]) for index in range(0, len(chars), KEY_SEGMENT)
    )
    return f"{KEY_PREFIX}{grouped}"


class CredentialStore:
    """Persistence for the credential registry (uses the app database)."""

    def __init__(self, database: Any) -> None:
        self._database = database

    def register(
        self,
        *,
        credential_id: str,
        type: str,
        tenant_id: str,
        key_ref: str,
        not_before: str,
        expires_at: str | None = None,
        version: int = 1,
        rotation_of: str | None = None,
        created_at: str,
        status: str = CredentialStatus.ACTIVE.value,
    ) -> None:
        """Insert a credential. Presence of the same ``(type, key_ref)`` is
        tolerated (idempotent reload of the same configured secret)."""
        if status not in {s.value for s in CredentialStatus}:
            raise InvalidCredentialTransition(f"Unknown initial status {status!r}")
        with self._database.connect() as connection:
            connection.execute(
                """INSERT INTO credential_registry (
                    credential_id, type, tenant_id, status, key_ref, version,
                    not_before, expires_at, created_at, updated_at, rotation_of
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(type, key_ref) DO NOTHING""",
                (
                    credential_id,
                    type,
                    tenant_id,
                    status,
                    key_ref,
                    version,
                    not_before,
                    expires_at,
                    created_at,
                    created_at,
                    rotation_of,
                ),
            )

    def transition(self, credential_id: str, action: str, *, now: str, by: str) -> str:
        """Apply a lifecycle action transactionally; return the new status.

        Applying an action that already matches the current status is a no-op
        (revoke on an already-revoked credential is idempotent, mirroring the
        Phase 28.2 ``ON CONFLICT DO NOTHING`` semantics).

        Phase 41.4 (SEC-404): the UPDATE is conditional on the row still being
        in the state we read (compare-and-set), so a concurrent revoke can no
        longer be overwritten by a stale retire/expire discovered earlier.
        A rowcount of 0 re-reads and re-validates (bounded, fail-closed).
        """
        if action not in {"activate", "retire", "revoke", "expire"}:
            raise InvalidCredentialTransition(f"Unknown credential action {action!r}")
        target = {
            "activate": "active",
            "retire": "retiring",
            "revoke": "revoked",
            "expire": "expired",
        }[action]
        for _attempt in range(3):
            with self._database.connect() as connection:
                row = connection.execute(
                    "SELECT * FROM credential_registry WHERE credential_id = ?",
                    (credential_id,),
                ).fetchone()
                if row is None:
                    raise LookupError(f"Credential {credential_id} not found")
                current = dict(row)
                if current["status"] == target:
                    return target
                if target not in ALLOWED_TRANSITIONS[current["status"]]:
                    raise InvalidCredentialTransition(
                        f"Credential {credential_id} cannot move {current['status']} -> {target}"
                    )
                updates: dict[str, Any] = {"updated_at": now}
                if action == "revoke":
                    updates.update(revoked_at=now, revoked_by=by)
                elif action == "retire":
                    updates.update(retired_at=now)
                updated = connection.execute(
                    """UPDATE credential_registry SET status = ?, updated_at = ?,
                       retired_at = COALESCE(?, retired_at),
                       revoked_at = COALESCE(?, revoked_at),
                       revoked_by = COALESCE(?, revoked_by)
                    WHERE credential_id = ? AND status = ?""",
                    (
                        target,
                        now,
                        updates.get("retired_at"),
                        updates.get("revoked_at"),
                        updates.get("revoked_by"),
                        credential_id,
                        current["status"],
                    ),
                ).rowcount
            if updated:
                return target
            # A concurrent transition won the race; re-read and re-validate.
            continue
        logger.warning(
            "credential.transition_conflict credential=%s action=%s target=%s",
            credential_id,
            action,
            target,
        )
        raise InvalidCredentialTransition(
            f"Credential {credential_id} changed concurrently while transitioning to {target}"
        )

    def set_last_used(self, credential_id: str, now: str) -> None:
        with self._database.connect() as connection:
            connection.execute(
                "UPDATE credential_registry SET last_used_at = ? WHERE credential_id = ?",
                (now, credential_id),
            )

    def get(self, credential_id: str) -> dict[str, Any] | None:
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM credential_registry WHERE credential_id = ?",
                (credential_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_by_fingerprint(self, type: str, key_ref: str) -> dict[str, Any] | None:
        """Resolve a credential by ``(type, fingerprint)``.

        Used by key-id/version-aware authentication: a caller presents a
        ``key_id`` (the registry row) and the server looks up the fingerprint
        that key id maps to.  The unique ``(type, key_ref)`` index guarantees
        at most one row can match a fingerprint.
        """
        with self._database.connect() as connection:
            row = connection.execute(
                "SELECT * FROM credential_registry WHERE type = ? AND key_ref = ?",
                (type, key_ref),
            ).fetchone()
        return dict(row) if row else None

    def list_for_tenant(self, tenant_id: str, type: str | None = None) -> list[dict[str, Any]]:
        with self._database.connect() as connection:
            if type:
                rows = connection.execute(
                    "SELECT * FROM credential_registry WHERE tenant_id = ? AND type = ? "
                    "ORDER BY created_at",
                    (tenant_id, type),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM credential_registry WHERE tenant_id = ? ORDER BY created_at",
                    (tenant_id,),
                ).fetchall()
        return [dict(row) for row in rows]


class CredentialLifecycle:
    """High-level lifecycle decisions on top of the store."""

    def __init__(self, store: CredentialStore) -> None:
        self.store = store

    def rotate(self, credential_id: str, *, now: str, by: str) -> None:
        """Begin rotation: retire the old credential in its overlap window."""
        self.store.transition(credential_id, "retire", now=now, by=by)

    def revoke(self, credential_id: str, *, now: str, by: str) -> None:
        self.store.transition(credential_id, "revoke", now=now, by=by)

    def activate(self, credential_id: str, *, now: str, by: str) -> None:
        self.store.transition(credential_id, "activate", now=now, by=by)


class InvalidCredentialTransition(Exception):
    """Raised when a lifecycle action violates the state machine."""


def register_configured_from_json(
    *,
    store: CredentialStore,
    type: str,
    tenant_id: str,
    raw_json: str,
    created_at: str,
    not_before: str = "",
) -> int:
    """Idempotently register configured secrets into the registry.

    ``api_key`` secrets are the *keys* of the principal map (``{key: {tenant_id,
    actor_id, role}}``) and keep the deterministic short credential id
    (``sha256(key)[:12]``) so existing clients, the admin revoke route, and the
    legacy ``revoked_api_keys`` table all continue to address the same entity.
    Channel-style configs are ``{alias: {secret, ...}}``; those get fresh
    ``cred_...`` identifiers.

    Only metadata is stored — the registry fingerprint stays deterministic so a
    restart after a rotated secret file re-registers the new key as a new
    credential without ever reading the old secret back.

    Returns how many unique secrets were (re)registered.
    """
    import json

    try:
        payload: Any = json.loads(raw_json or "{}")
    except json.JSONDecodeError:
        logger.warning("credential registration skipped: invalid JSON for %s", type)
        return 0
    if not isinstance(payload, dict):
        logger.warning("credential registration skipped: not an object for %s", type)
        return 0
    count = 0
    if type == "api_key":
        for secret, value in payload.items():
            if not isinstance(secret, str) or not secret:
                continue
            effective_tenant = (
                str(value["tenant_id"])
                if isinstance(value, dict) and value.get("tenant_id")
                else tenant_id
            )
            store.register(
                credential_id=key_ref_for(secret)[:API_KEY_CREDENTIAL_ID_PREFIX_LENGTH],
                type=type,
                tenant_id=effective_tenant,
                key_ref=key_ref_for(secret),
                not_before=not_before,
                expires_at=None,
                version=1,
                created_at=created_at,
            )
            count += 1
        return count
    for value in payload.values():
        secret = value.get("secret") if isinstance(value, dict) else None
        if not isinstance(secret, str) or not secret:
            continue
        effective_tenant = (
            str(value["tenant_id"])
            if isinstance(value, dict) and value.get("tenant_id")
            else tenant_id
        )
        store.register(
            credential_id=issue_credential_id(),
            type=type,
            tenant_id=effective_tenant,
            key_ref=key_ref_for(secret),
            not_before=not_before,
            expires_at=None,
            version=1,
            created_at=created_at,
        )
        count += 1
    return count


class VaultUnavailable(Exception):
    """Secret-manager failure for a fail-closed lifecycle path."""
