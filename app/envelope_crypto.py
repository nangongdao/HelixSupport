"""Per-tenant envelope encryption for restricted fields (ROADMAP 43.2).

restricted-classified values are encrypted with a per-tenant data encryption
key (DEK); each DEK is itself encrypted (wrapped) under a key-encryption key
(KEK) held by a KMS.  The design mirrors the swappable-protocol pattern of
``app.anchor_service``:

- ``KeyManagementServiceProtocol`` is the KMS boundary (a cloud KMS in
  production); ``DiskKeyManagementService`` is the development stand-in — a
  directory of versioned KEK files with rotate/revoke transitions.
- ``DekKeystoreProtocol`` persists *wrapped* DEKs per tenant together with
  their key versions; ``DatabaseDekKeystore`` stores them in the
  ``tenant_deks`` table (migration 38). Key material never lives in
  configuration.

Every envelope carries the key versions it was written with (``kek_version``
and ``dek_version`` travel with the ciphertext, plus a copy of the wrapped
DEK), so rotation stays online: old envelopes decrypt through the historical
DEK while new writes use the current one, and :meth:`rewrap_tenant_deks`
re-wraps stored DEKs under a new KEK version without touching any ciphertext.
Revoking a KEK version fails closed for anything not yet re-wrapped.

The ``cryptography`` library is imported lazily; when it or the KMS is
unavailable the module fails closed (never a silent fallback to plaintext),
matching the DSR export-encryption posture in ``app.dsr``.
"""

from __future__ import annotations

import base64
import json
import logging
import secrets
import sqlite3
from pathlib import Path
from typing import Any, Protocol

from app.db._util import utc_now

logger = logging.getLogger(__name__)

ENVELOPE_VERSION = 1
DEK_SIZE_BYTES = 32  # AES-256-GCM data keys
KEK_SIZE_BYTES = 32

#: DEK lifecycle; ``rotated`` rows stay decryptable, ``revoked`` fail closed.
DEK_STATUSES = ("active", "rotated", "revoked")
_ALLOWED_DEK_TRANSITIONS = {
    "active": {"rotated", "revoked"},
    "rotated": {"revoked"},
    "revoked": set(),
}


class EnvelopeCryptoError(Exception):
    """An envelope-encryption failure mapped to a safe public response."""

    def __init__(self, public_message: str, *, status_code: int = 500) -> None:
        super().__init__(public_message)
        self.public_message = public_message
        self.status_code = status_code


class KmsUnavailableError(EnvelopeCryptoError):
    """The KMS is unreachable or refuses the operation right now."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Key management service unavailable: {detail}", status_code=503)


class EnvelopeTamperError(EnvelopeCryptoError):
    """Ciphertext, identity binding, or key material failed authentication."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Envelope failed authentication: {detail}", status_code=500)


def _aesgcm(key: bytes) -> Any:
    """Lazily build an AESGCM cipher bound to ``key``; fail closed when missing."""
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:  # pragma: no cover - exercised via patching
        raise EnvelopeCryptoError(
            "Envelope encryption is unavailable on this deployment",
            status_code=501,
        ) from exc
    return AESGCM(key)


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii")


