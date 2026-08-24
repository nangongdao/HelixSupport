"""Encrypted, expiring data-subject export objects (M0 SEC-002).

The DSR export flow never returns raw PII inline and never writes plaintext
export data to disk:

- the export blob is encrypted at rest with a Fernet key derived from
  ``DSR_EXPORT_SECRET`` (AES-128-CBC + HMAC-SHA256, RFC 4728-independent
  construction via the ``cryptography`` library);
- a download token is single-use, has a 15-minute TTL, and is persisted only
  as a SHA-256 digest;
- the encrypted object expires 24 hours after creation and is pruned by the
  turn-worker housekeeping sweep (and lazily on any download attempt);
- audit records carry only the request id and per-type counts — never the
  customer reference or message content.

``cryptography`` is imported lazily and the module fails closed: if the
library or ``DSR_EXPORT_SECRET`` is missing, exports cannot be created and
the API returns a stable error instead of silently weakening encryption.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import secrets
from typing import Any

from app.database import Database, utc_now

logger = logging.getLogger(__name__)

# One-time download tokens expire after this long (ROADMAP 2.x: <= 15 min).
EXPORT_TOKEN_TTL_SECONDS = 15 * 60
# Encrypted export objects are deleted at the latest this long after creation
# (ROADMAP 2.x: <= 24 h).
EXPORT_OBJECT_TTL_SECONDS = 24 * 60 * 60


class DsrExportError(Exception):
    """A DSR export failure mapped to a safe public response."""

    def __init__(self, public_message: str, *, status_code: int = 400) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


def _derived_fernet_key(secret: str) -> bytes:
    """Derive a Fernet key from ``DSR_EXPORT_SECRET`` (SHA-256 -> base64)."""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def _fernet(secret: str | None = None) -> Any:
    """Lazily build the Fernet cipher; fail closed when unavailable."""
    key_source = secret if secret is not None else os.getenv("DSR_EXPORT_SECRET", "")
    if not key_source:
        raise DsrExportError(
            "Export encryption is not configured (DSR_EXPORT_SECRET is unset)",
            status_code=501,
        )
    try:
        from cryptography.fernet import Fernet
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DsrExportError(
            "Export encryption is unavailable on this deployment",
            status_code=501,
        ) from exc
    return Fernet(_derived_fernet_key(key_source))


def encrypt_export(plaintext: bytes, secret: str | None = None) -> str:
    """Encrypt an export payload; returns base64 of the Fernet token."""
    return _fernet(secret).encrypt(plaintext).decode("ascii")


def decrypt_export(encrypted: str, secret: str | None = None) -> bytes:
    try:
        return _fernet(secret).decrypt(encrypted.encode("ascii"))
    except Exception as exc:
        logger.warning("DSR export decrypt failure: %s", exc)
        raise DsrExportError(
            "Export object could not be decrypted",
            status_code=500,
        ) from exc


def new_download_token() -> tuple[str, str]:
    """Return ``(raw_token, sha256_digest)``; only the digest is persisted."""
    raw = secrets.token_urlsafe(32)
    return raw, hashlib.sha256(raw.encode("ascii")).hexdigest()


class DsrExportStore:
    """Persist and prune encrypted DSR export objects (tenant-scoped)."""

    def __init__(self, database: Database, secret: str | None = None) -> None:
        self.database = database
        self._secret = secret

    def _encrypt(self, plaintext: bytes) -> str:
        return encrypt_export(plaintext, self._secret)

    def _decrypt(self, encrypted: str) -> bytes:
        return decrypt_export(encrypted, self._secret)

    def store(
        self,
        tenant_id: str,
        request_id: str,
        plaintext: bytes,
        *,
        now_epoch: int,
    ) -> dict[str, Any]:
        """Encrypt and persist an export object; returns the stored row."""
        encrypted = self._encrypt(plaintext)
        object_id = f"dsrex_{secrets.token_hex(16)}"
        raw_token, token_hash = new_download_token()
        created_at = utc_now()
        expires_at = _iso_offset(now_epoch + EXPORT_OBJECT_TTL_SECONDS)
        token_expires_at = _iso_offset(now_epoch + EXPORT_TOKEN_TTL_SECONDS)
        with self.database.connect() as conn:
            conn.execute(
                """INSERT INTO dsr_export_objects
                   (id, tenant_id, request_id, encrypted_blob, content_sha256,
                    created_at, expires_at, download_token_hash,
                    download_token_expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    object_id,
                    tenant_id,
                    request_id,
                    encrypted,
                    hashlib.sha256(plaintext).hexdigest(),
                    created_at,
                    expires_at,
                    token_hash,
                    token_expires_at,
                ),
            )
        return {
            "object_id": object_id,
            "raw_token": raw_token,
            "token_expires_at": token_expires_at,
            "expires_at": expires_at,
            "content_sha256": hashlib.sha256(plaintext).hexdigest(),
            "byte_count": len(plaintext),
        }

    def consume(
        self,
        tenant_id: str,
        object_id: str,
        raw_token: str,
        *,
        now_epoch: int,
    ) -> dict[str, Any] | None:
        """One-time download: verify the token digest, mark consumed, decrypt.

        Returns the export row with ``plaintext`` bytes, or ``None`` when the
        object is unknown, expired, already downloaded, or the token is wrong
        or expired — all indistinguishable to the caller.
        """
        token_hash = hashlib.sha256(raw_token.encode("ascii")).hexdigest()
        with self.database.connect() as conn:
            row = conn.execute(
                """SELECT id, tenant_id, request_id, encrypted_blob,
                          content_sha256, expires_at, download_token_hash,
                          download_token_expires_at, downloaded_at
                   FROM dsr_export_objects
                   WHERE id = ? AND tenant_id = ?""",
                (object_id, tenant_id),
            ).fetchone()
            if row is None:
                return None
            now_iso = _iso_now()
            if _epoch_of(row["expires_at"]) <= now_epoch:
                return None
            if (
                row["download_token_hash"] != token_hash
                or row["downloaded_at"] is not None
                or _epoch_of(row["download_token_expires_at"]) <= now_epoch
            ):
                return None
            conn.execute(
                "UPDATE dsr_export_objects SET downloaded_at = ? WHERE id = ?",
                (now_iso, object_id),
            )
        payload = dict(row)
        try:
            payload["plaintext"] = self._decrypt(str(payload["encrypted_blob"]))
        except DsrExportError:
            return None
        return payload

    def prune_expired(self, *, now_epoch: int) -> int:
        """Delete objects past their 24-hour expiry; returns count deleted."""
        with self.database.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM dsr_export_objects WHERE expires_at < ?",
                (_iso_from_epoch(now_epoch),),
            )
            return cursor.rowcount


def _iso_now() -> str:
    return utc_now()


def _iso_from_epoch(epoch: int) -> str:
    from datetime import UTC, datetime

    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="milliseconds")


def _iso_offset(epoch: int) -> str:
    return _iso_from_epoch(epoch)


def _epoch_of(value: str) -> int:
    from datetime import datetime

    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return 0