def _b64decode(encoded: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(encoded.encode("ascii"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise EnvelopeTamperError("envelope field is not valid base64") from exc


def _aad(tenant_id: str, dek_version: int) -> bytes:
    """Additional authenticated data binding an envelope to its identity.

    Binding the tenant and DEK version into the AEAD authentication makes a
    ciphertext swapped between tenants (or between key generations) fail the
    integrity check instead of decrypting to plausible garbage.
    """
    payload = json.dumps(
        {"dek_version": dek_version, "env": "helix-envelope", "tenant_id": tenant_id},
        sort_keys=True,
        separators=(",", ":"),
    )
    return payload.encode("utf-8")


# ---------------------------------------------------------------------------
# KMS reference implementation (development stand-in for a cloud KMS)
# ---------------------------------------------------------------------------


class KeyManagementServiceProtocol(Protocol):
    def wrap(self, data_key: bytes) -> tuple[str, int]:
        """Encrypt ``data_key`` under the active KEK; return (wrapped, version)."""
        ...

    def unwrap(self, wrapped: str, kek_version: int) -> bytes:
        """Decrypt a wrapped key; refuses revoked versions (fail closed)."""
        ...

    def active_version(self) -> int:
        """Version of the newest non-revoked KEK."""
        ...

    def rotate(self) -> int:
        """Generate and activate the next KEK version."""
        ...

    def revoke(self, kek_version: int) -> None:
        """Revoke one KEK version; further unwraps through it fail closed."""
        ...

    def list_versions(self) -> list[dict[str, Any]]:
        ...


class DiskKeyManagementService:
    """Directory-backed versioned KEK store (the development KMS stand-in).

    Each KEK is one ``kek-v{N}.json`` file holding the raw key material plus
    lifecycle metadata. Rotation adds a file; revocation flips the status in
    place and refuses illegal transitions. Production swaps this class for a
    cloud-KMS adapter implementing the same protocol.
    """

    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise KmsUnavailableError(f"cannot open KEK directory {self.directory}: {exc}") from exc

    def _path(self, kek_version: int) -> Path:
        return self.directory / f"kek-v{kek_version:04d}.json"

    def _write(self, kek_version: int, record: dict[str, Any]) -> None:
        path = self._path(kek_version)
        try:
            path.write_text(
                json.dumps(record, sort_keys=True, separators=(",", ":")), encoding="utf-8"
            )
        except OSError as exc:
            raise KmsUnavailableError(f"cannot persist KEK v{kek_version}: {exc}") from exc

    def _read(self, kek_version: int) -> dict[str, Any]:
        path = self._path(kek_version)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise KmsUnavailableError(f"KEK v{kek_version} does not exist") from exc
        except OSError as exc:
            raise KmsUnavailableError(f"cannot read KEK v{kek_version}: {exc}") from exc
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise KmsUnavailableError(f"KEK v{kek_version} is corrupt") from exc

    def list_versions(self) -> list[dict[str, Any]]:
        records = []
        for path in sorted(self.directory.glob("kek-v*.json")):
            try:
                version = int(path.stem.split("-v")[1])
            except (IndexError, ValueError) as exc:
                raise KmsUnavailableError(f"unexpected KEK file {path.name}") from exc
            records.append(self._read(version))
        return records

    def generate_initial(self) -> int:
        """Create KEK v1 when the store is empty (idempotent bootstrap)."""
        if self.list_versions():
            return self.active_version()
        record = {
            "version": 1,
            "key": _b64encode(secrets.token_bytes(KEK_SIZE_BYTES)),
            "status": "active",
            "created_at": utc_now(),
        }
        self._write(1, record)
        logger.info("kms.kek_provisioned version=1")
        return 1

    def active_version(self) -> int:
        active = [r for r in self.list_versions() if r.get("status") == "active"]
        if not active:
            raise KmsUnavailableError("every KEK version is revoked")
        return max(int(r["version"]) for r in active)

    def _key_bytes(self, record: dict[str, Any], kek_version: int) -> bytes:
        key = _b64decode(str(record.get("key", "")))
        if len(key) != KEK_SIZE_BYTES:
            raise KmsUnavailableError(f"KEK v{kek_version} has unexpected key length")
        return key

    def wrap(self, data_key: bytes) -> tuple[str, int]:
        kek_version = self.active_version()
        record = self._read(kek_version)
        nonce = secrets.token_bytes(12)
        key = self._key_bytes(record, kek_version)
        wrapped = nonce + _aesgcm(key).encrypt(nonce, data_key, None)
        return _b64encode(wrapped), kek_version

    def unwrap(self, wrapped: str, kek_version: int) -> bytes:
        record = self._read(kek_version)
        if record.get("status") != "active":
            raise EnvelopeCryptoError(
                f"KEK v{kek_version} is {record.get('status')} and cannot unwrap key material "
                "(fail closed; re-wrap affected tenants onto an active KEK)",
                status_code=423,
            )
        raw = _b64decode(wrapped)
        if len(raw) <= 12:
            raise EnvelopeTamperError("wrapped key material is truncated")
        key = self._key_bytes(record, kek_version)
        try:
            return _aesgcm(key).decrypt(raw[:12], raw[12:], None)
        except Exception as exc:
            raise EnvelopeTamperError(f"KEK v{kek_version} could not authenticate wrapped DEK") from exc

    def rotate(self) -> int:
        versions = self.list_versions()
        next_version = max((int(r["version"]) for r in versions), default=0) + 1
        record = {
            "version": next_version,
            "key": _b64encode(secrets.token_bytes(KEK_SIZE_BYTES)),
            "status": "active",
            "created_at": utc_now(),
        }
        self._write(next_version, record)
        logger.info("kms.kek_rotated version=%d", next_version)
        return next_version

    def revoke(self, kek_version: int) -> None:
        record = self._read(kek_version)
        if record.get("status") == "revoked":
            raise EnvelopeCryptoError(f"KEK v{kek_version} is already revoked")
        record["status"] = "revoked"
        record["revoked_at"] = utc_now()
        self._write(kek_version, record)
        logger.warning("kms.kek_revoked version=%d", kek_version)


# ---------------------------------------------------------------------------
# Wrapped-DEK keystore (persists per-tenant DEKs in the tenant_deks table)
# ---------------------------------------------------------------------------


class DekKeystoreProtocol(Protocol):
    def list_versions(self, tenant_id: str) -> list[dict[str, Any]]:
        ...

    def save(
        self, tenant_id: str, dek_version: int, wrapped_dek: str, kek_version: int
    ) -> None:
        ...

    def replace_wrapped(
        self, tenant_id: str, dek_version: int, wrapped_dek: str, kek_version: int
    ) -> None:
        """Rewrite a stored DEK's wrapped material under a new KEK version."""
        ...

    def set_status(self, tenant_id: str, dek_version: int, status: str) -> None:
        ...


class EnvelopeCipherProtocol(Protocol):
    """The restricted-field encryption surface consumed by callers.

    ``encrypt_text``/``decrypt_text`` are the only operations webhook
    registration and delivery rely on; typing the dependency against this
    protocol (rather than ``Any``) keeps the envelope contract explicit.
    """

    def encrypt_text(self, tenant_id: str, plaintext: str) -> dict[str, Any]:
        ...

    def decrypt_text(self, tenant_id: str, envelope: dict[str, Any]) -> str:
        ...


class DatabaseDekKeystore:
    """``tenant_deks``-backed keystore (migration 38, shared by both backends)."""

    def __init__(self, database: Any) -> None:
        self.database = database

    def list_versions(self, tenant_id: str) -> list[dict[str, Any]]:
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT tenant_id, dek_version, wrapped_dek, kek_version, status,
                          created_at, rotated_at, revoked_at
                FROM tenant_deks WHERE tenant_id = ? ORDER BY dek_version""",
                (tenant_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def save(self, tenant_id: str, dek_version: int, wrapped_dek: str, kek_version: int) -> None:
        now = utc_now()
        with self.database.connect() as connection:
            connection.execute(
                """INSERT INTO tenant_deks
                (tenant_id, dek_version, wrapped_dek, kek_version, status, created_at)
                VALUES (?, ?, ?, ?, 'active', ?)""",
                (tenant_id, dek_version, wrapped_dek, kek_version, now),
            )

    def replace_wrapped(
        self, tenant_id: str, dek_version: int, wrapped_dek: str, kek_version: int
    ) -> None:
        """Rewrite a stored DEK's wrapped material under a new KEK version."""
        now = utc_now()
        with self.database.connect() as connection:
            existing = connection.execute(
                "SELECT status FROM tenant_deks WHERE tenant_id = ? AND dek_version = ?",
                (tenant_id, dek_version),
            ).fetchone()
            if existing is None:
                raise LookupError(f"DEK v{dek_version} for {tenant_id} does not exist")
            if str(existing["status"]) == "revoked":
                raise EnvelopeCryptoError(
                    f"DEK v{dek_version} for {tenant_id} is revoked (cannot re-wrap)"
                )
            connection.execute(
                """UPDATE tenant_deks
                SET wrapped_dek = ?, kek_version = ?, rotated_at = COALESCE(rotated_at, ?)
                WHERE tenant_id = ? AND dek_version = ?""",
                (wrapped_dek, kek_version, now, tenant_id, dek_version),
            )

    def set_status(self, tenant_id: str, dek_version: int, status: str) -> None:
        if status not in DEK_STATUSES:
            raise ValueError(f"unknown DEK status {status!r}")
        expected = _ALLOWED_DEK_TRANSITIONS.keys()
        now = utc_now()
        column = {"rotated": "rotated_at", "revoked": "revoked_at"}.get(status)
        with self.database.connect() as connection:
            row = connection.execute(
                "SELECT status FROM tenant_deks WHERE tenant_id = ? AND dek_version = ?",
                (tenant_id, dek_version),
            ).fetchone()
            if row is None:
                raise LookupError(f"DEK v{dek_version} for {tenant_id} does not exist")
            if status not in _ALLOWED_DEK_TRANSITIONS.get(str(row["status"]), set()):
                raise EnvelopeCryptoError(
                    f"DEK v{dek_version} for {tenant_id} cannot move "
                    f"{row['status']} -> {status} (allowed from: {sorted(expected)})"
                )
            connection.execute(
                f"""UPDATE tenant_deks SET status = ?{", " + column + " = ?" if column else ""}
                WHERE tenant_id = ? AND dek_version = ?""",
                (
                    (status, now, tenant_id, dek_version)
                    if column
                    else (status, tenant_id, dek_version)
                ),
            )


# ---------------------------------------------------------------------------
# The envelope cipher itself
# ---------------------------------------------------------------------------


class TenantEnvelopeCipher:
    """Encrypt/decrypt restricted payloads with per-tenant DEKs + KMS KEKs.

    Envelopes are JSON-safe dicts carrying their key versions and a copy of
    the wrapped DEK, so a restored backup (or a rebuilt ``tenant_deks``
    table) can recover data from the envelopes alone given the KEKs.
    """

    def __init__(
        self,
        kms: KeyManagementServiceProtocol,
        keystore: DekKeystoreProtocol,
        *,
        database: Any | None = None,
    ) -> None:
        self.kms = kms
        self.keystore = keystore
        self.database = database

    # -- DEK management ---------------------------------------------------

    def _audit(self, tenant_id: str, event_type: str, payload: dict[str, Any]) -> None:
        if self.database is None:
            return
        self.database.audit(tenant_id, None, "system:kms", event_type, payload)

    def active_dek(self, tenant_id: str) -> dict[str, Any]:
        """Return the tenant's active DEK row, provisioning one if needed.

        When the KMS has rotated its KEK since the DEK was last wrapped, the
        returned row is transparently re-wrapped under the active KEK first
        (lazy forward migration).  New ciphertext therefore always uses the
        newest KEK generation, while envelopes written under an older KEK keep
        decrypting through the wrapped-DEK copy they carry.

        Concurrent provisioning races are settled by the primary key: the
        loser re-reads and finds the winner's row instead of duplicating.
        """
        for _attempt in range(3):
            versions = self.keystore.list_versions(tenant_id)
            active = [r for r in versions if r["status"] == "active"]
            if active:
                row = active[-1]
                current_kek = self.kms.active_version()
                if int(row["kek_version"]) != current_kek:
                    dek = self.kms.unwrap(str(row["wrapped_dek"]), int(row["kek_version"]))
                    fresh_wrapped, fresh_kek = self.kms.wrap(dek)
                    if int(fresh_kek) != int(row["kek_version"]):
                        self.keystore.replace_wrapped(
                            tenant_id, int(row["dek_version"]), fresh_wrapped, fresh_kek
                        )
                    return {
                        **row,
                        "wrapped_dek": fresh_wrapped,
                        "kek_version": str(fresh_kek),
                    }
                return row
            next_version = max((int(r["dek_version"]) for r in versions), default=0) + 1
            wrapped, kek_version = self.kms.wrap(secrets.token_bytes(DEK_SIZE_BYTES))
            try:
                self.keystore.save(tenant_id, next_version, wrapped, kek_version)
            except sqlite3.IntegrityError:
                continue  # concurrent provisioner won; re-read above
            self._audit(
                tenant_id,
                "security.dek.provisioned",
                {"dek_version": next_version, "kek_version": kek_version},
            )
            return self.keystore.list_versions(tenant_id)[-1]
        raise EnvelopeCryptoError(
            f"could not provision a tenant DEK for {tenant_id} under concurrency",
            status_code=409,
        )

    def rotate_tenant_dek(self, tenant_id: str, *, actor: str = "system:kms") -> int:
        """Mark the current DEK rotated and provision its replacement."""
        current = self.active_dek(tenant_id)
        current_version = int(current["dek_version"])
        self.keystore.set_status(tenant_id, current_version, "rotated")
        following = self.active_dek(tenant_id)
        new_version = int(following["dek_version"])
        self._audit(
            tenant_id,
            "security.dek.rotated",
            {"from_version": current_version, "to_version": new_version},
        )
        return new_version

    def rewrap_tenant_deks(self, tenant_id: str, *, actor: str = "system:kms") -> dict[str, int]:
        """Re-wrap every tenant DEK not already on the active KEK.

        Online operation: only the wrapped key material changes — no
        ciphertext anywhere is touched, and decryption stays available
        throughout (rows are rewritten one transaction at a time). Rows already
        wrapped under the active KEK are skipped, so a repeated sweep converges
        to zero writes and zero audit noise.
        """
        active_kek = self.kms.active_version()
        rewrapped = 0
        for row in self.keystore.list_versions(tenant_id):
            if row["status"] == "revoked":
                continue
            if int(row["kek_version"]) == active_kek:
                continue
            dek = self.kms.unwrap(str(row["wrapped_dek"]), int(row["kek_version"]))
            fresh_wrapped, fresh_kek = self.kms.wrap(dek)
            self.keystore.replace_wrapped(tenant_id, int(row["dek_version"]), fresh_wrapped, fresh_kek)
            rewrapped += 1
        if rewrapped:
            self._audit(tenant_id, "security.dek.rewrapped", {"count": rewrapped})
        return {"rewrapped": rewrapped}

    def revoke_kek_version(self, kek_version: int, *, actor: str = "system:kms") -> None:
        self.kms.revoke(kek_version)
        self._audit("", "security.kek.revoked", {"kek_version": kek_version})

    # -- payload encryption -------------------------------------------------

    def encrypt_text(self, tenant_id: str, plaintext: str) -> dict[str, Any]:
        """Encrypt ``plaintext`` under the tenant's active DEK."""
        dek_row = self.active_dek(tenant_id)
        dek_version = int(dek_row["dek_version"])
        dek = self.kms.unwrap(str(dek_row["wrapped_dek"]), int(dek_row["kek_version"]))
        nonce = secrets.token_bytes(12)
        ciphertext = _aesgcm(dek).encrypt(nonce, plaintext.encode("utf-8"), _aad(tenant_id, dek_version))
        return {
            "v": ENVELOPE_VERSION,
            "tenant_id": tenant_id,
            "dek_version": dek_version,
            "kek_version": int(dek_row["kek_version"]),
            "wrapped_dek": str(dek_row["wrapped_dek"]),
            "nonce": _b64encode(nonce),
            "ciphertext": _b64encode(ciphertext),
        }

    def decrypt_text(self, tenant_id: str, envelope: dict[str, Any]) -> str:
        """Decrypt an envelope; refuses cross-tenant use and any tampering.

        The unwrap always uses the envelope's own wrapped-DEK copy and key
        versions, never the keystore's current material: a lazy forward
        re-wrap may have migrated the stored DEK onto a newer KEK, but the
        historical KEK still authenticates the copy the envelope carries. Any
        forged copy fails the AEAD integrity check below.
        """
        if not isinstance(envelope, dict) or envelope.get("v") != ENVELOPE_VERSION:
            raise EnvelopeTamperError("unsupported or malformed envelope version")
        if envelope.get("tenant_id") != tenant_id:
            raise EnvelopeTamperError("envelope belongs to a different tenant")
        dek_version = int(envelope["dek_version"])
        stored = next(
            (
                r
                for r in self.keystore.list_versions(tenant_id)
                if int(r["dek_version"]) == dek_version
            ),
            None,
        )
        if stored and stored["status"] == "revoked":
            raise EnvelopeCryptoError(
                f"DEK v{dek_version} for {tenant_id} is revoked (fail closed)", status_code=423
            )
        dek = self.kms.unwrap(str(envelope["wrapped_dek"]), int(envelope["kek_version"]))
        try:
            plain = _aesgcm(dek).decrypt(
                _b64decode(str(envelope["nonce"])),
                _b64decode(str(envelope["ciphertext"])),
                _aad(tenant_id, dek_version),
            )
        except Exception as exc:
            raise EnvelopeTamperError("AEAD authentication failed") from exc
        return plain.decode("utf-8")


# ---------------------------------------------------------------------------
# Data-classification gate for search/index fields (bullet 3 of 43.2)
# ---------------------------------------------------------------------------


def ensure_searchable_fields_are_classified(field_names: list[str]) -> dict[str, str]:
    """No field may become searchable/indexable before it is classified.

    Fields absent from ``app.redaction.FIELD_REGISTRY`` raise instead of
    silently defaulting, so search features cannot downgrade the encryption /
    redaction posture of an unclassified field.
    """
    from app.redaction import FIELD_REGISTRY, classify_field

    unclassified = [name for name in field_names if name not in FIELD_REGISTRY]
    if unclassified:
        raise ValueError(
            "searchable fields must be classified in FIELD_REGISTRY first: "
            f"{sorted(unclassified)}"
        )
    return {name: classify_field(name) for name in field_names}


__all__ = [
    "DEK_SIZE_BYTES",
    "DatabaseDekKeystore",
    "DekKeystoreProtocol",
    "DiskKeyManagementService",
    "ENVELOPE_VERSION",
    "EnvelopeCipherProtocol",
    "EnvelopeCryptoError",
    "EnvelopeTamperError",
    "KeyManagementServiceProtocol",
    "KmsUnavailableError",
    "TenantEnvelopeCipher",
    "ensure_searchable_fields_are_classified",
]
